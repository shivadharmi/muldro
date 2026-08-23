# Contributing to Muldro

Muldro is pre-launch and maintained by a solo founder. Contributions are welcome, but the
project moves fast and architectural decisions are already made in a lot of places — open an
issue and agree on the shape of a change before writing a large patch.

## The binding standard

**[`docs/engineering-standards.md`](docs/engineering-standards.md) is the binding rulebook for
all contributions, including AI-assisted ones.** Read it before you write code. It covers:

- One-way dependencies (`api → services → {models, contracts}`), the frozen god objects, typed
  boundary contracts
- Which OOP patterns are used deliberately and which are banned
- File size caps: **800 lines** (Python), **400** (React components), **200** (Zustand stores),
  ~50 lines per function. These are enforced by a pre-commit hook (`scripts/check_file_size.py`),
  not by review. Pre-existing oversized files are grandfathered by exact line count and **must
  not grow**.
- Refactoring process: characterization tests first, one mechanical move per commit, structure
  and behavior never in the same commit
- The view-layer side-effect rule
- Open-source hygiene: no secrets anywhere, conventional commits, no attribution trailers

This file does not repeat any of that. It covers the mechanics: environment, tests, commits, PRs.

Architecture background lives in [`docs/architecture/`](docs/architecture/README.md) — start with
`overview.md`, then `message-flow.md` and `execution.md`. [`CLAUDE.md`](CLAUDE.md) at the repo root
is the densest single description of the system (agent boundaries, capability routing, the trust
and permission gates, the view layer) and is kept current.

## Prerequisites

- Docker + Docker Compose
- Python **3.12** (`requires-python = ">=3.12"`; CI and the backend Dockerfile both use 3.12)
- Node.js 22 (CI pins 22)
- [`uv`](https://github.com/astral-sh/uv) for the Python environment
- An Anthropic API key for anything that actually calls a model. Unit tests mock the model layer
  and do not need a real key.

## Development setup

### 1. Infrastructure

Bare `docker compose up` starts **infrastructure only** — Postgres 17, Redis 7, MinIO, Qdrant,
Neo4j. That is what you want for the native dev loop.

```bash
docker compose up -d
```

(`docker compose --profile app up` additionally builds and runs the API and frontend containers.
Use that to check the packaged stack, not to develop against.)

### 2. Backend

All backend commands run from `backend/`.

```bash
cd backend
uv venv .venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env          # then edit: at minimum MULDRO_ANTHROPIC_API_KEY
alembic upgrade head
python run.py                 # API only, port 8000
python run.py --worker        # API + background worker (StreamConsumer + Scheduler)
```

Configuration is all environment variables with a `MULDRO_` prefix, read by pydantic-settings in
`src/config/settings.py`. `.env.example` lists the full set; `.env.minimal` at the repo root is
the smallest working configuration.

Two escape hatches you will likely need locally:

- `MULDRO_SKIP_GATEWAY_VALIDATION=true` — the API lifespan registers OAuth configs with the
  OpenConnector gateway and **aborts startup** if that fails. Set this if you are not running a
  gateway. (`tests/conftest.py` sets it for you.)
- `MULDRO_SKIP_REGISTRY_VALIDATION=true` — disables the tool-registry startup cross-checks.

### 3. Frontend

All frontend commands run from `frontend/`.

```bash
cd frontend
npm install
npm run dev                   # port 3000
```

## Running tests

### Backend — read this before running the suite

**The backend test suite talks to a live Postgres.** Tests whose filenames end in `_db.py` (and
several others) build a real engine against `MULDRO_DATABASE_URL`, insert rows, and delete them
again. They `skipif` when Postgres is unreachable, so a full run against your development database
will silently write to and delete from it.

Two consequences:

1. **Point `MULDRO_DATABASE_URL` at a disposable database**, not one holding anything you care
   about. The compose Postgres with a throwaway database name is the easy option:

   ```bash
   docker compose exec postgres createdb -U muldro muldro_test
   MULDRO_DATABASE_URL=postgresql+asyncpg://muldro:muldro@localhost:5432/muldro_test \
     pytest tests/test_entity_resolver_db.py -v
   ```

   Run `alembic upgrade head` against that database once before using it.

2. **Run targeted test files, not the whole suite**, while iterating. A full run is slow and
   touches shared state. If a test goes red unexpectedly, check the database's actual state before
   believing the failure.

```bash
# from backend/, venv active
pytest tests/test_planner.py -v                 # single file  (preferred while iterating)
pytest tests/test_planner.py::test_name -v      # single test
pytest tests/ -v -k "planner"                   # keyword filter

# what CI runs
MULDRO_ANTHROPIC_API_KEY=ci-placeholder-not-a-real-key \
  pytest tests/ -v --ignore=tests/e2e --ignore=tests/eval --ignore=tests/golden -x
```

`tests/e2e`, `tests/eval` and `tests/golden` require a live backend or real model calls and are
excluded from CI. Do not add tests that need either to the default suite.

Test files mirror the `src/` layout. Use `make_mock_settings()` from `tests/conftest.py` for
Settings. `conftest.py` installs its own async test hook, so **write new async tests unmarked** —
do not add `@pytest.mark.asyncio` unless you have a reason.

### Frontend

```bash
cd frontend
npm run test          # vitest run
npm run test:watch
npm run lint          # eslint  (CI runs this)
npm run build         # CI runs this too — a build failure fails CI
```

## Lint and format

```bash
# backend, from backend/
ruff check src/ tests/
ruff check src/ tests/ --fix
ruff format src/ tests/
ruff format --check src/ tests/     # what CI asserts
```

ruff config lives in `backend/pyproject.toml`: line length 100, target `py312`, rules `E, F, I, N, W`.
CI fails on either `ruff check` or `ruff format --check`.

## Pre-commit hooks

Install them once, from the repo root:

```bash
pre-commit install
```

The hooks (`.pre-commit-config.yaml`) are: `ruff` + `ruff-format` (backend only), **gitleaks**
secret scan, `detect-private-key`, `check-added-large-files` (500 KB), `check-merge-conflict`, and
the local file-size cap script.

To run them by hand, **scope them to the files you touched**:

```bash
pre-commit run --files backend/src/services/planner.py backend/tests/test_planner.py
```

Do **not** run `pre-commit run --all-files`. The repository has grandfathered oversized files and
`--all-files` produces a wall of unrelated output that hides your own failures.

If the file-size hook rejects your change, splitting by responsibility is the fix. Raising a cap
or adding a grandfather entry requires changing `docs/engineering-standards.md` first — see rule
§1 there: "When a rule blocks you, the rule wins until changed here first."

## Database migrations

Schema changes go through Alembic only, from `backend/`:

```bash
alembic revision --autogenerate -m "add prepared_action columns"
alembic upgrade head
```

Review the generated migration by hand before committing — autogenerate misses server defaults,
index changes and type narrowings. Every data table is workspace-scoped: new tables need a
`workspace_id` NOT NULL FK to `workspaces` unless they belong to the small user-level set
(`users`, `workspaces`, `workspace_members`, `sessions`, `magic_links`).

## Commits

Conventional commits, imperative mood:

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

**No attribution trailers.** No `Co-Authored-By` lines.

Structure and behavior do not change in the same commit. Each commit should leave the tests green
so the history stays bisectable.

## Pull requests

- Branch off `main`. `main` is the only supported branch.
- Fill in the PR template: summary, linked issue, type of change, test plan with the actual
  commands you ran, checklist.
- Keep PRs scoped. No drive-by refactors outside the declared scope of the change.
- New feature code ships with tests. Coverage is a target (80%), not a hard gate, but tests for
  new features are required at review.
- **Docs only change when an architectural fact changes** — a component added or removed, a
  changed contract, invariant or dependency, a renamed concept. Adding a file, tool, migration or
  test requires no doc change. Never record counts, line numbers as identity, or file inventories;
  they rot within days. See `docs/engineering-standards.md` §8 and the "Documentation Maintenance"
  section of `CLAUDE.md`.
- If your change alters an architectural fact, update the relevant `docs/architecture/` page in
  the same PR.
- Once released, `/v1/` routes are a public contract: breaking changes need versioning.

CI must be green: backend lint, backend format check, backend tests, frontend lint, frontend build.

## Reporting bugs and requesting features

Use the issue templates. Blank issues are disabled.

**Do not report security vulnerabilities in a public issue.** See [`SECURITY.md`](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Report unacceptable behavior
to shivadharmi@gmail.com.

## License

Muldro is Apache-2.0. By contributing you agree that your contributions are licensed under the
same terms.
