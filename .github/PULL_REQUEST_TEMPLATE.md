# Summary

<!-- What changed and why. If this fixes a bug, state the root cause, not just the symptom. -->

Closes #

## Type of change

<!-- Mark one. It should match the conventional-commit type on your commits. -->

- [ ] `feat` — new behaviour
- [ ] `fix` — bug fix
- [ ] `refactor` — structure only, no behaviour change
- [ ] `perf` — performance
- [ ] `docs` — documentation only
- [ ] `test` — tests only
- [ ] `chore` / `ci` — tooling, dependencies, pipelines

## Architectural impact

<!--
Does this change an architectural fact — a component added or removed, a changed contract,
invariant or dependency, a renamed concept? If yes, name it and say which docs/architecture/
page you updated. If no, write "none" and leave the docs alone.
-->

none

## Test plan

<!-- The commands you actually ran, and their outcome. Not the ones you intended to run. -->

```bash
# backend, from backend/ with the venv active, MULDRO_DATABASE_URL pointed at a disposable DB
pytest tests/<file> -v
ruff check src/ tests/
ruff format --check src/ tests/

# frontend, from frontend/
npm run test
npm run lint
npm run build
```

<!-- Manual verification, if this touches a surface, a connector, or an execution path. -->

## Checklist

- [ ] Commits follow conventional commits (`<type>: <description>`) with **no** `Co-Authored-By`
      or other attribution trailers
- [ ] Structure and behaviour are not mixed in the same commit; each commit leaves tests green
- [ ] `ruff check src/ tests/` and `ruff format --check src/ tests/` are clean
- [ ] Targeted tests for the changed code were run and pass (full-suite runs touch a live
      Postgres — see CONTRIBUTING.md)
- [ ] New feature code ships with tests
- [ ] Change respects `docs/engineering-standards.md`: one-way dependencies, typed boundary
      contracts, no new methods on the frozen god objects, file size caps
- [ ] `pre-commit run --files <changed paths>` passes (including the file-size cap and gitleaks)
- [ ] Docs updated **only** if an architectural fact changed; no counts, line numbers or file
      inventories added
- [ ] Schema changes go through Alembic, the generated migration was reviewed by hand, and new
      tables are `workspace_id`-scoped
- [ ] No secrets, tokens, keys or real message contents in the diff, the tests, the fixtures or
      this description
- [ ] Scope is limited to the stated change — no drive-by refactors
