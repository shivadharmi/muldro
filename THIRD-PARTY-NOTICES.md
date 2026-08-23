# Third-Party Notices

Muldro is licensed under the **Apache License, Version 2.0** (see `LICENSE`).

This file lists third-party dependencies whose licenses carry **attribution or
source-disclosure obligations** that Apache-2.0 does not itself satisfy —
principally the copyleft (GPL) and weak-copyleft (LGPL, MPL) families. It is a
notices file, not a full SBOM: permissively-licensed dependencies (MIT, BSD,
Apache-2.0, ISC, Python-2.0) are intentionally omitted, since Apache-2.0
distribution of Muldro already satisfies them.

Nothing in this file changes Muldro's own license. Every dependency below is
used **unmodified**, is consumed through its public interface, and is installed
by a package manager at build time — none of it is vendored into Muldro's source
tree.

Versions are taken from the resolved lockfiles at the time of writing —
`backend/uv.lock` and `frontend/package-lock.json`. Re-verify after any
dependency bump; a lockfile change can silently introduce a new license.

**How each claim below was verified:** Python packages from the installed
`*.dist-info/METADATA` (`License-Expression` / `License` / `Classifier: License`)
plus the bundled license text under `*.dist-info/licenses/`, cross-checked against
`backend/uv.lock`. Node packages from the `license` field of each entry in
`frontend/package-lock.json`, cross-checked against the installed
`node_modules/<pkg>/package.json`. Anything that could not be verified from those
sources is marked **UNVERIFIED** and says so explicitly.

Two categories were confirmed outside the tree, because they are not knowable from
it: the two default model weights were checked against their Hugging Face model
cards (see §"Model weights"), and the Docker image licenses in §"Container images"
come from each project's published licensing rather than from image manifests —
they describe software this project *deploys*, not code it links against, and so
impose no obligation on muldro's own source.

---

## 1. LGPL-3.0 — Python (`psycopg` 3)

| Package | Version | License |
|---|---|---|
| `psycopg` | 3.3.4 | LGPL-3.0-only |
| `psycopg-binary` | 3.3.4 | LGPL-3.0-only |
| `psycopg-pool` | 3.3.1 | LGPL-3.0-only |

`License-Expression: LGPL-3.0-only` is declared in each package's `METADATA`;
the full license text ships in each wheel as
`<pkg>.dist-info/licenses/LICENSE.txt`.

**How Muldro uses it.** `psycopg[binary]>=3.3.4` is a **direct** dependency
declared in `backend/pyproject.toml`. It backs the LangGraph durable
checkpointer: `backend/src/deep_runtime/checkpointer.py` imports
`psycopg.rows.dict_row` and `psycopg_pool.AsyncConnectionPool` to build an
`AsyncPostgresSaver` over a dedicated psycopg3 pool. (`psycopg-pool` is also
pulled transitively by `langgraph-checkpoint-postgres`, and `psycopg-binary` is
an extra of `psycopg` itself.) Muldro's primary SQLAlchemy pool remains on
`asyncpg`; psycopg3 serves only the checkpointer.

**Distribution.** `backend/Dockerfile` runs
`uv pip install --system -r pyproject.toml`, so the `psycopg[binary]` wheel —
which embeds a prebuilt libpq and its dependencies — is present in any published
backend image.

**Obligation.** Muldro imports psycopg through its public Python API and does not
modify it. Under LGPL-3.0 §4/§5 this is "use of a Library" via a documented
interface, so Muldro's own source stays under Apache-2.0. What is required of
anyone **redistributing** a Muldro image or binary artifact is: (a) this notice
and a copy of the LGPL-3.0 text, and (b) that recipients be able to replace the
psycopg version in use — trivially satisfied here, because psycopg is installed
as a separate, replaceable site-packages distribution rather than statically
linked into a Muldro artifact.

**Note.** Should this obligation ever become inconvenient, `psycopg` is only
needed by the checkpointer; there is no LGPL surface elsewhere in the backend.

---

## 2. LGPL-3.0-or-later — Node (`libvips`, via `sharp`, via `next`)

Reached as: `next@16.1.6` → **optional** dependency `sharp@^0.34.4`
(resolved `sharp@0.34.5`, itself Apache-2.0) → optional platform packages.

### 2a. Prebuilt libvips binaries — `LGPL-3.0-or-later`

All at version **1.2.4**:

| Package |
|---|
| `@img/sharp-libvips-darwin-arm64` |
| `@img/sharp-libvips-darwin-x64` |
| `@img/sharp-libvips-linux-arm` |
| `@img/sharp-libvips-linux-arm64` |
| `@img/sharp-libvips-linux-ppc64` |
| `@img/sharp-libvips-linux-riscv64` |
| `@img/sharp-libvips-linux-s390x` |
| `@img/sharp-libvips-linux-x64` |
| `@img/sharp-libvips-linuxmusl-arm64` |
| `@img/sharp-libvips-linuxmusl-x64` |

Ten packages. Each declares `"license": "LGPL-3.0-or-later"` and contains a
prebuilt libvips shared library plus its dependencies.

### 2b. sharp platform packages that *statically embed* libvips

These are `sharp` 0.34.5 platform packages whose declared license expression
already folds in the LGPL component, because libvips is linked into the binary
rather than shipped beside it:

| Package | Version | Declared license |
|---|---|---|
| `@img/sharp-win32-arm64` | 0.34.5 | `Apache-2.0 AND LGPL-3.0-or-later` |
| `@img/sharp-win32-ia32` | 0.34.5 | `Apache-2.0 AND LGPL-3.0-or-later` |
| `@img/sharp-win32-x64` | 0.34.5 | `Apache-2.0 AND LGPL-3.0-or-later` |
| `@img/sharp-wasm32` | 0.34.5 | `Apache-2.0 AND LGPL-3.0-or-later AND MIT` |

The remaining sharp platform packages (`@img/sharp-darwin-*`,
`@img/sharp-linux-*`, `@img/sharp-linuxmusl-*`) are Apache-2.0 and depend on the
matching `@img/sharp-libvips-*` package from §2a at run time.

**Distribution.** `frontend/Dockerfile` builds on `node:20-alpine` and runs
`npm ci`, which installs optional dependencies. On that image (musl, x64) the
installed pair is `@img/sharp-linuxmusl-x64` (Apache-2.0) plus
`@img/sharp-libvips-linuxmusl-x64` (LGPL-3.0-or-later), and both remain in the
final image because the Dockerfile is single-stage and does not prune
`node_modules`. Anyone publishing that image is redistributing an LGPL-3.0
libvips binary.

**Obligation.** Same shape as psycopg: sharp loads libvips as a separate
prebuilt shared library through a stable interface, unmodified, so no
copyleft reaches Muldro's or Next.js's source. Redistributors of the frontend
image should carry this notice and a copy of LGPL-3.0, and be able to point
recipients at the upstream libvips sources
(<https://github.com/libvips/libvips>) and at the `@img/sharp-libvips-*`
packages, which are individually replaceable in `node_modules`.

**Note.** `sharp` is an *optional* dependency of Next.js, used only for
`next/image` optimization. If the LGPL obligation is unwanted, sharp can be
omitted at install time and Next.js falls back to unoptimized images.

---

## 3. MPL-2.0

MPL-2.0 is file-level copyleft: modifications **to the covered files
themselves** must be published under MPL-2.0. Muldro modifies none of these
packages, so the only obligation is attribution and preservation of the notice.

### 3a. Python (`backend/uv.lock`)

| Package | Version | Declared license | Reached via |
|---|---|---|---|
| `certifi` | 2026.2.25 | `MPL-2.0` | `httpx`, `httpcore`, `requests` |
| `orjson` | 3.11.9 | `MPL-2.0 AND (Apache-2.0 OR MIT)` | `langgraph-checkpoint-postgres`, `langgraph-sdk`, `langsmith` |
| `tqdm` | 4.69.0 | `MPL-2.0 AND MIT` | `fastembed`, `huggingface-hub`, `openai` |

All three are transitive. `orjson` and `tqdm` are dual/split-licensed: only part
of each codebase is MPL-2.0 (both ship the MIT/Apache text alongside).

### 3b. Frontend (`frontend/package-lock.json`)

| Package | Version | License | Reached via |
|---|---|---|---|
| `lightningcss` | 1.31.1 | MPL-2.0 | `@tailwindcss/node` (dep), `vite` (peer) |
| `lightningcss-android-arm64` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-darwin-arm64` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-darwin-x64` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-freebsd-x64` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-linux-arm-gnueabihf` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-linux-arm64-gnu` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-linux-arm64-musl` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-linux-x64-gnu` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-linux-x64-musl` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-win32-arm64-msvc` | 1.31.1 | MPL-2.0 | optional platform binary |
| `lightningcss-win32-x64-msvc` | 1.31.1 | MPL-2.0 | optional platform binary |
| `axe-core` | 4.11.1 | MPL-2.0 | `eslint-plugin-jsx-a11y` (via `eslint-config-next`) |

Both are **devDependencies** in the lockfile — `lightningcss` is Tailwind v4's
CSS transformer, `axe-core` is an accessibility rule source for ESLint. Neither
is part of the shipped browser bundle. They are nonetheless *present* in the
frontend Docker image, because `frontend/Dockerfile` runs a plain `npm ci`
(dev dependencies included) and never prunes, so they are listed here for
completeness of what a published image contains.

---

## 4. Model weights are licensed separately from the code (deployer responsibility)

Muldro computes embeddings and reranking locally with **`fastembed` 0.8.0**
(Apache-2.0 — see §6b). The *library* is permissive. The **model weights it
downloads at runtime are not**, and they are not part of this repository.

`fastembed`'s bundled `NOTICE` (`fastembed-0.8.0.dist-info/licenses/NOTICE`)
declares, verbatim:

- `jinaai/jina-colbert-v2` — **CC-BY-NC-4.0**
- `jinaai/jina-reranker-v2-base-multilingual` — **CC-BY-NC-4.0**
- `jinaai/jina-embeddings-v3` — **CC-BY-NC-4.0**
- `vidore/colpali-v1.3` — **Gemma Terms of Use** (<https://ai.google.dev/gemma/terms>)

**CC-BY-NC-4.0 prohibits commercial use.**

Muldro's configured defaults do **not** touch those families
(`backend/src/config/settings.py`):

- `embedding_model` default: `BAAI/bge-base-en-v1.5`
- `reranker_model` default: `Xenova/ms-marco-MiniLM-L-12-v2`

Verified against the Hugging Face model cards on 2026-08-23:

- **`BAAI/bge-base-en-v1.5` — MIT.** The card's License section states
  "FlagEmbedding is licensed under the MIT License." No restriction on
  commercial use.
- **`Xenova/ms-marco-MiniLM-L-12-v2` — no license is declared.** The model card
  carries **no license identifier in its metadata**. It is an ONNX repackaging
  of `cross-encoder/ms-marco-MiniLM-L12-v2`, which *does* declare **Apache-2.0**
  (itself derived from `microsoft/MiniLM-L12-H384-uncased`). So the weights
  descend from Apache-2.0 material, but the redistribution muldro actually
  downloads carries no explicit grant of its own.

> **Open item, stated plainly rather than glossed:** relying on an undeclared
> redistribution is a small but real supply-chain gap. A deployer who needs a
> clean licensing chain for the reranker should point `MULDRO_RERANKER_MODEL` at
> the upstream `cross-encoder/ms-marco-MiniLM-L12-v2` (explicitly Apache-2.0)
> rather than the Xenova ONNX mirror, or vendor the weights with the upstream
> license recorded alongside them.

`fastembed` itself carries no per-model license metadata in its model registry
and caches no weights until first use, so neither of the above can be confirmed
from anything inside this repository — they were confirmed at the source.

**Why this is a deployer responsibility.** Both settings are user-overridable at
runtime via `MULDRO_EMBEDDING_MODEL` and `MULDRO_RERANKER_MODEL`, and weights are
fetched on first use — nothing in the build pipeline pins or vets them. A
commercial deployment that points either variable at a `jinaai/*` model would be
downloading and using CC-BY-NC-4.0 weights in violation of that license, and a
deployment pointed at `vidore/colpali-v1.3` accepts the Gemma Terms of Use.

**If you deploy Muldro commercially, you are responsible for the license of
whatever model you configure.** Muldro's Apache-2.0 license covers its own code
only; it grants you nothing with respect to third-party model weights.

---

## 5. Docker images used by the development stack

`docker-compose.yml` (local development) pulls these images:

| Image | Tag | Upstream license |
|---|---|---|
| `pgvector/pgvector` | `pg17` | PostgreSQL License (permissive) + pgvector PostgreSQL License |
| `redis` | `7-alpine` | Redis 7.x: BSD-3-Clause |
| `qdrant/qdrant` | `v1.17.1` | Apache-2.0 |
| `neo4j` | `5.26-community` | **GPL-3.0** (Community Edition) |
| `minio/minio` | *(unpinned — no tag)* | **AGPL-3.0** |

> ⚠️ **`minio/minio` is specified with no tag**, so it resolves to a floating
> `:latest`. Two consequences: builds are not reproducible, and the AGPL-3.0
> terms in force can change under you between pulls. Pin it to an explicit tag.

**These impose no license obligation on Muldro's own source.** Muldro
communicates with each of these over a network protocol (Postgres wire, RESP,
HTTP/gRPC, Bolt, S3). They are *deployed* services, not linked libraries; no
GPL/AGPL code is combined with Muldro's, and neither GPL-3.0 nor AGPL-3.0 reaches
across a network boundary to a separate program. Muldro remains Apache-2.0.

**It does matter to redistributors of the stack.** Anyone who ships
`docker-compose.yml` as part of a product, or who offers a hosted service built
on these images, inherits the corresponding obligations directly — in particular
**AGPL-3.0 §13** for MinIO, which requires offering the complete corresponding
source of the MinIO server (as modified) to users interacting with it over a
network. Muldro's Apache-2.0 grant does not and cannot waive that.

**`docker-compose.prod.yml` is clean.** It contains only `pgvector/pgvector:pg17`
and `redis:7-alpine` — no GPL or AGPL image. Neo4j, MinIO and Qdrant are
local-development conveniences only.

---

## 6. Documented false positives — do not re-flag these

Both of the following trip naive license scanners. Both were checked against the
actual license text shipped in the wheel. Neither is a copyleft obligation.

### 6a. `docutils` 0.22.4 — **NOT GPL**

**Why it gets flagged.** Its `METADATA` carries
`Classifier: License :: OSI Approved :: GNU General Public License (GPL)`, and
the wheel ships `docutils-0.22.4.dist-info/licenses/gpl-3-0.txt`.

**What is actually true.** `COPYING.rst` (also shipped in the wheel) opens with:

> "Most of the files included in this project have been placed in the public
> domain, and therefore have no license requirements and no restrictions on
> copying or usage"

It then enumerates the exceptions. Every exception is BSD-2-Clause or
BSD-3-Clause **except one**:

> `tools/editors/emacs/rst.el` — copyright by Free Software Foundation, Inc.,
> released under the GNU General Public License version 3 or later

`rst.el` is an **Emacs major mode**, not Python code — and it is **not present in
the installed wheel** (a search of the installed distribution for `rst.el` or any
`*.el` file returns nothing). The `gpl-3-0.txt` file is a license *artifact*
carried along by the packaging, covering a file the wheel does not contain.
`docutils` reaches Muldro three levels deep and build-only:
`cyclopts` → `rich-rst` → `docutils`.

**Verdict: public domain / BSD. No GPL obligation. Do not re-flag.**

### 6b. `fastembed` 0.8.0 — **NOT proprietary**

**Why it gets flagged.** Its `METADATA` carries
`Classifier: License :: Other/Proprietary License`.

**What is actually true.** `METADATA` also carries `License: Apache License`, and
the wheel ships `fastembed-0.8.0.dist-info/licenses/LICENSE` containing the
verbatim **Apache License, Version 2.0**, plus a `NOTICE` attributing the work to
Qdrant. The classifier is upstream packaging noise.

**Verdict: Apache-2.0. Compatible with Muldro. Do not re-flag.**

**But do not dismiss the `NOTICE` along with the classifier** — see §4. The
library is Apache-2.0; some of the *model weights* it can download are not.

---

## Regenerating this file

After any dependency change, re-derive rather than edit by hand:

```bash
# Python — declared license per installed distribution
cd backend/.venv/lib/python*/site-packages
for d in *.dist-info; do
  printf '%-40s ' "$d"
  grep -m1 -E '^(License-Expression|License):' "$d/METADATA" 2>/dev/null \
    || grep -m1 '^Classifier: License' "$d/METADATA" 2>/dev/null \
    || echo '(none declared)'
done

# Node — declared license per lockfile entry
cd frontend
python3 -c "import json;d=json.load(open('package-lock.json'));\
[print(k, v.get('version'), v.get('license')) for k,v in sorted(d['packages'].items()) if v.get('license')]"
```

Then grep the output for `GPL`, `LGPL`, `AGPL`, `MPL`, `CC-BY-NC`, `SSPL`,
`BUSL`, `Proprietary`, and check every hit against the license text actually
shipped in the package — **not** against the classifier. §6 exists because the
classifier lied twice.
