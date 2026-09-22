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
