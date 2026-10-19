# immich-overflight-feed

Serves `/wallpapers.json` in the schema [Overflight](https://github.com/hitorunajp/Overflight)
(a Projectivy Launcher plugin) expects, so an Android TV / Nvidia Shield home
screen background rotates through a shared Immich album.

Docker Hub: [`bdelima/immich-overflight-feed`](https://hub.docker.com/r/bdelima/immich-overflight-feed)

See the repo-root README's [Setup](../README.md#setup) section for getting an Immich API key, finding `ALBUM_ID`, and creating a shared link for `SHARE_SLUG`/`SHARE_KEY` if you haven't already.

## How it works

- Lists the album's assets via `POST /api/search/metadata` with an
  `albumIds` filter, authenticated with a personal Immich API key.
  (`GET /api/albums/{id}` does not return a usable `assets` array in current
  Immich versions, under any auth.)
- Keeps a copy of each photo's **original, at full resolution**, in a local
  folder (`CACHE_DIR`), and builds each `url_img` (or `url_1080p` for videos)
  link to point at that copy (`/img/...` on this service). Each photo is read
  from Immich **once**, as soon as it joins the album; after that the TV is
  served from the local copy. Without this, every time the TV moved to the
  next picture Immich opened the original file again, which keeps the disks
  under Immich's library spinning if they are hard drives.
- Downloads use Immich's shared-link auth (`SHARE_SLUG` or `SHARE_KEY`),
  since personal API keys 403 on assets contributed by other album members,
  while shared-link auth is scoped to the whole share regardless of who added
  what.
- Checks the album every `CACHE_SECONDS` (that is a database query in Immich,
  not a read of the photo files), caches anything new, and removes the copy of
  a photo a day after it leaves the album. A photo whose original is replaced
  in Immich is fetched again.
- If `CACHE_DIR` can't be written to (or is set to an empty value), the cache
  is off and the links point straight at Immich, as in earlier versions.

The cache holds one copy of every original in the album, so size the volume to
the album (for example, 500 photos of about 8 MB each is roughly 4 GB).

## Configuration

| Variable | Required | Description |
|---|---|---|
| `IMMICH_INTERNAL_URL` | yes | Immich URL reachable from this container (e.g. `http://immich-server:2283` on the same Docker network). Used for the album listing and for downloading originals. |
| `IMMICH_PUBLIC_URL` | yes | Immich URL reachable from the *device displaying the wallpapers* (e.g. `https://immich.example.com`). Only used when the image cache is off. |
| `ALBUM_ID` | yes | UUID of the Immich album to serve. |
| `IMMICH_API_KEY` | yes, unless set via `SECRETS_FILE` | Personal Immich API key, used only for listing. |
| `SECRETS_FILE` | no (default `/run/secrets/immich_secrets.env`) | Path to a shared `KEY=VALUE` secrets file. An `IMMICH_API_KEY=` line in it wins over the env var above — this is the same file [immich-photo-pipeline](https://github.com/bdelima/immich-photo-pipeline) uses; this service only ever reads that one line out of it. |
| `SHARE_SLUG` or `SHARE_KEY` | one required | Immich shared-link auth, used to download the originals. |
| `CACHE_DIR` | no (default `/data/images`) | Where the originals are kept. Mount a volume here on a local disk (not the disks behind Immich). Set to an empty value to turn the cache off. |
| `FEED_PUBLIC_URL` | no | Where the TV reaches *this* service (e.g. `https://feed.example.com`), used to build the image links. When unset, links use the address the TV used to fetch `/wallpapers.json`. |
| `CACHE_SECONDS` | no (default `300`) | How often the album listing is refreshed (and new photos cached). |
| `REQUEST_TIMEOUT` | no (default `10`) | Timeout in seconds for calls to Immich. |
| `DOWNLOAD_TIMEOUT` | no (default `120`) | Seconds without data before a download from Immich is given up. |
| `PORT` | no (default `8080`) | Port the Flask app listens on inside the container (when run without the Dockerfile's gunicorn). |

## Endpoints

- `GET /wallpapers.json` — the Overflight-formatted feed.
- `GET /img/<name>` — a cached original, unchanged; supports range requests.
- `GET /health` — liveness check, returns `200 ok`.
- `GET /version` — the running version.

## Running

```bash
docker run -d \
  -p 8080:8080 \
  -v ./overflight-feed-data:/data/images \
  -e IMMICH_INTERNAL_URL=http://immich-server:2283 \
  -e IMMICH_PUBLIC_URL=https://immich.example.com \
  -e ALBUM_ID=your-album-uuid \
  -e IMMICH_API_KEY=your-api-key \
  -e SHARE_SLUG=your-share-slug \
  bdelima/immich-overflight-feed:latest
```

See the repo-root `docker-compose.example.yml` for running both services
alongside an existing Immich stack.

## Tests

`pip install -r requirements.txt pytest` and then `pytest overflight-feed`
from the repo root. They run against a small fake Immich on a local port.
