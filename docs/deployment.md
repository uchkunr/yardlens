# Deployment: the server in Docker

The server runs as four containers: PostgreSQL, the API (which also processes the cameras), the
dashboard and Caddy for HTTPS. One server runs every camera.

```
browsers ──https──▶ Caddy :443 ─▶ dashboard (Next.js)          docker compose
                       └─ /api, /media ─▶ API ─▶ PostgreSQL
                                           └─ cameras
```

## The server

Needs Docker with Compose v2.

```bash
cd deploy
cp .env.example .env    # set PUBLIC_HOST, ADMIN_PASSWORD, POSTGRES_PASSWORD
docker compose up -d
```

Open `https://<PUBLIC_HOST>` and sign in as the administrator from `.env`. The browser warns about
the certificate until its authority is installed (below). To build the images locally instead of
pulling a release, run `docker compose build` first.

The image runs inference on the CPU through OpenVINO. To use an NVIDIA GPU or Apple silicon, run
the API outside Docker (`bun run build && bun run start`, or the systemd units and the Windows
script in [`deploy/`](../deploy)): PyTorch then picks the GPU by itself.

### HTTPS on the local network

Caddy issues the certificates from its own certificate authority, so HTTPS works without a domain
or internet access. Install that authority once on each device that opens the dashboard: Settings,
System, Download certificate, then follow the steps for the device shown there.

With a real domain later, set `PUBLIC_HOST` to it and remove `tls internal` from
[`deploy/Caddyfile`](../deploy/Caddyfile): Caddy then gets a public certificate by itself.

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
a new one on every device, so back it up as well.

## Updating

Set the new `YARDLENS_VERSION` in `.env`, then `docker compose pull && docker compose up -d`. The
schema is applied on start.

## Releasing images

Images are built by [`.github/workflows/images.yml`](../.github/workflows/images.yml) when a tag
`v<version>` matching `apps/api/pyproject.toml` is pushed:

- `ghcr.io/uchkunr/yardlens:<version>`: the API;
- `ghcr.io/uchkunr/yardlens-web:<version>`: the dashboard.

Make both packages public once on GitHub (package settings) so they can be pulled without logging
in. On a network without internet access, move images with `docker save` and `docker load`.
