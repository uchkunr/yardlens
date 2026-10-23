# Configuration and API

Every variable has a working default; YardLens runs with none of them set. To override any:

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

## API (`apps/api/.env`)

| Variable | Default | |
| --- | --- | --- |
| `YARDLENS_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/yardlens` | SQLAlchemy URL |
| `YARDLENS_CORS_ORIGINS` | `http://localhost:3100,http://127.0.0.1:3100` | Comma-separated |
| `YARDLENS_HOST` / `YARDLENS_PORT` | `127.0.0.1` / `8100` | Bind address for `bun run dev` and `uv run yardlens-api` |
| `YARDLENS_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `YARDLENS_LOG_JSON` | `false` | One JSON object per log line |
| `YARDLENS_DATA_DIR` | `data` | Snapshots, model weights, videos |
| `YARDLENS_DEFAULT_MODEL` | `yolo26n.pt` | Used until changed in Settings |
| `YARDLENS_INFERENCE_DEVICE` | `auto` | `auto` (NVIDIA GPU, then Apple Metal, else CPU), `cpu`, `cuda:0`, `mps`, `intel:gpu` |
| `YARDLENS_INFERENCE_BACKEND` | `auto` | `auto` (OpenVINO on the CPU, PyTorch on GPUs), `pytorch`, `openvino` |
| `YARDLENS_INFERENCE_THREADS` | `0` | CPU threads for inference; `0` picks them from the number of cores |
| `YARDLENS_HARDWARE_DECODE` | `true` | Decode camera video on the GPU where FFmpeg can |
| `YARDLENS_LOW_PRIORITY` | `true` | Run below normal priority so the rest of the system stays responsive |
| `YARDLENS_STREAM_FPS` | `15` | Live view frame rate, independent of inference FPS |
| `YARDLENS_STREAM_JPEG_QUALITY` | `80` | Quality of the live stream |
| `YARDLENS_MAX_FRAME_WIDTH` | `1280` | Wider frames are downscaled before inference and streaming |
| `YARDLENS_SNAPSHOT_MAX_WIDTH` | `960` | Width of event snapshots |
| `YARDLENS_EVENT_RETENTION_DAYS` | `7` | Older events and their snapshots are purged hourly |
| `YARDLENS_SESSION_IMAGE_RETENTION_DAYS` | `365` | Photos of older loading sessions are deleted; the sessions stay |
| `YARDLENS_LOADING_ARRIVE_SECONDS` | `15` | How long a vehicle stands in a loading zone before it counts as arrived |
| `YARDLENS_LOADING_DEPART_SECONDS` | `60` | How long a bay stays quiet before the vehicle counts as gone |
| `YARDLENS_PUBLIC_URL` | `http://localhost:3100` | Where people open the dashboard; HTTPS here makes the sign-in cookies Secure |
| `YARDLENS_ADMIN_USERNAME` / `YARDLENS_ADMIN_PASSWORD` | unset | Create the administrator on first start; otherwise `uv run yardlens-admin create` |
| `YARDLENS_AUTH_SECRET` | generated | Signs access tokens; generated once into the data directory when unset |
| `YARDLENS_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers are trusted (Caddy) |
| `YARDLENS_CA_CERT_PATH` | unset | The certificate authority behind HTTPS, offered for download under Settings → System |

Telegram credentials are deliberately not environment variables; they're set in the UI and stored
server-side, and the API only ever returns a masked hint of the token.

## Dashboard (`apps/web/.env.local`)

| Variable | Default | |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8100` | API origin for the rewrites, MJPEG, SSE and `gen:api`; empty behind Caddy, where the API shares the dashboard's origin |

The dashboard port (3100) is set in `apps/web/package.json`: Next.js picks its port before it reads
any `.env` file. If you change it, update `YARDLENS_CORS_ORIGINS` to match.

## API endpoints

Everything lives under `/api`; the full schema is at `/docs` (off in production). Everything except
signing in needs a signed-in user. Event snapshots are served from `/media/snapshots/`, also to
signed-in users only.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Version, uptime, cameras online, model and device |
| `GET /api/system/resources` | CPU, memory and GPU load, the governor's level and each camera's current rate |
| `GET /api/system/ca.pem` | The certificate authority behind the server's HTTPS, when it has one |
| `GET, POST /api/cameras` | List and create cameras |
| `POST /api/cameras/test` | Grab one frame from a source without saving it |
| `GET, PATCH, DELETE /api/cameras/{id}` | Read, update (the running worker picks up the change, including its own `detection` settings), delete |
| `POST /api/cameras/{id}/enable`, `/disable` | Start or stop a camera |
| `GET /api/cameras/{id}/stream.mjpeg` | Annotated live stream |
| `GET /api/cameras/{id}/snapshot.jpg` | Latest annotated frame; `?raw=true` for the clean one |
| `GET /api/events` | Filter by camera, type, class and time range; paginated, up to 200 per page |
| `GET /api/events/stream` | Server-sent events: new events, camera status changes and loading sessions |
| `GET /api/events/{id}` | A single event |
| `GET /api/stats/overview` | Today's totals with per-class, per-camera and hourly breakdowns |
| `GET /api/loading/summary` | Vehicles and cargo today, vehicles in a bay now, one entry per day |
| `GET /api/loading/sessions` | Loading history: filter by plate, camera and time; with totals |
| `GET /api/loading/sessions/export` | The same history as an Excel file |
| `GET, PATCH /api/loading/sessions/{id}` | One loading session; `PATCH` fixes its plate by hand |
| `GET, PUT /api/settings` | Detection, Telegram and performance settings |
| `GET /api/settings/models/{model}/classes` | The classes a model detects, for a camera's own settings |
| `POST /api/settings/telegram/test` | Send a test message |
| `POST /api/auth/login`, `/refresh`, `/logout`, `/logout-all`, `/password`; `GET /api/auth/me` | Sign-in with HttpOnly cookies (see [deployment](deployment.md#security)) |
