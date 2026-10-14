# Roadmap: architecture work not done yet

Reviewed 2026-10-14. Done since that review: database writes are retried through outages, loading
sessions survive an API restart, each camera can have its own model and classes, loading bays run
at full rate while a vehicle is loaded, `yardlens-eval` checks counts against a recording, disk
space is watched, and CI runs on Linux and Windows. The sack model itself is in
[sack-model.md](sack-model.md). What is left:

## Authentication (not scheduled)

There is no login, and `GET /api/cameras` returns camera URLs with their credentials, which the
settings page also shows. Anyone who can reach the dashboard can see camera passwords and change
or delete cameras. Before the dashboard is reachable beyond a trusted network:

- Put it behind a reverse proxy that handles login (Caddy or nginx with basic auth, or an identity
  proxy), or add sessions to the API.
- Return camera URLs with the password masked (`rtsp://admin:••••@host/...`) and keep the stored
  one when an update sends the masked value back, the way the Telegram token is handled.

## Inference outside the API process

Capture, inference, the web API and database writes share one Python process. A crash in native
code (FFmpeg, OpenVINO, CUDA) takes the API down with it, and one inference at a time with no
batching caps throughput. The service managers in `deploy/` restart the process and loading
sessions resume, so for one to four cameras this is acceptable. Beyond that:

- Move camera workers into a separate process (or one per camera) that reports to the API over a
  queue (Redis streams, NATS, or plain multiprocessing pipes on one machine).
- Batch inference across cameras on the GPU; on the CPU keep one model instance per core group.

## Live view over WebRTC or HLS

The live view is MJPEG: simple and low-latency, but 1–3 Mbit/s per viewer and limited to about
six streams per browser by HTTP/1.1. For viewers on phones or over the internet:

- Encode the annotated stream to H.264 and serve it through MediaMTX (WebRTC for low latency,
  LL-HLS as a fallback), or draw the boxes in the browser over the camera's own stream with
  detections sent over the existing SSE connection.

## Database migrations

The schema is `apps/api/db/schema.sql`, made idempotent with `CREATE ... IF NOT EXISTS` and
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Adding columns works; renaming, changing a type or
moving data does not. Introduce Alembic before the first such change: a baseline revision matching
the current schema, `alembic stamp head` on existing databases, and `alembic upgrade head` in
`db-setup`.

## Dependency upgrades

The stack is recent (Python 3.14, PyTorch 2.14, Next.js 16, Ultralytics 8.4). Lockfiles keep
installs identical everywhere and CI catches breakage, but upgrade deliberately: PyTorch already
warns that `torch.jit.trace`, which the OpenVINO export uses, may break on Python 3.14+. After an
upgrade, run `bun run bench` and `uv run yardlens-eval` against a recorded loading as well.

## Licence (business decision)

Ultralytics YOLO is AGPL-3.0, so YardLens is too. Running a modified version as a network service
means offering its source to the service's users; selling it as a closed product needs an
[Ultralytics Enterprise License](https://www.ultralytics.com/license) or a permissively licensed
detector (for example RT-DETR from the PaddleDetection or HuggingFace ports, or YOLOX, Apache-2.0)
in place of YOLO.
