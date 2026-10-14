# Deployment: the server in Docker, workers on other machines

The server runs as four containers (PostgreSQL, the API, the dashboard and Caddy for HTTPS). Any
other computer with Docker can join as a **worker**: it takes some of the cameras and analyzes them
with its own CPU and GPU. The server and its workers run the same release.

```
browsers ──https──▶ Caddy :443 ─▶ dashboard (Next.js)          docker compose, on the server
                       └─ /api, /media ─▶ API ─▶ PostgreSQL
                                           └─ cameras on this server (can be turned off)
worker PCs: docker run ghcr.io/uchkunr/yardlens ──wss /api/workers/connect──┘
```

## The server

Needs Docker with Compose v2. On a machine with an NVIDIA GPU, also the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/).

```bash
cd deploy
cp .env.example .env    # set PUBLIC_HOST, ADMIN_PASSWORD, POSTGRES_PASSWORD
docker compose up -d
```

Open `https://<PUBLIC_HOST>` and sign in as the administrator from `.env`. The browser warns about
the certificate until its authority is installed (below). To build the images locally instead of
pulling a release, run `docker compose build` first.

With an NVIDIA GPU on the server: `YARDLENS_VARIANT=cuda` in `.env`, then
`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.

### HTTPS on the local network

Caddy issues the certificates from its own certificate authority, so HTTPS works without a domain
or internet access. Install that authority once on each device that opens the dashboard: Settings,
System, Download certificate, then follow the steps for the device shown there. Workers need no
installation: their command carries the authority's fingerprint, and a worker trusts that authority
only.

With a real domain later, set `PUBLIC_HOST` to it and remove `tls internal` from
[`deploy/Caddyfile`](../deploy/Caddyfile): Caddy then gets a public certificate by itself.

## Workers

In the dashboard: Settings, Workers, Add worker. Give it a name and copy the command: it starts the
worker in Docker with its token, the server's address and the certificate fingerprint. The command
is shown once; the server stores only a hash of the token.

```bash
docker run -d --name yardlens-worker --restart unless-stopped -v yardlens-worker:/data \
  -e YARDLENS_SERVER=https://192.168.1.10 -e YARDLENS_WORKER_TOKEN=ylw_... \
  -e YARDLENS_CA_PIN=... ghcr.io/uchkunr/yardlens:0.1.0-cpu
```

The `-cpu` image runs on any x86-64 computer (PyTorch on the CPU with OpenVINO). On a computer with
an NVIDIA GPU use the second command (`--gpus all`, the `-cuda` image). A worker that is part of a
Compose setup can use [`deploy/worker-compose.yml`](../deploy/worker-compose.yml) instead.

The worker appears online within seconds. What it does from then on:

- **Placement.** A camera runs on exactly one node. A new camera goes to the least-loaded node
  (cores weighted, a GPU counts for more), then stays there: moving a camera resets its tracking.
  Pin a camera to a worker in its settings (Where to process) to keep it there; video files always
  run on the server. Rebalance in Settings, Workers spreads the automatic cameras evenly again.
- **This server.** "Process cameras on this server" can be turned off, so that the server only
  collects events and serves the dashboard.
- **Outages.** A worker cut off from the server keeps counting for a minute and keeps what it
  counted until the server has saved it. After a minute it stops its cameras; after a minute and a
  half the server gives them to other nodes. Loading sessions in progress carry on where they move.
  A worker that comes back within that time just continues.
- **Server restarts.** Workers keep running and reconnect. Cameras stay where they were.
- **Models.** A worker downloads custom models from the server (checked by SHA-256) and stock
  models from Ultralytics; they stay in its `/data` volume.
- **Clocks.** Event times are corrected for a worker whose clock is off; the dashboard warns when it
  is off by more than two seconds.

Removing a worker in the dashboard disconnects it at once; its command stops working.

## Security

- **Sign-in.** One administrator. Passwords are hashed with Argon2id; repeated wrong passwords are
  slowed down per address and overall, and reported to Telegram when it is set up. The browser
  holds an access token for 15 minutes and a refresh token that is renewed on every visit (good for
  400 days without one), both in HttpOnly cookies that scripts cannot read. A refresh token used
  twice signs that session out everywhere; Sign out everywhere and a password change take effect
  at once.
- **Requests.** Changes need a custom header and an allowed origin, so other sites cannot make
  them in a signed-in browser. Camera passwords never leave the server. API docs are off in
  production.
- **Workers.** Each has its own token (`ylw_<id>_<secret>`, stored hashed) and talks to the server
  over TLS, trusting only the server's certificate authority. Photos a worker sends are checked
  (name, size, JPEG) before they are written.
- **Network.** Only Caddy is published (ports 80 and 443). PostgreSQL and the API are reachable
  inside the Compose network only.
- **Reset the password** from the server: `docker compose exec api yardlens-admin set-password`.

## Backups

Two things hold state: the database and the API's data volume (snapshots, models, and the key that
signs access tokens; without the key everyone signs in again, nothing else is lost).

```bash
docker compose exec -T postgres pg_dump -U yardlens -Fc yardlens > yardlens.dump
docker run --rm -v yardlens_api-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/yardlens-data.tgz -C /data .
```

Caddy's volume (`yardlens_caddy-data`) holds the certificate authority. Losing it means installing
a new one on every device and adding the workers again, so back it up as well.

## Updating

Set the new `YARDLENS_VERSION` in `.env`, then `docker compose pull && docker compose up -d`. The
schema is applied on start. Update the workers to the same version (run the dashboard's command
again, or change the tag in their compose file): a worker refuses to work with a server of another
protocol version and says so in its log.

## Releasing images

Images are built by [`.github/workflows/images.yml`](../.github/workflows/images.yml) when a tag
`v<version>` matching `apps/api/pyproject.toml` is pushed:

- `ghcr.io/uchkunr/yardlens:<version>-cpu` and `-cuda`: the API and the worker (its default command);
- `ghcr.io/uchkunr/yardlens-web:<version>`: the dashboard.

Make both packages public once on GitHub (package settings) so workers can pull them without
logging in. On a network without internet access, move images with `docker save` and
`docker load`.
