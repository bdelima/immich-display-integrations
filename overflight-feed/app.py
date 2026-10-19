import fcntl
import hashlib
import mimetypes
import os
import re
import threading
import time
import logging

import requests
from flask import Flask, jsonify, Response, abort, request, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("immich-overflight-feed")

APP_VERSION = os.environ.get("APP_VERSION", "unknown")

IMMICH_INTERNAL_URL = os.environ.get("IMMICH_INTERNAL_URL", os.environ.get("IMMICH_URL", "")).rstrip("/")
# Used to query the album and to download the originals - when this feed
# lives in the same compose project as immich-server, point this at
# http://immich-server:2283 so the calls happen over the internal Docker
# network, with no dependency on NPM or external DNS being up.

IMMICH_PUBLIC_URL = os.environ.get("IMMICH_PUBLIC_URL", os.environ.get("IMMICH_URL", "")).rstrip("/")
# Only used when the image cache below is turned off or can't be written to:
# then the url_img links in the JSON point straight at Immich, which must be
# reachable from the Shield (e.g. https://immich.pumapants.cc).

ALBUM_ID = os.environ["ALBUM_ID"]                             # album uuid


def _resolve_immich_api_key():
    """Reads IMMICH_API_KEY= from the shared secrets file this whole
    Immich pipeline/display-integrations ecosystem uses (plain KEY=VALUE
    lines, one per credential -- see immich-photo-pipeline's
    app/secrets.py for the canonical version of this parsing, since
    there's no shared package to import across these two repos). This
    service only ever reads the IMMICH_API_KEY= line and leaves every
    other key in the file (CLAUDE_CODE_OAUTH_TOKEN, IMMICH_EXTRA_API_KEY,
    ...) strictly alone -- those belong to immich-photo-pipeline. The
    file's value wins over the IMMICH_API_KEY env var, so rotating the
    key is "edit that one line", never "edit compose and restart"."""
    secrets_file = os.environ.get("SECRETS_FILE", "/run/secrets/immich_secrets.env")
    if secrets_file and os.path.isfile(secrets_file):
        try:
            with open(secrets_file, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            log.warning("could not read %s: %s", secrets_file, exc)
            lines = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "IMMICH_API_KEY" and value.strip():
                return value.strip()
    env_key = os.environ.get("IMMICH_API_KEY", "").strip()
    if env_key:
        return env_key
    raise RuntimeError(
        f"no Immich API key found: set IMMICH_API_KEY or add an "
        f"IMMICH_API_KEY=... line to {secrets_file} (SECRETS_FILE)"
    )


# The internal listing call uses a personal API key, not the shared-link
# auth - Immich withholds the per-asset array from shared-link-authenticated
# requests (confirmed empirically: /api/albums/{id}, /api/shared-links/me
# all return assetCount but never the assets array when auth'd via
# slug/key). A real API key gets the full, unrestricted response instead.
IMMICH_API_KEY = _resolve_immich_api_key()

# The shared-link auth is still needed to download the originals, since a
# personal API key 403s on assets another household member added.
SHARE_SLUG = os.environ.get("SHARE_SLUG", "")
SHARE_KEY = os.environ.get("SHARE_KEY", "")

if not SHARE_SLUG and not SHARE_KEY:
    raise RuntimeError("Set either SHARE_SLUG or SHARE_KEY")

_AUTH_PARAM = {"slug": SHARE_SLUG} if SHARE_SLUG else {"key": SHARE_KEY}

CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))  # how long to cache Immich's album response
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
REFRESH_SECONDS = 10    # the album listing is not asked for again sooner than this because a photo's name was not in it
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "120"))  # seconds without data while downloading an original

# Where the originals are kept. Each photo is read from Immich once, the first
# time it is seen, and every later request for it is served from here, so the
# disks behind Immich are not read again each time the TV moves to the next
# picture. Originals are stored exactly as Immich has them (no resizing).
# Point this at a volume on a local disk. Set it to an empty string to turn the
# cache off (the links then go straight to Immich, as before).
CACHE_DIR = os.environ.get("CACHE_DIR", "/data/images")
# Where the TV reaches this service (e.g. https://feed.example.com). Optional:
# when unset, links are built from the address the TV used to fetch the feed.
FEED_PUBLIC_URL = os.environ.get("FEED_PUBLIC_URL", "").rstrip("/")

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")
mimetypes.add_type("video/quicktime", ".mov")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_cache = {"body": None, "fetched_at": 0}
_cache_lock = threading.Lock()
_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[0-9a-f]{10}\.[a-z0-9]{1,5}$")
_STALE_SECONDS = 24 * 3600     # a cached file is kept this long after it is first missing from the album


def _cache_ready():
    """True when the cache directory exists and can be written to."""
    if not CACHE_DIR:
        return False
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        probe = os.path.join(CACHE_DIR, f".probe-{os.getpid()}")
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except OSError as exc:
        log.warning("image cache %s is not usable (%s); links will point at Immich", CACHE_DIR, exc)
        return False


CACHE_ON = _cache_ready()


def _cache_name(asset):
    """File name for one photo: its id, a short digest of Immich's checksum (so a
    replaced original is fetched again) and the original's extension."""
    digest = hashlib.sha1(str(asset.get("checksum") or "").encode()).hexdigest()[:10]
    ext = os.path.splitext(asset.get("originalFileName") or "")[1].lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,5}", ext):
        ext = "bin"
    return f"{asset['id']}-{digest}.{ext}"


def fetch_wallpapers():
    """Fetch the album's assets via search/metadata. Returns a list of
    {"id", "title", "video", "name"} -- the links are built per request, since
    they depend on how the TV reached us. GET /api/albums/{id} doesn't return a
    usable assets array in this Immich version (confirmed empirically, with and
    without a shared-link auth, with and without withoutAssets=false) - the
    search endpoint is what actually returns full per-asset details."""
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
            if not asset.get("id"):
                continue
            wallpapers.append({
                "id": asset["id"],
                "title": asset.get("originalFileName", ""),
                "video": asset.get("type") == "VIDEO",
                "name": _cache_name(asset),
            })

        next_page = assets_block.get("nextPage")
        if not next_page:
            break
        page = next_page

    log.info("Fetched %d assets from album %s", len(wallpapers), ALBUM_ID)
    return wallpapers


def _download(entry):
    """Makes sure the original of `entry` is in the cache and returns its path.
    One download at a time per photo, even across the server's worker
    processes (a lock file), so a photo is read from Immich only once."""
    path = os.path.join(CACHE_DIR, entry["name"])
    if os.path.isfile(path):
        return path
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if os.path.isfile(path):          # another worker finished it while we waited
                return path
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
            try:
                with requests.get(
                    f"{IMMICH_INTERNAL_URL}/api/assets/{entry['id']}/original",
                    params=_AUTH_PARAM, stream=True, timeout=(REQUEST_TIMEOUT, DOWNLOAD_TIMEOUT),
                ) as resp:
                    resp.raise_for_status()
                    expected = resp.headers.get("Content-Length")
                    written = 0
                    with open(tmp, "wb") as out:
                        for chunk in resp.iter_content(1 << 20):
                            out.write(chunk)
                            written += len(chunk)
                if expected is not None and written != int(expected):
                    raise requests.RequestException(f"short download: {written} of {expected} bytes")
                os.replace(tmp, path)
                log.info("cached %s (%d bytes)", entry["name"], written)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return path


def _warm(wallpapers):
    """Downloads every photo in the list that isn't cached yet, so the TV never
    waits for one. The disks behind Immich are read here, once per new photo."""
    for entry in wallpapers:
        try:
            _download(entry)
        except (requests.RequestException, OSError) as exc:
            log.error("could not cache %s: %s", entry["name"], exc)


_FILE = re.compile(r"^(?P<base>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[0-9a-f]{10}\.[a-z0-9]{1,5})(?P<rest>.*)$")


def _prune(wallpapers):
    """Removes the cached copy of a photo a day after it left the album. The day is counted from
    when the photo was first missing from the album listing: that moment is written down as a
    small `<name>.gone` file next to the copy (so it survives a restart and is shared by the
    server's workers), and the file is taken away again if the photo comes back. Skipped when the
    album listing is empty, so a bad answer from Immich can't empty the cache."""
    if not wallpapers:
        return
    keep = {w["name"] for w in wallpapers}
    now = time.time()
    groups = {}         # cached name -> every file that belongs to it (the copy, its lock, a marker, a partial download)
    for name in os.listdir(CACHE_DIR):
        m = _FILE.match(name)
        if m:
            groups.setdefault(m.group("base"), []).append(name)
    for base, names in groups.items():
        marker = os.path.join(CACHE_DIR, base + ".gone")
        try:
            if base in keep:
                if base + ".gone" in names:
                    os.remove(marker)
                continue
            if base + ".gone" not in names:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)       # first time it is missing
                os.close(fd)
                continue
            if now - os.path.getmtime(marker) > _STALE_SECONDS:
                for name in names:
                    os.remove(os.path.join(CACHE_DIR, name))
                log.info("removed %s (left the album more than a day ago)", base)
        except FileExistsError:
            pass                            # another worker wrote the marker first
        except OSError:
            pass


def get_wallpapers_cached(max_age=None):
    """The album listing, asked of Immich again when what we hold is older than `max_age`
    seconds (CACHE_SECONDS by default)."""
    max_age = CACHE_SECONDS if max_age is None else max_age
    now = time.time()
    with _cache_lock:
        if _cache["body"] is not None and (now - _cache["fetched_at"]) <= max_age:
            return _cache["body"]
        try:
            _cache["body"] = fetch_wallpapers()
            _cache["fetched_at"] = now
        except requests.RequestException as exc:
            log.error("Failed to fetch album from Immich: %s", exc)
            if _cache["body"] is None:
                raise
            # serve the stale cache rather than nothing if Immich is briefly unreachable
        return _cache["body"]


def _background():
    """Keeps the album listing fresh and the originals cached, whether or not
    the TV is asking, so a new photo is read from Immich as soon as it joins the
    album and not at the moment the TV first shows it."""
    while True:
        try:
            wallpapers = get_wallpapers_cached()
            _warm(wallpapers)
            _prune(wallpapers)
        except Exception as exc:       # keep the thread alive whatever Immich does
            log.error("background refresh failed: %s", exc)
        time.sleep(max(30, CACHE_SECONDS))


def _base_url():
    return FEED_PUBLIC_URL or request.url_root.rstrip("/")


@app.route("/wallpapers.json")
def wallpapers_json():
    try:
        wallpapers = get_wallpapers_cached()
    except requests.RequestException:
        return Response('{"error": "immich unreachable"}', status=502, mimetype="application/json")
    auth_qs = "&".join(f"{k}={v}" for k, v in _AUTH_PARAM.items())
    out = []
    for w in wallpapers:
        if CACHE_ON:
            url = f"{_base_url()}/img/{w['name']}"
        else:
            url = f"{IMMICH_PUBLIC_URL}/api/assets/{w['id']}/original?{auth_qs}"
        # Overflight expects videos under url_1080p/url_4k/etc, not url_img - the
        # file is served as-is regardless of type, so the same URL works, just
        # filed differently. Using url_1080p as a reasonable default since we
        # don't know the actual resolution/HDR status without deeper probing.
        out.append({"title": w["title"], ("url_1080p" if w["video"] else "url_img"): url})
    return jsonify(out)


@app.route("/img/<name>")
def image(name):
    """An original from the cache (downloaded now if it isn't there yet and it is
    in the album). Range requests work, for videos."""
    if not CACHE_ON or not _NAME.match(name):
        abort(404)
    path = os.path.join(CACHE_DIR, name)
    if not os.path.isfile(path):
        # Each worker keeps its own copy of the album listing, so one that has not looked yet (just
        # started) or looked a while ago may not know a photo the other one has already listed. Ask
        # again before saying no, but not more than once every REFRESH_SECONDS, so a made-up name
        # can't make the feed query Immich over and over.
        try:
            entry = next((w for w in get_wallpapers_cached() if w["name"] == name), None)
            if entry is None:
                entry = next((w for w in get_wallpapers_cached(max_age=REFRESH_SECONDS) if w["name"] == name), None)
        except requests.RequestException:
            abort(502)
        if entry is None:
            abort(404)
        try:
            path = _download(entry)
        except (requests.RequestException, OSError) as exc:
            log.error("could not fetch %s for the TV: %s", name, exc)
            abort(502)
    response = send_file(path, mimetype=mimetypes.guess_type(name)[0] or "application/octet-stream", conditional=True)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@app.route("/health")
def health():
    return "ok", 200


@app.route("/version")
def version():
    return jsonify({"version": APP_VERSION})


if CACHE_ON and os.environ.get("DISABLE_BACKGROUND") != "1":
    threading.Thread(target=_background, daemon=True, name="album-refresh").start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
