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

**GPU.** Inference runs on an NVIDIA GPU when there is one (in half precision), on Apple silicon
through Metal, and on the CPU otherwise; **Settings → System** shows which. On Windows, setup
installs PyTorch's CUDA 13.0 build, which needs an RTX 20-series card or newer and still works
without one; PyPI's Linux build already includes CUDA.

## How it works

```
RTSP · HLS · MJPEG / JPEG snapshot · YouTube (yt-dlp) · local file
  │
  ▼
CameraWorker: one per camera, three threads
  ├─ capture     keeps only the newest frame, paced by stream timestamps, reconnects with backoff
  ├─ inference   every 1/fps s: shared YOLO ─▶ ByteTrack ─▶ LineZone ─▶ events + JPEG snapshots
  └─ render      up to 15 fps while watched: latest tracks, extrapolated ─▶ annotated MJPEG
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
behaviour stays predictable; the cost is throughput (see [Limitations](#limitations)). On the CPU,
PyTorch is held to `YARDLENS_INFERENCE_THREADS` threads and its OpenMP threads wait passively: by
default they spin for about 200 ms after every inference, which with a few cameras kept every core
busy without making detection any faster.

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

**Plain SQL instead of migrations, for now.** [`apps/api/db/schema.sql`](apps/api/db/schema.sql) is
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
4. **Settings → Detection**: pick the model, and under **What counts as cargo** the sack class.

How a session runs:

- It opens once a vehicle (`truck`, `car` or `bus`) has stood in the zone for 15 s, recorded as
  arriving when it pulled in. Cargo counted before that opens it straight away, so nothing is lost.
- Every cargo item crossing the line is added to it. Cargo is not reported as one event per item.
- It closes after 60 s with neither a vehicle nor cargo in sight, so a worker walking in front of the
  truck or a break in loading doesn't split it.
- The plate is read twice a second from the full-resolution frame while the vehicle is parked, with
  [fast-alpr](https://github.com/ankandrew/fast-alpr) (a YOLOv9 plate detector and an OCR model,
  ONNX on the CPU, downloaded on first use). Readings are pooled, readings in an Uzbek format
  (`01 A 123 BC`, `01 123 ABC`) weigh more, and reading stops once one plate clearly wins. A plate
  fixed by hand in the dashboard is never overwritten.
- Telegram can report *vehicle arrived* and *loading completed* (plate, count, time in the bay).
  These are one per vehicle, so they skip the rate limit.
- Sessions are kept for good; event retention doesn't touch them.

**Sacks need a model of their own.** COCO has no sack class. Record footage from the bay, label a few
hundred frames with two classes, `truck` and `sack` (in CVAT or Label Studio, say), and train a YOLO
model on it (`yolo train data=bay.yaml model=yolo26n.pt`; about an hour on a GPU). Put the resulting
`.pt` file into `apps/api/data/models` and it appears under **Settings → Detection**. Keep the name
`truck` for the vehicle class: that is how YardLens knows a vehicle from cargo.
## API

Everything lives under `/api`. The full schema is at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Version, uptime, cameras online, model and device |
| `GET, POST /api/cameras` | List and create cameras |
| `POST /api/cameras/test` | Grab one frame from a source without saving it |
| `GET, PATCH, DELETE /api/cameras/{id}` | Read, update (the running worker picks up the change), delete |
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
| `GET, PUT /api/settings` | Detection and Telegram settings |
| `POST /api/settings/telegram/test` | Send a test message |

Event snapshots are served as static files from `/media/snapshots/`.

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
| `YARDLENS_INFERENCE_DEVICE` | `auto` | `auto` (NVIDIA GPU, then Apple Metal, else CPU), `cpu`, `mps`, `cuda:0` |
| `YARDLENS_INFERENCE_THREADS` | `4` | PyTorch CPU threads; matters when inference runs on the CPU |
| `YARDLENS_STREAM_FPS` | `15` | Live view frame rate, independent of inference FPS |
| `YARDLENS_STREAM_JPEG_QUALITY` | `80` | Quality of the live stream |
| `YARDLENS_MAX_FRAME_WIDTH` | `1280` | Wider frames are downscaled before inference and streaming |
| `YARDLENS_SNAPSHOT_MAX_WIDTH` | `960` | Width of event snapshots |
| `YARDLENS_EVENT_RETENTION_DAYS` | `7` | Older events and their snapshots are purged hourly |
| `YARDLENS_LOADING_ARRIVE_SECONDS` | `15` | How long a vehicle stands in a loading zone before it counts as arrived |
| `YARDLENS_LOADING_DEPART_SECONDS` | `60` | How long a bay stays quiet before the vehicle counts as gone |

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

Nx caches lint, typecheck and test. The API tests need neither Postgres nor model weights: they run
against SQLite in a temp directory, with a fake detector injected through
`create_app(detector=...)`. Tracking and line counting are tested with synthetic detections, which is
why `FrameAnalyzer` has no threads or I/O of its own.

```
apps/
├── api/                          FastAPI service (Python 3.14, uv)
│   ├── db/schema.sql
│   ├── src/yardlens_api/
│   │   ├── main.py               app factory, lifespan, retention task
│   │   ├── api/routes/           health, cameras, streams, events, stats, loading, settings
│   │   ├── core/                 settings (YARDLENS_*), logging, errors
│   │   ├── db/, schemas/         SQLAlchemy models, Pydantic v2 schemas
│   │   └── services/
│   │       ├── sources.py        source validation, URL resolution, frame readers
│   │       ├── camera_worker.py  capture, inference and render threads
│   │       ├── camera_manager.py worker lifecycle, live reconfiguration
│   │       ├── detector.py       shared YOLO model
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
```

## Limitations

- **No authentication.** Run it on a trusted network or behind a reverse proxy that handles auth.
- **Decoding stays on the CPU.** Detection moves to the GPU, but FFmpeg still decodes every frame on
  the CPU, and that grows with resolution and frame rate: a 4K stream at 25 fps takes about three
  quarters of a core. Use a camera's sub-stream (720p or so) when it has one.
- **Throughput is bounded by one model.** Inference is in-process and serialized. A GPU handles
  many cameras; a nano model on the CPU handles a handful at 2–5 fps each. Past that you want a
  separate inference service.
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
