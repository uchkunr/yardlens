# YardLens

Self-hosted video analytics for yards, gates and streets. Point it at a camera stream and it detects
and tracks vehicles and people with YOLO and ByteTrack, counts what crosses a line you draw, and
pushes annotated live video and events to a web dashboard and, optionally, to Telegram. At a
loading bay it records every vehicle: its plate, when it came and left, and how much was loaded.

![YardLens: live annotated cameras, line counting and real-time events](docs/yardlens-demo.gif)

**Backend:** FastAPI, Ultralytics YOLO, supervision + trackers (ByteTrack), OpenCV, SQLAlchemy 2
(async) on PostgreSQL. **Frontend:** Next.js 16 App Router, TanStack Query, shadcn/ui.
**Tooling:** Bun workspaces, uv, Nx as a task runner, Ruff, Pyright (strict).

## Quick start

You need [Bun](https://bun.sh) ≥ 1.4, [uv](https://docs.astral.sh/uv/) ≥ 0.12 and a PostgreSQL
server (tested with 17 and 18); no PostgreSQL client tools are needed. uv installs Python 3.14 on its
own. The steps are the same on Windows, macOS and Linux.

```bash
# skip if you already have Postgres running
docker run -d --name postgres18 -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18-alpine

bun install
bun run setup   # uv sync, create the database and schema, seed demo cameras once
bun run dev     # API on :8100, dashboard on :3100
```

Open <http://localhost:3100>. The first start downloads the YOLO weights (a few MB); after that the
sample camera shows annotated detections within seconds. OpenAPI docs live at
<http://localhost:8100/docs>.

`bun run setup` is safe to re-run after a Python dependency or schema change: every step is
idempotent, and demo cameras are seeded only once per database, so the ones you delete stay
deleted. It creates the database when it's missing, which needs a role that may create databases.
To run as a dedicated role without that right, create the database once as a superuser
(`CREATE DATABASE yardlens OWNER yardlens`) and setup only applies the schema. `bun run dev` runs
both apps as Nx continuous tasks; pick one with ↑/↓ and press Enter to see its logs.

**Language.** The dashboard is in Russian by default; switch to English from the header. The choice
is kept in a cookie and read on the server, so pages render in it from the first paint. Telegram
notifications have their own language under **Settings → Integrations**.

**GPU and CPU.** Inference runs on an NVIDIA GPU when there is one (in half precision), on Apple
silicon through Metal, and otherwise on the CPU through OpenVINO: the model is converted once, in a
few seconds, and cached next to the weights. Camera video is decoded on the GPU too where FFmpeg can
(Intel Quick Sync, NVIDIA, AMD). **Settings → Load** shows what runs where and how busy the machine
is. On Windows, setup installs PyTorch's CUDA 13.0 build, which needs an RTX 20-series card or newer
and still works without one; PyPI's Linux build already includes CUDA.

`bun run dev` is for development. On a server, build once and run without it; see
[Running on a server](#running-on-a-server).

## How it works

```
RTSP · HLS · MJPEG / JPEG snapshot · YouTube (yt-dlp) · local file
  │
  ▼
CameraWorker: one per camera, three threads
  ├─ capture     takes every frame off the stream, converts only the ones in use, reconnects
  ├─ inference   paced by activity: shared YOLO ─▶ ByteTrack ─▶ LineZone ─▶ events + snapshots
  └─ render      up to 15 fps while watched: latest tracks, extrapolated ─▶ annotated MJPEG
  ▲
  └─ ResourceMonitor  CPU, memory and GPU every 2 s ─▶ governor ─▶ frame rates for every worker
  │
  ▼  thread → event loop hand-off
EventRecorder
  ├─▶ PostgreSQL   batched inserts
  ├─▶ SSE bus      live dashboard updates
  └─▶ Telegram     optional, rate-limited
```

The dashboard talks to the API in two ways: short JSON requests go through a same-origin Next.js
rewrite, while MJPEG streams and the SSE connection go to the API directly.

### Design decisions

**Three threads per camera, newest frame wins.** Capture, inference and rendering share nothing but
the latest frame. If the model can't keep up, frames are dropped rather than queued, so latency stays
bounded instead of creeping up over the day. File and HLS sources are paced by their timestamps, so a
looped clip or a bursty HLS segment plays at real speed instead of as fast as it decodes.

**Inference rate and display rate are independent.** Inference runs at a per-camera rate (default
3 fps, max 15) and the live view renders at up to 15 fps. Between inference results the render thread
moves each box along its smoothed velocity, never more than `max(1.5 / fps, 0.3)` seconds past the
last measurement. You get a smooth feed on a CPU that could never run YOLO at 15 fps. Event snapshots
always use the real inference boxes, never extrapolated ones.

**Nothing is drawn for nobody.** After 5 s without a viewer the render thread stops annotating and
encoding until a stream or snapshot request asks for a frame again, and frames are downscaled only
when inference or rendering actually takes them, not as they're decoded. A system nobody is
looking at spends its CPU on decoding and detection alone.

**One model, shared and serialized.** Ultralytics models aren't safe for concurrent `predict` calls,
so every worker goes through a single lazily loaded model behind a lock. Memory stays flat and
behaviour stays predictable; the cost is throughput (see [Limitations](#limitations)). How the
machine's cores are shared out is described in [Performance and load](#performance-and-load).

**An outage costs nothing.** If PostgreSQL is briefly unreachable, events and loading sessions wait
in memory and are retried with backoff (1 s doubling to 30 s) until they are saved; for a session
only its latest state is kept, since every update carries the whole of it. Rows the database
refuses outright, such as an event for a camera deleted a moment earlier, are dropped one at a
time, so they never hold the others back.

**Events, not detections.** Workers never persist per-frame detections. They emit five event types:
`object_entered`, `line_crossed_in`, `line_crossed_out`, `camera_online` and `camera_offline`. A
track only counts once ByteTrack has confirmed it with two consecutive matches, and a line crossing is
triggered by the bottom center of the box, which is where a vehicle actually meets the road. Storage
grows with activity rather than with frame rate, and every detection event carries a downscaled JPEG
snapshot.

**Workers know nothing about asyncio or the database.** They hand events to the event loop, where the
`EventRecorder` batches them into PostgreSQL (up to 100 events or 250 ms, whichever comes first),
publishes them on the SSE bus and sends notifications. Its queue is bounded at 10,000 events: if the
database falls that far behind, events are dropped with a warning rather than stalling capture.

**Reconnecting is the normal path.** Streams drop, so a worker reconnects with exponential backoff
(1 s doubling to 60 s, ±20% jitter) and treats 20 s without a frame as a stall. YouTube URLs expire,
so they're re-resolved with yt-dlp on every reconnect. Status changes reach the UI over SSE.

**The browser's connection limit shapes the frontend.** Browsers allow about six HTTP/1.1 connections
per origin and every MJPEG stream holds one open. That's why long-lived connections bypass the Next.js
proxy, at most four visible tiles stream MJPEG at once while the rest refresh snapshots, and one
app-wide SSE connection updates the TanStack Query cache in place.

**Typed contract between the apps.** The frontend client is generated from FastAPI's OpenAPI schema
(`openapi-typescript` + `openapi-fetch`). After an API change, `bun run gen:api` regenerates the
types and the type checker points at everything that broke.

**Plain SQL instead of migrations, for now.** [`schema.sql`](apps/api/src/yardlens_api/db/schema.sql) is
three tables, idempotent (`CREATE … IF NOT EXISTS`) and applied by `uv run db-setup`. That's enough
for an MVP; the next schema change should bring in Alembic.

## Cameras

Add cameras under **Settings → Cameras**. **Test connection** grabs a single frame so you can check the
URL before saving.

| Type | URL |
| --- | --- |
| RTSP | `rtsp://user:pass@192.168.1.20:554/stream1` |
| HTTP | An MJPEG stream (`multipart/x-mixed-replace`) or a JPEG snapshot URL, which gets polled |
| HLS | An `.m3u8` playlist |
| YouTube | A live stream URL, resolved to HLS with yt-dlp |
| File | A path relative to `apps/api/data` (or absolute), played in a loop |

To count, open **Counting line**, click two points on the frame and save. Crossing in the direction of
the green arrow counts as *in*, the other way as *out*. The line is stored in normalized coordinates,
so it survives a resolution change.

Inference FPS is set per camera. Model, confidence (default 0.35) and classes (default car, truck,
bus, motorcycle, person) are global, under **Settings → Detection**.

**Demo cameras.** `bun run setup` seeds a looped highway clip (supervision's `vehicles.mp4` sample)
with a counting line already drawn, plus three New York State DOT traffic cameras published through
511NY. Those are third-party feeds, so availability and terms are up to their owners. To re-add
missing demo cameras to a database that isn't empty, run `uv run seed --force` in `apps/api`.

**Telegram.** Configured under **Settings → Integrations**. By default it notifies on line crossings,
on a camera going offline and on a completed loading, at most once every 30 s per camera and event
type (loading messages are never held back). Messages are in Russian or English, set on the same page. The bot token stays
on the server; the API only ever returns a masked hint.

## Loading bays

At a warehouse where vehicles are loaded (sacks on a conveyor, say), a camera can watch the bay and
keep a *loading session* per vehicle: its plate, when it arrived and left, and how many items went
on board. The overview then leads with a report for a manager: vehicles and sacks today, a 14-day
chart and the vehicle in the bay right now. **Loadings** holds the full history, with plate search,
totals and an Excel export.

To set a camera up as a loading bay:

1. Add it. To count sacks on a conveyor, set inference FPS to 8–15.
2. **Counting line**: draw it across the conveyor where the sacks go into the vehicle, with the
   green arrow pointing into the vehicle. That direction counts as loaded, the other as unloaded.
3. **Loading zone**: click the corners of the spot where the vehicle stands while it is loaded.
4. **Detection**: in the camera's menu, give it settings of its own (a model trained for the bay,
   its classes, and under **What counts as cargo** the sack class), so other cameras keep theirs.
   Settings left unset there follow **Settings → Detection**.

How a session runs:

- It opens once a vehicle (`truck`, `car` or `bus`) has stood in the zone for 15 s, recorded as
  arriving when it pulled in. Cargo counted before that opens it straight away, so nothing is lost.
- Every cargo item crossing the line is added to it. Cargo is not reported as one event per item.
- It closes after 60 s with neither a vehicle nor cargo in sight, so a worker walking in front of the
  truck or a break in loading doesn't split it.
- While a vehicle is in the bay the camera analyses at its full rate, and the session is saved at
  least once a minute. If the API restarts meanwhile (an update, a crash) the session is picked up
  again rather than split in two; if the vehicle turns out to be gone, it closes as of the last
  time it was seen.
- The plate is read twice a second from the full-resolution frame while the vehicle is parked, with
  [fast-alpr](https://github.com/ankandrew/fast-alpr) (a YOLOv9 plate detector and an OCR model,
  ONNX on the CPU, downloaded on first use). Readings are pooled, readings in an Uzbek format
  (`01 A 123 BC`, `01 123 ABC`) weigh more, and reading stops once one plate clearly wins. A plate
  fixed by hand in the dashboard is never overwritten.
- Telegram can report *vehicle arrived* and *loading completed* (plate, count, time in the bay).
  These are one per vehicle, so they skip the rate limit.
- Sessions are kept for good; event retention doesn't touch them. Their photos are kept for a year
  (`YARDLENS_SESSION_IMAGE_RETENTION_DAYS`).

**Sacks need a model of their own.** COCO has no sack class, and zero-shot detectors were tried and
did not find sacks on a moving conveyor. Record footage from the bay, label a few hundred frames
with two classes, `truck` and `sack`, train a YOLO model on it, and check its count against loadings
counted by hand with `uv run yardlens-eval`. The full procedure is in
[docs/sack-model.md](docs/sack-model.md). Keep the name `truck` for the vehicle class: that is how
YardLens knows a vehicle from cargo.
## Performance and load

The goal is a server that never chokes: cameras get the CPU they need while something happens, give
it back while nothing does, and slow down in a set order when the machine runs out.

### Where the CPU goes

Measured with `bun run bench` on the sample 4K clip, on an i7-14700K:

| Stage | CPU time per frame | How often |
| --- | --- | --- |
| Decoding 4K H.264 | 15 ms on the CPU, 3.6 ms with GPU decoding | Every frame of the stream, 25 a second |
| Turning a decoded 4K frame into an image | 18–23 ms | Only frames that are used |
| YOLO26n, PyTorch on the CPU | 50 ms | Each inference |
| YOLO26n, OpenVINO, 2 threads | 35 ms; 17 ms for a cropped bay at 416 px | Each inference |
| YOLO26n on an RTX 4070 | 16 ms, mostly pre- and post-processing | Each inference |

### What keeps it down

- **Inference follows activity.** A camera runs at its configured rate while something moves,
  appears or crosses the line, for 8 s after, and for as long as a vehicle is being loaded. A vehicle
  that has just parked is watched at 3 fps until its session opens, and a scene where nothing moves
  at 1 fps (**Settings → Load**): enough to notice that something changed.
- **Only frames in use are converted.** Capture keeps up with the stream, but converts a frame to an
  image only when inference or a viewer is due one. On the 4K clip this halves the cost of an idle
  camera; with GPU decoding as well it falls to about a fifth.
- **GPU decoding when available.** FFmpeg decodes on Intel Quick Sync, NVIDIA or AMD where it can,
  and falls back to the CPU on its own; a camera whose GPU decoding fails switches to the CPU for
  good.
- **Sub-stream for analysis, main stream for plates.** IP cameras usually offer a full-resolution
  main stream and a light sub-stream. Give the camera the sub-stream and put the main stream under
  **Main stream for plates**: it is opened only while a parked vehicle's plate is being read and
  closed 15 s after, instead of being decoded all day.
- **OpenVINO on the CPU.** Without an NVIDIA GPU the model runs on OpenVINO, which needs about half
  the time and a third less CPU than PyTorch, on Intel and AMD alike. The PyTorch model serves while
  the converted copy is prepared; a retrained model is converted again.
- **Loading bays look at the bay only.** A camera with a loading zone runs the model on the zone and
  counting line plus a margin, at a matching input size (down to 320 px instead of 640) when that
  covers no more than 60% of the frame.
- **A fixed thread budget.** Every library would otherwise start a thread per core. Inference gets a
  third of the physical cores (at most 4), FFmpeg 2 threads per stream, OpenCV 2 and plate OCR 1–2;
  a six-core machine gives inference 2 threads. `YARDLENS_INFERENCE_THREADS` overrides it.
- **Below-normal priority.** The operating system, PostgreSQL and the dashboard stay responsive
  even when the cameras keep the CPU busy.
- **Disk.** The monitor also watches the disk holding the data directory. With less than 2 GB free,
  snapshots stop being written (counting goes on) until space is freed, and **Settings → Load**
  says so.

### The governor

Every 2 s the resource monitor reads CPU, memory, GPU and how busy the detector is, and compares the
busiest of them with the CPU limit (75% by default, **Settings → Load**):

| Level | When | Live view | Inference |
| --- | --- | --- | --- |
| Normal | Under the limit | 15 fps | As configured |
| Busy | Over the limit for 4 s | 8 fps | As configured |
| High | 4 s more | 4 fps | 70% of the configured rate, idle cameras at half rate |
| Overload | 4 s more | 2 fps | 50% of the configured rate |

Counting never drops below 4 fps (or the camera's own rate, if lower), so no crossing is missed. The
governor steps back down one level after 10 s at least 15 points under the limit, and memory above
92% raises it to *High* right away. The level, the readings of the last three minutes and each
camera's current rate are under **Settings → Load** and at `GET /api/system/resources`.

### Sizing a server

`bun run bench [video]` runs the camera pipeline on a recorded clip with the model, backend and
threads the API would use, then prints the cost of each stage, the load per camera when idle, in
standby and active, and how many cameras can be active at once under the limit. Run it on the
server with a clip from the real camera. For the cameras themselves: H.264 rather than H.265, 10–12
fps, and the sub-stream for analysis. On a CPU-only server an Intel i5-12400 (not the F model) is
worth choosing over the 12400F or a Ryzen 5 5600: its integrated graphics decode the video.

### Running on a server

The simplest is Docker: the server, HTTPS and the database in one Compose file, and any number of
worker computers that take cameras off the server with one `docker run` command each. See
[docs/deployment.md](docs/deployment.md).

Without Docker:

```bash
bun install
bun run setup
bun run build
bun run start   # API on :8100, dashboard on :3100
```

`bun run dev` compiles pages on demand and restarts the API on every file change, which costs more
than the cameras do. To start YardLens with the machine:

- **Linux:** systemd units are in [`deploy/linux`](deploy/linux). Adjust the user and paths, copy
  them to `/etc/systemd/system` and run `systemctl enable --now yardlens-api yardlens-web`. They add
  a hard ceiling (`CPUQuota`, `MemoryMax`) on top of the governor.
- **Windows:** [`deploy/windows/register-tasks.ps1`](deploy/windows/register-tasks.ps1) registers
  two scheduled tasks that start at boot, run below normal priority and restart if they stop. Run
  `bun run build` first, then the script from an elevated PowerShell.

## API

Everything lives under `/api`. The full schema is at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Version, uptime, cameras online, model and device |
| `GET /api/system/resources` | CPU, memory and GPU load, the governor's level and each camera's current rate |
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
| `POST /api/auth/login`, `/refresh`, `/logout`, `/logout-all`, `/password`; `GET /api/auth/me` | Sign-in with HttpOnly cookies (see [deployment](docs/deployment.md#security)) |
| `GET, POST /api/workers`; `PATCH, DELETE /api/workers/{id}` | Workers with their load and cameras; adding one returns its token and start command once |
| `PUT /api/workers/local`, `POST /api/workers/rebalance` | Whether this server processes cameras; spread cameras over the nodes |
| `WS /api/workers/connect` | The worker channel (worker token; see `schemas/worker_protocol.py`) |

Everything except signing in and the worker endpoints needs a signed-in user. Event snapshots are
served from `/media/snapshots/`, also to signed-in users only.

## Configuration

Every variable has a working default. To override any of them:

```bash
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local
```

**API** (`apps/api/.env`)

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
| `YARDLENS_WORKERS_ENABLED` | `true` | `false`: this server never processes cameras itself |
| `YARDLENS_WORKER_IMAGE` | `ghcr.io/uchkunr/yardlens` | The image in the dashboard's worker command |
| `YARDLENS_WORKER_SERVER_URL` | `YARDLENS_PUBLIC_URL` | The address workers connect to |
| `YARDLENS_CA_CERT_PATH` | unset | The certificate authority behind HTTPS: offered for download, pinned by workers |

A worker (`yardlens-worker`) reads `YARDLENS_SERVER`, `YARDLENS_WORKER_TOKEN` and
`YARDLENS_CA_PIN`, which its command from the dashboard sets, plus the inference variables above.

**Web** (`apps/web/.env.local`)

| Variable | Default | |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8100` | API origin for the rewrites, MJPEG, SSE and `gen:api` |

The dashboard port (3100) is set in `apps/web/package.json`: Next.js picks its port before it reads
any `.env` file. If you change it, update `YARDLENS_CORS_ORIGINS` to match.

Telegram credentials are deliberately not environment variables; they're set in the UI and stored
server-side.

## Development

| Command | |
| --- | --- |
| `bun run lint` | ESLint with zero warnings allowed, Ruff lint and format check |
| `bun run typecheck` | `tsc --noEmit`, Pyright in strict mode |
| `bun run test` | `bun test`, pytest |
| `bun run format` | Ruff fix and format |
| `bun run gen:api` | Regenerate `apps/web/src/lib/api/schema.d.ts` from the running API |
| `bun run bench [video]` | Measure what a camera costs on this machine (see [Sizing a server](#sizing-a-server)) |
| `uv run yardlens-eval video …` | Count a recording offline and compare with a known count (in `apps/api`) |
| `bun run start` | Production build of the dashboard and the API without reloading |

Nx caches lint, typecheck and test. The API tests need neither Postgres nor model weights: they run
against SQLite in a temp directory, with a fake detector injected through
`create_app(detector=...)`. Tracking and line counting are tested with synthetic detections, which is
why `FrameAnalyzer` has no threads or I/O of its own.

```
apps/
├── api/                          FastAPI service (Python 3.14, uv)
│   ├── src/yardlens_api/
│   │   ├── main.py               app factory, lifespan, retention task
│   │   ├── bench.py              `yardlens-bench`: cost of a camera on this machine
│   │   ├── evaluate.py           `yardlens-eval`: count a recording, compare with a known count
│   │   ├── api/routes/           health, system, cameras, streams, events, stats, loading, settings
│   │   ├── core/                 settings (YARDLENS_*), logging, errors
│   │   ├── db/, schemas/         SQLAlchemy models, Pydantic v2 schemas
│   │   └── services/
│   │       ├── sources.py        source validation, URL resolution, frame readers
│   │       ├── camera_worker.py  capture, inference and render threads
│   │       ├── camera_manager.py worker lifecycle, live reconfiguration
│   │       ├── detector.py       shared YOLO model, PyTorch or OpenVINO
│   │       ├── pacing.py         inference rate by activity, analysis region
│   │       ├── governor.py       load levels and the frame rates they allow
│   │       ├── resources.py      thread budget, priority, resource monitor
│   │       ├── plate_stream.py   main stream opened on demand for plates
│   │       ├── analyzer.py       ByteTrack, line counting, annotation
│   │       ├── loading.py        loading-bay sessions: arrival, cargo, departure
│   │       ├── plates.py         number plate reading and voting
│   │       ├── loading_store.py  sessions in the database, reports, Excel export
│   │       ├── event_recorder.py batching, SSE, notifications
│   │       └── notifier.py       Telegram
│   └── tests/
└── web/                          Next.js dashboard (Bun)
    └── src/
        ├── app/                  overview, loading, cameras, events, settings
        ├── i18n/                 Russian (default) and English copy, formatting
        └── lib/api/              client and types generated from OpenAPI
deploy/                           Docker Compose, Caddy, worker compose; systemd units, Windows tasks
docs/                             deployment, sack model plan, roadmap of the architecture work left
```

## Limitations

- **One administrator.** There are no other users or roles yet.
- **Camera tests run on the server.** Checking a camera pinned to a worker on another network from
  the camera form fails when the server cannot reach that camera; the worker itself still can.
- **Every frame is still decoded.** Without GPU decoding a 4K stream at 25 fps keeps about a third
  of a core busy even when nothing is converted; frames in use add their conversion on top. Give
  cameras their sub-stream (720p–1080p) where they have one.
- **Throughput is bounded by one model.** Inference is in-process and serialized. Past what
  `bun run bench` reports for your machine, you want a GPU or a separate inference service.
- **Extrapolation can drift.** An object that brakes or turns sharply may wander off its box until the
  next inference corrects it. Snapshots aren't affected.
- **`object_entered` can over-count.** At low inference rates, crowded or fast scenes can split one
  object into several tracks. Line crossings are the more reliable number.
- **Tile counters are per session.** The in/out numbers on camera tiles reset with the worker;
  dashboard totals come from stored events.
- **YouTube is fragile.** YouTube can rate-limit an IP (HTTP 429, "confirm you're not a bot"), which
  leaves the source in an error state, so no YouTube camera is seeded. yt-dlp also needs a JavaScript
  runtime (Deno, Node or Bun) to resolve YouTube URLs.
- **No migrations yet**, see above.

## License

[AGPL-3.0-only](LICENSE), matching Ultralytics YOLO, which YardLens depends on. If you run a modified
version as a network service, you have to offer its source to that service's users. Closed-source or
commercial use needs an [Ultralytics Enterprise License](https://www.ultralytics.com/license) or a
permissively licensed detector in place of YOLO. `supervision` is MIT.
