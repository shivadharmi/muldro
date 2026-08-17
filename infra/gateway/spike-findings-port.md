# OpenConnector Port-Remap Spike (Task 0)

**Status:** DONE (throwaway POC, no production code touched)
**Date:** 2026-08-17
**Image:** `ghcr.io/oomol-lab/open-connector:v1.3.5` (same digest as `spike-findings.md`/`spike-findings-connect.md`)
**Scratch container:** `oc-portspike` — torn down (`docker rm -f`) after the spike, confirmed via `docker ps -a` (no rows).

Gates the decision of how to move OpenConnector (OC) off host port 3000 (needed for the
Next.js frontend) without breaking Gmail's OAuth `redirect_uri`.

## Question

When OC runs mapped `-p 3001:3000` (host 3001 → container-internal 3000), does the
`expectedRedirectUri` OC emits from `PUT /api/oauth/configs/gmail` reflect the **host** port
(3001, request-Host-derived) or the **container-internal** port (3000, fixed)?

## Method

1. `docker run --rm -d --name oc-portspike -p 3001:3000 -e OOMOL_CONNECT_ENCRYPTION_KEY=<throwaway hex32> -e OOMOL_CONNECT_ADMIN_TOKEN=<throwaway> -e OOMOL_CONNECT_RUNTIME_TOKEN=<throwaway> ghcr.io/oomol-lab/open-connector:v1.3.5`
2. Waited for `"connect server listening","url":"http://0.0.0.0:3000"` in the container log.
3. `curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:3001/oauth/callback` → **400** (route reachable through the port mapping, confirms Docker's forwarding works fine).
4. `PUT http://localhost:3001/api/oauth/configs/gmail` with the admin bearer token and a dummy
   `clientId`/`clientSecret` (`dummy-client-id.apps.googleusercontent.com` / `dummy-client-secret`
   — no real Google credentials used or needed).
5. Read `expectedRedirectUri` from the response.
6. Probed `docker exec oc-portspike env | grep -iE "url|host|port|base|public|callback"` for a
   base/public-URL override.
7. Re-ran the container with `-p 3001:3001 -e PORT=3001` (internal listen port changed to match
   the host port 1:1) and repeated the `PUT /api/oauth/configs/gmail` call to see whether
   `expectedRedirectUri` then reflected `:3001`.
8. Torn down: `docker rm -f oc-portspike`.

## Result: **Branch B** — `expectedRedirectUri` stays fixed at the container-internal port

With `-p 3001:3000` (host 3001 → container-internal 3000, no other env changes):

```json
{
  "service": "gmail",
  "configured": true,
  "clientId": "dummy-client-id.apps.googleusercontent.com",
  "expectedRedirectUri": "http://localhost:3000/oauth/callback",
  ...
}
```

Exact string observed: **`http://localhost:3000/oauth/callback`** — even though the `PUT`
request itself was sent to `http://localhost:3001/...` (curl's default `Host: localhost:3001`
header on that request). OC does **not** derive the redirect URI from the inbound request's
Host header/port. It is fixed at OC's own internally-known listen port.

## Base/public-URL env probe

`docker exec oc-portspike env | grep -iE "url|host|port|base|public|callback"` on the running
container returned only:

```
PORT=3000
HOST=0.0.0.0
```

No `PUBLIC_URL`, `BASE_URL`, `CALLBACK_URL`, `PUBLIC_HOST`, or similar override variable exists.
`docker run --rm ghcr.io/oomol-lab/open-connector:v1.3.5 --help` only printed Node.js's own
built-in `--help` output (the entrypoint script does not intercept `--help` with app-specific
flags) — no additional base-URL flag surfaced there either.

**However**, OC's built-in `PORT` env var (documented in `spike-findings.md` §1 as a built-in
default, `PORT=3000`) turns out to double as the value OC uses to construct the redirect URI —
not just the internal listen port. Re-running with `-e PORT=3001` **and** `-p 3001:3001` (host
and container both on 3001, so the container's own listen port matches):

```json
{
  "service": "gmail",
  "configured": true,
  "clientId": "dummy-client-id.apps.googleusercontent.com",
  "expectedRedirectUri": "http://localhost:3001/oauth/callback",
  ...
}
```

`expectedRedirectUri` became **`http://localhost:3001/oauth/callback`** — confirming `PORT` (not
the Docker port mapping, not a dedicated base/public-URL var) is the lever.

**Important nuance:** a `-p 3001:3000` remap alone (container still internally listening on
3000, `PORT` unset) does **not** fix this — Docker happily forwards traffic, but OC still
computes and emits `:3000` in `expectedRedirectUri`/the Google consent `redirect_uri`. Google
would then redirect the browser to `http://localhost:3000/oauth/callback`, which is occupied by
the Next.js frontend, not OC — a real `redirect_uri_mismatch`/dead-callback failure, invisible
until a live OAuth consent is attempted. The container's internal `PORT` must be changed to
`3001` (with a matching `-p 3001:3001` mapping, not a `3001:3000` remap) for the emitted
`redirect_uri` to actually resolve to a listening OC instance.

## Recommendation

Run OC with `PORT=3001` and `-p 3001:3001` (host and container both on 3001) to free host port
3000 for the Next.js frontend, rather than remapping via `-p 3001:3000` — a straight port remap
leaves OC still emitting `:3000` in the OAuth `redirect_uri`, which breaks the Google consent
callback even though the host port itself is reachable.
