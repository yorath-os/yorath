# Build from source

Yorath is developed in a private repository and published to `yorath-os/yorath` release by release; pull requests are not accepted (see [CONTRIBUTING](../CONTRIBUTING.md)). You can still build and run everything from a release.

## Run locally

You need Docker 29+, uv, Node 24.

```bash
make up          # production profile: builds images, starts db, ollama, core, web
open http://127.0.0.1:8080   # status page
curl http://127.0.0.1:8000/health
make down        # stop; make down-v also removes the data
```

`make up-dev` starts the core with reload from source and the vite dev server. `make test`, `make lint`, `make check-manifest` are the same as in CI.

Core integration tests (model, journal, migrations) run against a live Postgres: set `YORATH_DATABASE_URL` (for example, `postgresql+asyncpg://yorath:yorath@127.0.0.1:5432/yorath` with the Compose `db` running), the suite fails without a database, it never skips; the tests create and use a separate `<db>_test` database on the same server.

`make test` runs the core unit tests, the core suite on a live Postgres and the web tests; `make lint` runs ruff, mypy, eslint and `tsc`.

Core maintenance commands: `yorath create-user --login <login> --role owner` (password from stdin) creates a user, `yorath sync-agents` creates agents and revisions from the md files in `agents/`, `yorath sync-policies` loads the built-in Cedar policies into the `policies` table (owner edits are kept), `yorath purge-traces` deletes traces older than `journal.trace_retention_days` from the manifest.

## Layout

| Directory | What |
|---|---|
| `core/` | Python 3.14 / FastAPI core: packages by plane `yorath.control`, `yorath.runtime`, `yorath.enforcement`, the extension point `yorath.plugins`, the manifest and self-diagnostics `yorath.distribution` |
| `web/` | React + TypeScript + Vite: setup wizard, Desk, Chat, Agents, Journal, Data and the other screens |
| `installer/` | Docker Compose (`compose.yaml` + `compose.dev.yaml`), Dockerfiles, `render-env.py` |
| `agents/` | Built-in md agents |
| `enterprise/` | Empty by design: the Core / Enterprise boundary |
| `distribution.yaml` | The single source of component versions, the hardware matrix, Ollama limits |
