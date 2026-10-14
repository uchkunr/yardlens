# Roadmap: architecture work not done yet

Reviewed 2026-10-14. Done since that review: database writes are retried through outages, loading
sessions survive an API restart, each camera can have its own model and classes, loading bays run
at full rate while a vehicle is loaded, `yardlens-eval` checks counts against a recording, disk
space is watched, CI runs on Linux and Windows, and there are sign-in, HTTPS, Docker images and
workers on other machines ([deployment.md](deployment.md)). The sack model itself is in
[sack-model.md](sack-model.md). What is left:

## Users and roles

Sign-in, HTTPS and masked camera passwords are done (see [deployment](deployment.md#security)).
There is one administrator; operators who may only watch would need users with roles.

## Inference outside the API process

Workers ([deployment](deployment.md#workers)) run cameras on other machines, and this server's
own processing can be turned off, so a crash in native code (FFmpeg, OpenVINO, CUDA) on a worker
no longer touches the API. Cameras that do run on the server still share its process. Next:

- Run this server's cameras in a local worker process too (the same agent over a local socket), so
  the API process never runs native video code.
- Batch inference across cameras on the GPU; on the CPU keep one model instance per core group.
- Test a camera from the worker it is pinned to, not from the server.

## Live view over WebRTC or HLS

The live view is MJPEG: simple and low-latency, but 1–3 Mbit/s per viewer and limited to about
six streams per browser by HTTP/1.1. For viewers on phones or over the internet:

- Encode the annotated stream to H.264 and serve it through MediaMTX (WebRTC for low latency,
  LL-HLS as a fallback), or draw the boxes in the browser over the camera's own stream with
  detections sent over the existing SSE connection.

## Database migrations

The schema is `apps/api/src/yardlens_api/db/schema.sql`, made idempotent with
`CREATE ... IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Adding columns works; renaming, changing a type or
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
