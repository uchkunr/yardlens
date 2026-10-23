# YardLens

Self-hosted video analytics for yards, gates and loading bays. Point it at a camera and it detects
and tracks vehicles and people with YOLO, counts what crosses a line you draw, and shows annotated
live video and events in a web dashboard. At a loading bay it records every vehicle: its plate, when
it came and left, and how much was loaded.

![YardLens: live annotated cameras, line counting and real-time events](docs/yardlens-demo.gif)

Built with [Claude Code](https://claude.com/claude-code).

**Stack.** FastAPI, Ultralytics YOLO, supervision + ByteTrack, OpenCV, SQLAlchemy 2 (async) on
PostgreSQL; Next.js 16, TanStack Query, shadcn/ui; Bun workspaces, uv, Nx as a task runner, Ruff,
Pyright (strict).

## What it does

- **Any camera.** RTSP, HLS, MJPEG or JPEG snapshot URLs, YouTube live streams, local video files.
- **Detection and counting.** Tracked YOLO detections; a counting line per camera gives in and out
  counts, and every crossing becomes an event with a snapshot.
- **Live dashboard.** Annotated feeds, today's statistics, an event log with filters and Telegram
  notifications. Russian by default, English as the second language, light and dark.
- **Loading bays.** A camera over a bay keeps a record per vehicle: its plate (read with
  [fast-alpr](https://github.com/ankandrew/fast-alpr)), arrival, departure and the sacks counted
  onto it, with history and an Excel export.
- **Runs on what you have.** An NVIDIA GPU, Apple silicon or the CPU through OpenVINO. A governor
  keeps the server under a CPU limit, and other machines can join as workers.
- **Ready for a server.** Sign-in, HTTPS, Docker images, retries through database outages, CI on
  Linux and Windows.

## Quick start

You need [Bun](https://bun.sh) ≥ 1.4, [uv](https://docs.astral.sh/uv/) ≥ 0.11 and a PostgreSQL
server (tested with 17 and 18). uv installs Python 3.14 on its own; no PostgreSQL client tools are
needed. The steps are the same on Windows, macOS and Linux.

```bash
# skip if you already have Postgres running
docker run -d --name postgres18 -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18-alpine

bun install
bun run setup                                  # uv sync, create the database and schema, seed demo cameras once
(cd apps/api && uv run yardlens-admin create)  # the dashboard's username and password (10+ characters)
bun run dev                                    # API on :8100, dashboard on :3100
```

Open <http://localhost:3100> and sign in. The first start downloads the YOLO weights (a few MB);
after that the sample camera shows annotated detections within seconds. OpenAPI docs are at
<http://localhost:8100/docs>.

`bun run setup` is safe to re-run after a dependency or schema change: every step is idempotent, and
demo cameras are seeded only once per database. It creates the database when it's missing, which
needs a role that may create databases; otherwise create it once as a superuser
(`CREATE DATABASE yardlens OWNER yardlens`) and setup only applies the schema. Every setting is an
environment variable with a working default: see [docs/configuration.md](docs/configuration.md).

## Cameras

Add cameras under **Settings → Cameras**. **Test connection** grabs a single frame so you can check
the URL before saving.

| Type | URL |
| --- | --- |
| RTSP | `rtsp://user:pass@192.168.1.20:554/stream1` |
| HTTP | An MJPEG stream (`multipart/x-mixed-replace`) or a JPEG snapshot URL, which gets polled |
| HLS | An `.m3u8` playlist |
| YouTube | A live stream URL, resolved to HLS with yt-dlp |
| File | A path relative to `apps/api/data` (or absolute), played in a loop |

To count, open the camera's menu, **Counting line**, click two points on the frame and save.
Crossing in the direction of the green arrow counts as *in*, the other way as *out*. Inference FPS
is set per camera; model, confidence and classes are set once under **Settings → Detection**, and a
camera can have settings of its own (a model trained for its scene) from the same menu.

`bun run setup` seeds a looped highway clip with a counting line already drawn, plus three New York
State DOT traffic cameras published through 511NY (third-party feeds; availability is up to their
owners). To re-add missing demo cameras to a database that isn't empty, run `uv run seed --force`
in `apps/api`.

## Loading bays

A camera watching a bay keeps a *loading session* per vehicle. Set it up from the camera's menu:
**Loading zone** (the corners of where the vehicle stands), **Counting line** across the conveyor
with the arrow pointing into the vehicle, and **Detection** with a model that knows the cargo. A
session opens once a vehicle has stood in the zone for 15 s, every cargo item crossing the line is
added to it, the plate is read from the full-resolution frame while the vehicle is parked, and the
session closes after 60 s with neither vehicle nor cargo in sight. Sessions survive an API
restart and are kept for good; **Loadings** has the history, plate search, totals and an Excel
export, and Telegram can report *vehicle arrived* and *loading completed*.

**Sacks need a model of their own.** COCO has no sack class, and zero-shot detectors did not find
sacks on a moving conveyor. Record footage from the bay, label a few hundred frames with two classes
(`truck` and `sack`), train a YOLO model on it and check its count against loadings counted by hand
with `uv run yardlens-eval`. The procedure, what was already tried and what is still open are in
[docs/sack-model.md](docs/sack-model.md).

## Running on a server

The simplest is Docker: the server, HTTPS and the database in one Compose file, and any number of
worker computers that take cameras off the server with one `docker run` command each. See
[docs/deployment.md](docs/deployment.md). Without Docker, `bun run build` then `bun run start`
runs both apps in production mode; systemd units and a Windows scheduled-task script are in
[`deploy/`](deploy).

## Development

| Command | |
| --- | --- |
| `bun run lint` | ESLint with zero warnings allowed, Ruff lint and format check |
| `bun run typecheck` | `tsc --noEmit`, Pyright in strict mode |
| `bun run test` | `bun test`, pytest (SQLite and a fake detector; no Postgres or weights needed) |
| `bun run format` | Ruff fix and format |
| `bun run gen:api` | Regenerate the dashboard's API types from the running API |
| `bun run bench [video]` | Measure what a camera costs on this machine |
| `uv run yardlens-eval video …` | Count a recording offline and compare with a known count (in `apps/api`) |

Nx caches lint, typecheck and test. On Windows, while `bun run dev` is running, prefix `uv`
commands with `UV_NO_SYNC=1`.

## Documentation

- [Architecture](docs/architecture.md): the pipeline, the design decisions behind it, performance
  and the load governor, project layout.
- [Configuration and API](docs/configuration.md): every environment variable and endpoint.
- [Deployment](docs/deployment.md): Docker, HTTPS, workers on other machines, security, backups.
- [Sack model](docs/sack-model.md): training the detector a loading bay needs.
- [Roadmap](docs/roadmap.md): the architecture work not done yet.

## Limitations

- **One administrator.** There are no other users or roles yet.
- **Throughput is bounded by one model** per process. Past what `bun run bench` reports for your
  machine, add a GPU or workers.
- **Every frame is still decoded.** Give cameras their sub-stream (720p–1080p) where they have one.
- **`object_entered` can over-count** in crowded or fast scenes at low inference rates; line
  crossings are the more reliable number.
- **Camera tests run on the server**, so testing a camera pinned to a worker on another network
  fails from the form while the worker itself still reaches it.
- **YouTube is fragile.** YouTube rate-limits IPs, and yt-dlp needs a JavaScript runtime (Deno,
  Node or Bun) to resolve URLs.

## License

[AGPL-3.0-only](LICENSE), matching Ultralytics YOLO, which YardLens depends on. If you run a modified
version as a network service, you have to offer its source to that service's users. Closed-source or
commercial use needs an [Ultralytics Enterprise License](https://www.ultralytics.com/license) or a
permissively licensed detector in place of YOLO.
