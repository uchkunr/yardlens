# YardLens: notes for Claude

- The user writes in Uzbek: answer in Uzbek. Code, comments, commits and docs stay in English; the
  dashboard is English by default, with Russian as the second language.
- API on :8100, dashboard on :3100. `bun run setup` once (and after dependency or schema changes),
  then `bun run dev`, or `bun run build` and `bun run start` for a production-like run.
- Before committing: `bun run lint`, `bun run typecheck`, `bun run test`. On Windows, while
  `bun run dev` is running, `yardlens-api.exe` is locked and `uv` cannot sync: prefix commands
  with `UV_NO_SYNC=1`.
- Commits go straight to `main`; there are no branch rules.
- Docker, HTTPS and the security model: [docs/deployment.md](docs/deployment.md). The pipeline and
  the design decisions behind it: [docs/architecture.md](docs/architecture.md).

## Scope

YardLens is open source and does two things on one server: it counts line crossings on any camera
(RTSP, HLS, MJPEG, file), and keeps a loading session per vehicle (cargo, plate, arrival and
departure) at a loading bay. Workers on other machines, YouTube sources and per-object
"object entered" events were removed on purpose on 2026-10-23 (they were a third of the code and
nothing the project is for needed them); do not bring them back without asking.

## Pending work

- **Sack model, the main logic still to do.** Counting cargo needs a detector trained on sacks; the
  plan, what has already been tried, and the step-by-step procedure are in
  [docs/sack-model.md](docs/sack-model.md). When the user asks for the main logic, the sack model
  or sack counting ("asosiy logic", "qop modeli", "qoplarni sanash"), start from that file.
- The items to close before production are listed at the end of the same file; larger
  architecture work (roles, a separate inference process, WebRTC, migrations, licence) is in
  [docs/roadmap.md](docs/roadmap.md).
