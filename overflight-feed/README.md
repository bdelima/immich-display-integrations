# immich-overflight-feed

Serves `/wallpapers.json` in the schema [Overflight](https://github.com/hitorunajp/Overflight)
(a Projectivy Launcher plugin) expects, so an Android TV / Nvidia Shield home
screen background rotates through a shared Immich album.

Docker Hub: [`bdelima/immich-overflight-feed`](https://hub.docker.com/r/bdelima/immich-overflight-feed)

## How it works

- Lists the album's assets via `POST /api/search/metadata` with an
  `albumIds` filter, authenticated with a personal Immich API key.
  (`GET /api/albums/{id}` does not return a usable `assets` array in current
  Immich versions, under any auth.)
- Builds each `url_img` (or `url_1080p` for videos) link using Immich's
  shared-link auth (`SHARE_SLUG` or `SHARE_KEY`), since personal API keys
  403 on assets contributed by other album members, while shared-link auth
  is scoped to the whole share regardless of who added what.
- Caches the album listing in memory for `CACHE_SECONDS` to avoid hammering
  Immich on every Overflight poll.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `IMMICH_INTERNAL_URL` | yes | Immich URL reachable from this container (e.g. `http://immich-server:2283` on the same Docker network). Used for the album listing call. |
| `IMMICH_PUBLIC_URL` | yes | Immich URL reachable from the *device displaying the wallpapers* (e.g. `https://immich.example.com`). Used to build the `url_img` links. |
| `ALBUM_ID` | yes | UUID of the Immich album to serve. |
| `IMMICH_API_KEY` | yes | Personal Immich API key, used only for listing. |
| `SHARE_SLUG` or `SHARE_KEY` | one required | Immich shared-link auth, used for the asset download URLs embedded in the JSON. |
| `CACHE_SECONDS` | no (default `300`) | How long to cache the album listing before re-fetching. |
| `REQUEST_TIMEOUT` | no (default `10`) | Timeout in seconds for calls to Immich. |
| `PORT` | no (default `8080`) | Port the Flask app listens on inside the container. |

## Endpoints

- `GET /wallpapers.json` — the Overflight-formatted feed.
- `GET /health` — liveness check, returns `200 ok`.

## Running

```bash
docker run -d \
  -p 8080:8080 \
  -e IMMICH_INTERNAL_URL=http://immich-server:2283 \
  -e IMMICH_PUBLIC_URL=https://immich.example.com \
  -e ALBUM_ID=your-album-uuid \
  -e IMMICH_API_KEY=your-api-key \
  -e SHARE_SLUG=your-share-slug \
  bdelima/immich-overflight-feed:latest
```

See the repo-root `docker-compose.example.yml` for running both services
alongside an existing Immich stack.
