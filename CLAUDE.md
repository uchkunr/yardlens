# YardLens: notes for Claude

- The user writes in Uzbek: answer in Uzbek. Code, comments, commits and docs stay in English; the
  dashboard is Russian by default, with English as the second language.
- API on :8100, dashboard on :3100. `bun run setup` once (and after dependency or schema changes),
  then `bun run dev`, or `bun run build` and `bun run start` for a production-like run.
- Before committing: `bun run lint`, `bun run typecheck`, `bun run test`. On Windows, while
  `bun run dev` is running, `yardlens-api.exe` is locked and `uv` cannot sync: prefix commands
  with `UV_NO_SYNC=1`.
- Commits go straight to `main`; there are no branch rules.
- Docker, HTTPS, workers and the security model: [docs/deployment.md](docs/deployment.md). The
  worker protocol is `apps/api/src/yardlens_api/schemas/worker_protocol.py`; placement, leases
  and epochs are explained at the top of `services/cluster.py`.

## Pending work

- **Sack model, the main logic still to do.** Counting needs a detector trained on sacks; the plan,
  what has already been tried, and the step-by-step procedure are in
  [docs/sack-model.md](docs/sack-model.md). When the user asks for the main logic, the sack model
  or sack counting ("asosiy logic", "qop modeli", "qoplarni sanash"), start from that file.
- The items to close before production are listed at the end of the same file; larger
  architecture work (auth, inference process, WebRTC, migrations, licence) is in
  [docs/roadmap.md](docs/roadmap.md).
