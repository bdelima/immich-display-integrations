# immich-display-integrations

Two small services that push photos from a shared [Immich](https://immich.app)
album out to physical displays:

- **[`overflight-feed/`](overflight-feed/)** — serves a JSON feed in the
  schema [Overflight](https://github.com/hitorunajp/Overflight) expects, so
  an Android TV / Nvidia Shield home screen rotates through the album as its
  background. Published as
  [`bdelima/immich-overflight-feed`](https://hub.docker.com/r/bdelima/immich-overflight-feed).
- **[`frame-mirror/`](frame-mirror/)** — mirrors the album onto a Samsung
  Frame TV's Art Mode via its local WebSocket API, including Wake-on-LAN
  handling for a TV that sleeps aggressively. Published as
  [`bdelima/immich-frame-mirror`](https://hub.docker.com/r/bdelima/immich-frame-mirror).

Each service has its own `VERSION` file, Dockerfile, and independent release
pipeline — see each subfolder's README for configuration and usage.

## Setup

Both services need three things from Immich that aren't obvious from the configuration tables in their own READMEs: an API key, an album id, and a shared-link slug/key. This section gets you all three, then wires up the shared secrets file both services (and [immich-photo-pipeline](https://github.com/bdelima/immich-photo-pipeline), if you're running that too) can read from.

### 1. Get an Immich API key

In the Immich web app, signed in as whichever account owns (or has access to) the album you want to display: **Account Settings → API Keys → New API Key**. This is the same `IMMICH_API_KEY` both services need for listing the album's contents.

If you're also running immich-photo-pipeline, reuse that project's own pipeline account and key here too, rather than creating a separate one — see step 3 below.

### 2. Find the album's id and create a shared link

Open the album in the Immich web app. The album's id is the UUID in the browser's address bar (`.../albums/<this-part>`) — that's your `ALBUM_ID`.

From that same album, create a shared link (the album's share/options menu → **Create link**). Immich generates a URL like `https://immich.example.com/share/<slug>`; the `<slug>` portion is your `SHARE_SLUG`. (Older Immich versions use a `key` query parameter instead of a path slug — if that's what you get, use `SHARE_KEY` instead; either env var works, exactly one is required.) This shared-link auth is what lets a device fetch the actual photo bytes without needing its own Immich login — a personal API key 403s on assets a different household member added, but the shared link doesn't care who added what.

### 3. Create the shared secrets file

Both services read `IMMICH_API_KEY` from a shared `KEY=VALUE` secrets file in preference to the env var of the same name — one file, one place to rotate the key, instead of editing every compose file whenever it changes. If you're running immich-photo-pipeline too, this is the *same* file it uses (see that project's README for creating it, since it also holds that project's own Claude/extra-account keys); otherwise create a small one just for these two services:

```bash
mkdir -p ../immich-shared-secrets
echo 'IMMICH_API_KEY=<the key from step 1>' > ../immich-shared-secrets/immich_secrets.env
```

`docker-compose.example.yml` already mounts this path into both services' `SECRETS_FILE`. Each service only ever reads the `IMMICH_API_KEY=` line out of it and ignores anything else in the file.

### 4. Bring the services up

```bash
cp docker-compose.example.yml docker-compose.yml
```

Fill in `IMMICH_INTERNAL_URL`, `IMMICH_PUBLIC_URL`, `ALBUM_ID`, and `SHARE_SLUG` (from steps 1–2) in `docker-compose.yml`; leave `IMMICH_API_KEY` unset there since the secrets file covers it. For frame-mirror, also fill in `FRAME_IP` and `FRAME_MAC` — the TV's LAN IP and MAC address, found in the TV's own network settings menu.

```bash
docker compose up -d
docker compose logs -f
```

frame-mirror's first successful connection to the TV triggers an "Allow connection?" prompt **on the TV itself** — approve it there once; the approval is saved to the `/data` volume and persists across restarts.

See each service's own README ([overflight-feed](overflight-feed/README.md), [frame-mirror](frame-mirror/README.md)) for the rest of their configuration options and what each one actually does.

## Releasing

Each service releases independently, triggered by a push to `main` that
changes that service's `VERSION` file:

1. Bump `overflight-feed/VERSION` or `frame-mirror/VERSION`.
2. Push to `main`.
3. GitHub Actions builds the image from that service's subfolder, pushes it
   to Docker Hub tagged with the version and `latest`, and creates a
   matching GitHub Release (tagged `overflight-feed-vX.Y.Z` or
   `frame-mirror-vX.Y.Z`).

### Required repo secrets

`.github/workflows/*.yml` expects these under Settings → Secrets and
variables → Actions:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN` — a Docker Hub access token (Account Settings → Security
  → New Access Token), not your account password.

## Running

See [`docker-compose.example.yml`](docker-compose.example.yml) for running
both services alongside an existing Immich stack using the published images.
