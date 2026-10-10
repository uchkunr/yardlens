# YardLens

Camera-based yard monitoring. YardLens connects to video streams, runs YOLO object detection with
ByteTrack tracking, counts objects crossing a line, and shows live annotated feeds and events in a
web dashboard.

![Overview: live annotated camera tiles and the real-time event feed](docs/screenshots/overview.webp)

![Camera detail: live feed with tracked objects and the counting line, counters and recent events](docs/screenshots/camera-detail.webp)

## Architecture

```
yardlens/
├── apps/
│   ├── api/   FastAPI + Ultralytics YOLO + supervision (Python, uv)
│   └── web/   Next.js App Router + shadcn/ui + TanStack Query (Bun)
├── nx.json    Nx is used only as a task runner
└── package.json (Bun workspaces)
```

```
             ┌──────────────────────── apps/api ────────────────────────┐
 RTSP/HLS/   │  CameraWorker (per camera)                               │
 MJPEG/YT/ ──┼─▶ capture thread ─ real-time (PTS-paced), latest frame   │
 file        │      ├──────────────────────────────┐                    │
             │      ▼                              ▼                    │
             │   inference thread @ N fps       render thread @ 15 fps │
             │   shared YOLO (lock) ─▶ ByteTrack   latest tracks,       │
             │   ─▶ LineZone ─▶ events            extrapolated ─▶ MJPEG │
             │      │ events (thread-safe hand-off)                     │
             │      ▼                                                   │
             │   EventRecorder (asyncio) ─▶ PostgreSQL ─▶ SSE bus ─▶ Telegram
             └──────────────────────────────────────────────────────────┘
                     ▲ JSON via Next rewrite      ▲ MJPEG / SSE direct
             ┌───────┴──────────── apps/web ──────┴─────────────────────┐
             │ Overview · Cameras · Events · Settings                    │
             └───────────────────────────────────────────────────────────┘
```

**Backend** (`apps/api/src/yardlens_api`)

| Module | Responsibility |
| --- | --- |
| `main.py` | App factory, lifespan: builds services, starts/stops workers, retention task |
| `core/` | Settings (`YARDLENS_*` env), structured logging, error types and handlers |
| `db/` | SQLAlchemy 2 async models and engine (PostgreSQL via asyncpg) |
| `schemas/` | Pydantic v2 request/response models |
| `api/routes/` | `health`, `cameras`, `streams`, `events`, `stats`, `settings` |
| `services/sources.py` | Validates and resolves RTSP / HTTP (MJPEG or JPEG snapshot) / HLS / YouTube (yt-dlp) / file sources; frame readers |
| `services/camera_worker.py` | Per-camera capture, inference and render threads, reconnect with exponential backoff, status, events, snapshots |
| `services/camera_manager.py` | Worker lifecycle (start, live config updates, restart, graceful stop) |
| `services/detector.py` | Shared, lazily loaded YOLO model; serialized inference off the event loop |
| `services/analyzer.py` | ByteTrack tracking, line counting (normalized coordinates), motion-extrapolated annotation |
| `services/event_recorder.py` | Batches events into the database, publishes them over SSE, triggers notifications |
| `services/notifier.py` | Telegram notifications (with per-camera rate limiting) |

The live view is decoupled from inference: video sources are paced by their timestamps (so files
and bursty HLS segments play in real time), and a render thread draws the latest tracks onto every
new frame at up to `YARDLENS_STREAM_FPS`, moving each box along its measured velocity until the
next inference result. The feed stays smooth even at 2–3 inference FPS.

Workers emit **events**, not per-frame detections: `object_entered`, `line_crossed_in`,
`line_crossed_out`, `camera_online`, `camera_offline`. Every detection event stores a JPEG snapshot in
`apps/api/data/snapshots`, served at `/media/snapshots/…`.

**Frontend** (`apps/web/src`)

- Typed API client generated from FastAPI's OpenAPI schema (`openapi-typescript` + `openapi-fetch`).
- TanStack Query for data, one app-wide SSE connection that updates caches in real time.
- Regular requests go through a same-origin Next.js rewrite; MJPEG streams and SSE connect to the API
  directly. Browsers allow only ~6 connections per origin, so at most 4 visible feeds stream MJPEG at
  once and the rest refresh snapshots.

## Requirements

- [Bun](https://bun.sh) ≥ 1.4 and [uv](https://docs.astral.sh/uv/) ≥ 0.12
- PostgreSQL (tested with 18) and the `psql` client (`brew install libpq`)
- Python 3.14 is installed automatically by uv

A local Postgres in Docker works out of the box:

```bash
docker run -d --name postgres18 -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18-alpine
```

## Setup and run

```bash
bun install
bun run setup   # once, and again after Python dependencies or the schema change
bun run dev
```

`bun run setup` runs a one-time chain (each step finishes and exits):

1. `api:install` – `uv sync`
2. `api:db-setup` – creates the database if needed and applies `apps/api/db/schema.sql` with `psql`
3. `api:seed` – downloads a sample traffic video and inserts demo cameras (only when no cameras exist)

`bun run dev` runs `nx run-many -t dev` and starts only the two apps in parallel. Nx shows them
as *Continuous* tasks in its terminal UI; select a task with ↑/↓ and press Enter to see its logs:

- **api** – `uvicorn --reload` on <http://localhost:8000> (OpenAPI docs at `/docs`)
- **web** – `next dev` on <http://localhost:3000>

On first start the YOLO weights (~5 MB) are downloaded; after that the sample camera shows live
annotated detections within seconds.

Other commands:

| Command | What it does |
| --- | --- |
| `bun run lint` | ESLint (web) and Ruff lint + format check (api) |
| `bun run typecheck` | `tsc --noEmit` (web) and Pyright strict (api) |
| `bun run test` | `bun test` (web) and pytest (api) |
| `bun run format` | Ruff fix + format (api) |
| `bun run setup` | `uv sync`, create the database and schema, seed demo cameras |
| `cd apps/api && uv run seed --force` | Re-add any missing demo cameras to a non-empty database |
| `bun run gen:api` | Regenerate `apps/web/src/lib/api/schema.d.ts` from the running API |

## Environment variables

Copy the examples and adjust as needed (all values have working defaults):

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

**API** (`apps/api/.env`)

| Variable | Default | Description |
| --- | --- | --- |
| `YARDLENS_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/yardlens` | SQLAlchemy URL |
| `YARDLENS_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated allowed origins |
| `YARDLENS_HOST` / `YARDLENS_PORT` | `127.0.0.1` / `8000` | Bind address for `uv run yardlens-api` |
| `YARDLENS_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `YARDLENS_LOG_JSON` | `false` | One JSON object per log line |
| `YARDLENS_DATA_DIR` | `data` | Snapshots, model weights, videos |
| `YARDLENS_DEFAULT_MODEL` | `yolo26n.pt` | Model used until changed in Settings |
| `YARDLENS_INFERENCE_DEVICE` | `auto` | `auto` (CUDA if available, else CPU), `cpu`, `mps`, `cuda:0` |
| `YARDLENS_STREAM_JPEG_QUALITY` | `80` | Quality of the annotated live stream |
| `YARDLENS_STREAM_FPS` | `15` | Frame rate of the annotated live stream (independent of inference FPS) |
| `YARDLENS_SNAPSHOT_MAX_WIDTH` | `960` | Event snapshots are downscaled to this width |
| `YARDLENS_MAX_FRAME_WIDTH` | `1280` | Wider frames are downscaled before inference, annotation and streaming |
| `YARDLENS_EVENT_RETENTION_DAYS` | `7` | Older events and their snapshots are purged hourly |

**Web** (`apps/web/.env.local`)

| Variable | Default | Description |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API origin for rewrites, MJPEG streams and SSE |

Telegram credentials are not environment variables: they are entered in **Settings → Integrations**,
stored server-side and never returned by the API (only a masked hint).

## Adding a camera

1. Open **Settings → Cameras → Add camera**.
2. Choose the source type and enter the URL:
   - **RTSP** – `rtsp://user:pass@192.168.1.20:554/stream1`
   - **HTTP** – an MJPEG stream (`multipart/x-mixed-replace`) or a JPEG snapshot URL (polled)
   - **HLS** – an `.m3u8` playlist
   - **YouTube** – a live stream URL; resolved to HLS with yt-dlp on every (re)connect
   - **File** – a path relative to `apps/api/data` (or absolute), played in a loop
3. Click **Test connection** to grab a frame and preview it, then save.
4. Optionally open **Counting line**, click two points on the frame and save. Objects crossing in the
   direction of the green arrow count as *in*, the other way as *out*.

Inference FPS is per camera (default 3). Detection model, confidence and classes are global
(**Settings → Detection**).

## Demo cameras

`uv run seed` adds:

- **Sample · Highway (looped video)** – supervision's `vehicles.mp4` sample, always available, with
  a counting line across both carriageways.
- Three **New York State DOT** traffic cameras (HLS), published for public viewing through the
  511NY traveler service: East Ridge Rd at Culver Rd (Rochester), NY 25 at NY 25A (Long Island) and
  I-278 BQE at 58 St (Queens). Draw a counting line for them in **Settings → Cameras**.

Public cameras are third-party feeds: availability and terms are up to their owners. More NYSDOT
cameras are listed (with `"public": true`) at
`https://nysdottg.carsprogram.org/cameras_v1/api/cameras`.

## Database

The schema is plain SQL in [`apps/api/db/schema.sql`](apps/api/db/schema.sql) and is applied with
`psql` by `api:db-setup`. It is idempotent (`CREATE … IF NOT EXISTS`). For schema changes beyond the
MVP, introduce a migration tool (e.g. Alembic). Tests use SQLite in a temporary directory.

## Known limitations

- **License:** Ultralytics YOLO is AGPL-3.0. Using it in a closed-source or commercial product
  requires an [Ultralytics Enterprise License](https://www.ultralytics.com/license) (or replacing the
  detector). `supervision` is MIT.
- Inference runs in-process and is serialized through one model instance; a nano model on CPU handles
  a handful of cameras at 2–5 FPS. Many cameras or larger models need a GPU or a separate inference
  service.
- Between inference results, box positions in the live view are extrapolated from recent motion;
  objects that brake or turn sharply can briefly drift until the next inference corrects them.
  Event snapshots always use exact inference positions.
- Line in/out counters on camera tiles are per worker session; dashboard totals come from stored events.
- Tracking at low FPS can split one object into several tracks in crowded or fast scenes, which can
  inflate `object_entered` counts. Line crossings are the more reliable metric.
- YouTube stream URLs expire; the worker re-resolves them on reconnect. YouTube may rate-limit an
  IP ("HTTP 429" / "Sign in to confirm you're not a bot"), in which case YouTube sources stay in the
  error state; this happened during development, so no YouTube camera is seeded. yt-dlp also needs a
  JavaScript runtime (Deno/Node/Bun) for YouTube.
- No authentication: run it on a trusted network or behind a reverse proxy with auth.
- No database migrations yet (see above).
