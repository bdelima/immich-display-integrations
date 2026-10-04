# Attribution

This repository's own code is licensed under the MIT License (see `LICENSE`). It uses or builds on the third-party projects below, each under its own license and copyright; nothing here relicenses them.

## Immich

- **Project:** [immich-app/immich](https://github.com/immich-app/immich)
- **License:** AGPL-3.0
- **How it's used:** the services in this repo call Immich's HTTP API. No Immich code is included or redistributed here.

## Python dependencies

Installed from PyPI at image build time, unmodified; exact pins are in each service's `requirements.txt`.

| Service | Package | License |
|---|---|---|
| frame-mirror | requests | Apache-2.0 |
| frame-mirror | samsungtvws | LGPL-3.0 |
| frame-mirror | websocket-client | Apache-2.0 |
| frame-mirror | wakeonlan | MIT |
| overflight-feed | flask | BSD-3-Clause |
| overflight-feed | requests | Apache-2.0 |
| overflight-feed | gunicorn | MIT |

`samsungtvws` is LGPL-3.0 and is used as an unmodified library.

## Base images

- The `python` slim image, with its own licenses.

## Trademarks

"Immich" and "Samsung" are trademarks of their respective owners. This is an unofficial project, not affiliated with or endorsed by them.
