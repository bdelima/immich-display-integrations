import os
import time
import logging

import requests
from flask import Flask, jsonify, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("immich-overflight-feed")

IMMICH_INTERNAL_URL = os.environ.get("IMMICH_INTERNAL_URL", os.environ.get("IMMICH_URL", "")).rstrip("/")
# Used to query the album - when this feed lives in the same compose project
# as immich-server, point this at http://immich-server:2283 so the lookup
# happens over the internal Docker network, with no dependency on NPM or
# external DNS being up.

IMMICH_PUBLIC_URL = os.environ.get("IMMICH_PUBLIC_URL", os.environ.get("IMMICH_URL", "")).rstrip("/")
# Used to build the url_img links embedded in the JSON. This one MUST be
# reachable from the Shield (e.g. https://immich.pumapants.cc), since
# Overflight fetches these images directly over your LAN/internet, not
# from inside the Docker network.

ALBUM_ID = os.environ["ALBUM_ID"]                             # album uuid

# The internal listing call uses a personal API key, not the shared-link
# auth - Immich withholds the per-asset array from shared-link-authenticated
# requests (confirmed empirically: /api/albums/{id}, /api/shared-links/me
# all return assetCount but never the assets array when auth'd via
# slug/key). A real API key gets the full, unrestricted response instead.
IMMICH_API_KEY = os.environ["IMMICH_API_KEY"]

# The shared-link auth is still needed for the url_img links themselves,
# since that's what makes them fetchable unauthenticated from the Shield.
SHARE_SLUG = os.environ.get("SHARE_SLUG", "")
SHARE_KEY = os.environ.get("SHARE_KEY", "")

if not SHARE_SLUG and not SHARE_KEY:
    raise RuntimeError("Set either SHARE_SLUG or SHARE_KEY")

_AUTH_PARAM = {"slug": SHARE_SLUG} if SHARE_SLUG else {"key": SHARE_KEY}

CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))  # how long to cache Immich's album response
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))

app = Flask(__name__)

_cache = {"body": None, "fetched_at": 0}


def fetch_wallpapers():
    """Fetch the album's assets via search/metadata and shape them into
    Overflight's JSON schema. GET /api/albums/{id} doesn't return a usable
    assets array in this Immich version (confirmed empirically, with and
    without a shared-link auth, with and without withoutAssets=false) - the
    search endpoint is what actually returns full per-asset details."""
    auth_qs = "&".join(f"{k}={v}" for k, v in _AUTH_PARAM.items())

    wallpapers = []
    page = 1
    while True:
        resp = requests.post(
            f"{IMMICH_INTERNAL_URL}/api/search/metadata",
            headers={"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json"},
            json={"albumIds": [ALBUM_ID], "page": page},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        assets_block = data.get("assets", {})

        for asset in assets_block.get("items", []):
            asset_id = asset.get("id")
            if not asset_id:
                continue

            asset_url = f"{IMMICH_PUBLIC_URL}/api/assets/{asset_id}/original?{auth_qs}"
            entry = {"title": asset.get("originalFileName", "")}

            if asset.get("type") == "VIDEO":
                # Overflight expects videos under url_1080p/url_4k/etc, not
                # url_img - Immich's /original endpoint serves the file as-is
                # regardless of type, so the same URL works, just filed
                # differently. Using url_1080p as a reasonable default since
                # we don't know the actual resolution/HDR status without
                # deeper probing of the asset.
                entry["url_1080p"] = asset_url
            else:
                entry["url_img"] = asset_url

            wallpapers.append(entry)

        next_page = assets_block.get("nextPage")
        if not next_page:
            break
        page = next_page

    log.info("Fetched %d assets from album %s", len(wallpapers), ALBUM_ID)
    return wallpapers


def get_wallpapers_cached():
    now = time.time()
    if _cache["body"] is None or (now - _cache["fetched_at"]) > CACHE_SECONDS:
        try:
            _cache["body"] = fetch_wallpapers()
            _cache["fetched_at"] = now
        except requests.RequestException as exc:
            log.error("Failed to fetch album from Immich: %s", exc)
            if _cache["body"] is None:
                raise
            # serve the stale cache rather than nothing if Immich is briefly unreachable
    return _cache["body"]


@app.route("/wallpapers.json")
def wallpapers_json():
    try:
        data = get_wallpapers_cached()
    except requests.RequestException:
        return Response('{"error": "immich unreachable"}', status=502, mimetype="application/json")
    return jsonify(data)


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
