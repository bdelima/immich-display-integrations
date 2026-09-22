# immich-frame-mirror

Long-running service that mirrors a shared Immich album onto a Samsung Frame
TV's Art Mode over its local WebSocket API, including Wake-on-LAN handling
for a TV that sleeps aggressively.

Docker Hub: [`bdelima/immich-frame-mirror`](https://hub.docker.com/r/bdelima/immich-frame-mirror)

## How it works

- On each cycle, diffs the Immich album's current images against what's
  already on the TV (tracked in `state.json`), without waking the TV if
  nothing changed.
- When there's a diff, sends a Wake-on-LAN magic packet and retries the
  WebSocket connection with a generous budget (15 attempts by default),
  since a Frame TV can take several minutes to come up from a full
  power-off.
- Only ever deletes images it uploaded itself (tracked via `content_id` in
  `state.json`) — never touches art added manually through the Samsung app
  or purchased art.
- Downloads asset bytes via Immich's shared-link auth, since personal API
  keys 403 on assets contributed by other album members.
- Saves state incrementally so a mid-batch crash doesn't lose track of
  what's already been uploaded.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `IMMICH_INTERNAL_URL` | yes | Immich URL reachable from this container, e.g. `http://immich-server:2283`. |
| `IMMICH_API_KEY` | yes | Personal Immich API key, used for listing. |
| `ALBUM_ID` | yes | UUID of the Immich album to mirror. Only `IMAGE` assets are used — videos are skipped. |
| `SHARE_SLUG` or `SHARE_KEY` | one required | Immich shared-link auth, used to download asset bytes. |
| `FRAME_IP` | yes | LAN IP of the Frame TV. |
| `FRAME_MAC` | yes | MAC address of the Frame TV, for Wake-on-LAN (`-` or `:` separators both work). |
| `MATTE` | no (default `none`) | Matte style applied to uploaded art. |
| `CHECK_INTERVAL_SECONDS` | no (default `3600`) | How often to check Immich for changes. |
| `SHORT_RETRY_SECONDS` | no (default `300`) | Retry delay after a cycle that ended early (e.g. TV never woke up). |
| `REQUEST_TIMEOUT` | no (default `30`) | Timeout in seconds for calls to Immich. |
| `CONNECT_RETRIES` | no (default `15`) | Max attempts to connect to the TV after sending WoL. |
| `CONNECT_RETRY_DELAY_SECONDS` | no (default `60`) | Delay between connection attempts. |
| `RESEND_WOL_EVERY_N_ATTEMPTS` | no (default `3`) | Re-send the WoL packet every N failed connection attempts. |

## Persistent state

Bind-mount `/data` to somewhere durable. It holds:

- `token.txt` — the one-time TV pairing approval. Without it persisting,
  every container restart re-triggers an "Allow connection?" prompt on the
  TV itself.
- `state.json` — maps Immich asset IDs to the TV's `content_id`s, so the
  service knows what it already uploaded.

## Running

```bash
docker run -d \
  -v /path/to/data:/data \
  -e IMMICH_INTERNAL_URL=http://immich-server:2283 \
  -e IMMICH_API_KEY=your-api-key \
  -e ALBUM_ID=your-album-uuid \
  -e SHARE_SLUG=your-share-slug \
  -e FRAME_IP=192.168.1.50 \
  -e FRAME_MAC=AA:BB:CC:DD:EE:FF \
  bdelima/immich-frame-mirror:latest
```

See the repo-root `docker-compose.example.yml` for running both services
alongside an existing Immich stack.
