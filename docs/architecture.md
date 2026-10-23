# Architecture

How a frame travels from the camera to the dashboard, the decisions behind that path, and what it
costs the server. Deployment is in [deployment.md](deployment.md), every setting in
[configuration.md](configuration.md).

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

## Design decisions

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
so every worker goes through a single lazily loaded model behind a lock; a camera with a model of
its own gets a second instance behind the same lock. Memory stays flat and behaviour stays
predictable; the cost is throughput (see [Limitations](../README.md#limitations)).

**An outage costs nothing.** If PostgreSQL is briefly unreachable, events and loading sessions wait
in memory and are retried with backoff (1 s doubling to 30 s) until they are saved; for a session
only its latest state is kept, since every update carries the whole of it. Rows the database
refuses outright, such as an event for a camera deleted a moment earlier, are dropped one at a
time, so they never hold the others back.

**Events, not detections.** Workers never persist per-frame detections, and an object merely coming
into view is not an event either. They emit a handful of event types: `line_crossed_in`,
`line_crossed_out`, `camera_online`, `camera_offline`, `vehicle_arrived` and `loading_completed`. A
track only counts once ByteTrack has confirmed it with two consecutive matches, and a line crossing
is triggered by the bottom center of the box, which is where a vehicle actually meets the road.
Storage grows with activity rather than with frame rate, and every crossing carries a downscaled
JPEG snapshot.

**Workers know nothing about asyncio or the database.** They hand events to the event loop, where the
`EventRecorder` batches them into PostgreSQL (up to 100 events or 250 ms, whichever comes first),
publishes them on the SSE bus and sends notifications. Its queue is bounded at 10,000 events: if the
database falls that far behind, events are dropped with a warning rather than stalling capture.

**Reconnecting is the normal path.** Streams drop, so a worker reconnects with exponential backoff
(1 s doubling to 60 s, ±20% jitter) and treats 20 s without a frame as a stall. Status changes
reach the UI over SSE.

**The browser's connection limit shapes the frontend.** Browsers allow about six HTTP/1.1 connections
per origin and every MJPEG stream holds one open. That's why long-lived connections bypass the Next.js
proxy, at most four visible tiles stream MJPEG at once while the rest refresh snapshots (twelve over
HTTP/2 behind Caddy), and one app-wide SSE connection updates the TanStack Query cache in place.

**Typed contract between the apps.** The frontend client is generated from FastAPI's OpenAPI schema
(`openapi-typescript` + `openapi-fetch`). After an API change, `bun run gen:api` regenerates the
types and the type checker points at everything that broke.

**Plain SQL instead of migrations, for now.** [`schema.sql`](../apps/api/src/yardlens_api/db/schema.sql)
holds the six tables (cameras, events, loading sessions, settings, users and refresh tokens),
idempotent (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) and
applied by `uv run db-setup`. Adding columns works; the first rename or type change should bring in
Alembic ([roadmap](roadmap.md#database-migrations)).

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
worth choosing over the 12400F or a Ryzen 5 5600: its integrated graphics decode the video. Past
what one machine can do, you want a GPU.

## Project layout

```
apps/
├── api/                          FastAPI service (Python 3.14, uv)
│   ├── src/yardlens_api/
│   │   ├── main.py               app factory, lifespan, retention task
│   │   ├── admin.py              `yardlens-admin`: create the administrator, reset the password
│   │   ├── bench.py              `yardlens-bench`: cost of a camera on this machine
│   │   ├── evaluate.py           `yardlens-eval`: count a recording, compare with a known count
│   │   ├── api/routes/           auth, health, system, cameras, streams, events, stats, loading,
│   │   │                         settings
│   │   ├── core/                 settings (YARDLENS_*), logging, errors, security primitives
│   │   ├── db/, schemas/         SQLAlchemy models and schema.sql, Pydantic v2 schemas
│   │   ├── services/
│   │   │   ├── sources.py        source validation, URL resolution, frame readers
│   │   │   ├── camera_worker.py  capture, inference and render threads
│   │   │   ├── camera_manager.py worker lifecycle, live reconfiguration
│   │   │   ├── detector.py       shared YOLO model, PyTorch or OpenVINO
│   │   │   ├── pacing.py         inference rate by activity, analysis region
│   │   │   ├── governor.py       load levels and the frame rates they allow
│   │   │   ├── resources.py      thread budget, priority, resource monitor
│   │   │   ├── plate_stream.py   main stream opened on demand for plates
│   │   │   ├── analyzer.py       ByteTrack, line counting, annotation
│   │   │   ├── loading.py        loading-bay sessions: arrival, cargo, departure
│   │   │   ├── plates.py         number plate reading and voting
│   │   │   ├── loading_store.py  sessions in the database, reports, Excel export
│   │   │   ├── event_recorder.py batching, SSE, notifications
│   │   │   ├── auth.py           users, sessions, sign-in throttling
│   │   │   └── notifier.py       Telegram
│   └── tests/
└── web/                          Next.js dashboard (Bun)
    └── src/
        ├── app/                  overview, loading, cameras, events, settings, login
        ├── components/           pages, settings forms, editors, shadcn/ui primitives
        ├── i18n/                 Russian (default) and English copy, formatting
        └── lib/api/              client and types generated from OpenAPI
deploy/                           Docker Compose, Caddy; systemd units, Windows tasks
docs/                             this file, configuration, deployment, sack model plan, roadmap
```

The API tests need neither Postgres nor model weights: they run against SQLite in a temp directory,
with a fake detector injected through `create_app(detector=...)`. Tracking and line counting are
tested with synthetic detections, which is why `FrameAnalyzer` has no threads or I/O of its own.
