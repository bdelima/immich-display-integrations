import os
import time
import json
import logging

import requests
from samsungtvws import SamsungTVWS
from wakeonlan import send_magic_packet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("immich-frame-mirror")

APP_VERSION = os.environ.get("APP_VERSION", "unknown")

IMMICH_URL = os.environ["IMMICH_INTERNAL_URL"].rstrip("/")  # internal URL, e.g. http://immich-server:2283
IMMICH_API_KEY = os.environ["IMMICH_API_KEY"]
ALBUM_ID = os.environ["ALBUM_ID"]

FRAME_IP = os.environ["FRAME_IP"]
FRAME_MAC = os.environ["FRAME_MAC"].replace("-", ":")  # normalize 7C-0A-... to 7C:0A:...
MATTE = os.environ.get("MATTE", "none")

# Used only for downloading the actual file bytes, not for listing (the API
# key + search/metadata handles listing fine across every contributor).
# Personal API keys are scoped to assets YOU own - direct /original access
# 403s on anything contributed by another album member. Shared-link auth has
# no such restriction, since it's designed to grant access to the whole
# share regardless of who added what.
SHARE_SLUG = os.environ.get("SHARE_SLUG", "")
SHARE_KEY = os.environ.get("SHARE_KEY", "")
if not SHARE_SLUG and not SHARE_KEY:
    raise RuntimeError("Set either SHARE_SLUG or SHARE_KEY")
_SHARE_AUTH_PARAM = {"slug": SHARE_SLUG} if SHARE_SLUG else {"key": SHARE_KEY}

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "3600"))  # hourly by default
SHORT_RETRY_SECONDS = int(os.environ.get("SHORT_RETRY_SECONDS", "300"))  # retry sooner after an early abort
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
CONNECT_RETRIES = int(os.environ.get("CONNECT_RETRIES", "15"))
CONNECT_RETRY_DELAY_SECONDS = int(os.environ.get("CONNECT_RETRY_DELAY_SECONDS", "60"))
# 15 retries x 60s = 15 minutes total budget - observed wake time on this TV
# was ~10 minutes from a full power-off, over wired Ethernet. Resending the
# WoL packet every few attempts adds redundancy in case the first UDP
# broadcast gets dropped.
RESEND_WOL_EVERY_N_ATTEMPTS = int(os.environ.get("RESEND_WOL_EVERY_N_ATTEMPTS", "3"))

# Both live under /data, which should be a bind mount so they survive
# container recreation. token.txt holds the one-time pairing approval;
# without it persisting, every restart would re-prompt "Allow connection?"
# on the TV itself.
DATA_DIR = "/data"
STATE_FILE = os.path.join(DATA_DIR, "state.json")
TOKEN_FILE = os.path.join(DATA_DIR, "token.txt")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def wake_and_connect():
    """Send a WoL magic packet, wait for the TV to come up, then connect
    with retries - observed wake time on this TV over wired Ethernet was
    around 10 minutes from a full power-off, so the retry budget here is
    generous (15 minutes) rather than the couple of minutes that would
    cover a typical device."""
    log.info("Sending Wake-on-LAN to %s", FRAME_MAC)
    send_magic_packet(FRAME_MAC)

    last_exc = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            tv = SamsungTVWS(host=FRAME_IP, port=8002, token_file=TOKEN_FILE)
            tv_art = tv.art()
            tv_art.supported()  # cheap call to confirm the connection actually works
            return tv_art
        except Exception as exc:
            last_exc = exc
            log.warning(
                "Connection attempt %d/%d failed: %s", attempt, CONNECT_RETRIES, exc
            )
            if attempt % RESEND_WOL_EVERY_N_ATTEMPTS == 0:
                log.info("Resending Wake-on-LAN to %s", FRAME_MAC)
                send_magic_packet(FRAME_MAC)
            time.sleep(CONNECT_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"Could not connect to Frame TV after {CONNECT_RETRIES} attempts") from last_exc


def fetch_immich_assets():
    """Return {asset_id: {"filename": ...}} for every IMAGE in the album.
    Videos are skipped - Frame Art Mode only displays still images."""
    assets = {}
    page = 1
    while True:
        resp = requests.post(
            f"{IMMICH_URL}/api/search/metadata",
            headers={"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json"},
            json={"albumIds": [ALBUM_ID], "page": page},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        block = data.get("assets", {})

        for asset in block.get("items", []):
            if asset.get("type") != "IMAGE":
                continue
            asset_id = asset.get("id")
            if not asset_id:
                continue
            assets[asset_id] = {"filename": asset.get("originalFileName", "")}

        next_page = block.get("nextPage")
        if not next_page:
            break
        page = next_page

    return assets


def download_asset(asset_id):
    resp = requests.get(
        f"{IMMICH_URL}/api/assets/{asset_id}/original",
        params=_SHARE_AUTH_PARAM,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


def compute_diff(state):
    """Check Immich for changes without touching the TV at all."""
    immich_assets = fetch_immich_assets()
    known_ids = set(state.keys())
    current_ids = set(immich_assets.keys())
    to_add = current_ids - known_ids
    to_remove = known_ids - current_ids
    return immich_assets, to_add, to_remove


def sync_once(get_tv_art, tv_art, state, immich_assets, to_add, to_remove):
    log.info(
        "Immich album has %d images, TV currently tracks %d, adding %d, removing %d",
        len(immich_assets), len(state), len(to_add), len(to_remove),
    )

    # Only ever remove content_ids THIS script uploaded (tracked in state) -
    # never touches anything added manually via the Samsung app, purchased
    # art, or anything else already on the TV.
    remove_content_ids = [state[aid]["content_id"] for aid in to_remove if "content_id" in state[aid]]
    if remove_content_ids:
        try:
            tv_art.delete_list(remove_content_ids)
            log.info("Deleted %d image(s) from the TV", len(remove_content_ids))
        except Exception as exc:
            log.error("Failed to delete some images from the TV: %s", exc)
            # Don't drop these from state if deletion failed - we'll retry
            # next cycle instead of losing track of them.
            to_remove -= set(
                aid for aid in to_remove
                if state.get(aid, {}).get("content_id") in remove_content_ids
            )

    for aid in to_remove:
        state.pop(aid, None)

    for aid in to_add:
        info = immich_assets[aid]
        filename = info["filename"]
        try:
            file_bytes = download_asset(aid)
            file_type = "JPEG" if filename.lower().endswith((".jpg", ".jpeg")) else "PNG"
            content_id = tv_art.upload(file_bytes, matte=MATTE, file_type=file_type)
            state[aid] = {"content_id": content_id, "filename": filename}
            log.info("Uploaded %s -> %s", filename, content_id)
        except Exception as exc:
            log.warning(
                "Upload of %s failed (%s) - TV may have gone back to sleep mid-batch, "
                "reconnecting and retrying once",
                filename, exc,
            )
            try:
                tv_art = get_tv_art()  # fresh WoL + reconnect, replaces the stale connection
            except Exception as reconnect_exc:
                log.error(
                    "Reconnect failed (%s) - TV appears to be genuinely offline. "
                    "Stopping this cycle early rather than repeating a slow reconnect "
                    "attempt for every remaining photo; will retry sooner than the "
                    "normal schedule.",
                    reconnect_exc,
                )
                save_state(state)
                return tv_art, False

            try:
                content_id = tv_art.upload(file_bytes, matte=MATTE, file_type=file_type)
                state[aid] = {"content_id": content_id, "filename": filename}
                log.info("Uploaded %s -> %s (after reconnect)", filename, content_id)
            except Exception as exc2:
                log.error("Failed to upload %s even after reconnecting: %s", filename, exc2)
                # Left out of state - will be retried again next full cycle.

        save_state(state)  # save incrementally so partial progress survives a mid-batch crash

    return tv_art, True


def main():
    log.info("immich-frame-mirror v%s starting", APP_VERSION)
    os.makedirs(DATA_DIR, exist_ok=True)
    state = load_state()

    while True:
        completed = True
        try:
            immich_assets, to_add, to_remove = compute_diff(state)
            if not to_add and not to_remove:
                log.info("No changes in Immich album (%d images) - skipping TV wake", len(immich_assets))
            else:
                tv_art = wake_and_connect()
                tv_art, completed = sync_once(wake_and_connect, tv_art, state, immich_assets, to_add, to_remove)
        except Exception as exc:
            log.error("Sync cycle failed: %s", exc)

        wait_seconds = CHECK_INTERVAL_SECONDS if completed else SHORT_RETRY_SECONDS
        if not completed:
            log.info("Retrying sooner (in %ds) since this cycle ended early", wait_seconds)
        time.sleep(wait_seconds)


if __name__ == "__main__":
    main()
