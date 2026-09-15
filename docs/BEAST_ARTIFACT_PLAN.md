# beast-artifact Plan — a URL for anything the model renders

**Status: DESIGN, DECISIONS LOCKED (2026-09-14). Nothing built.** Max
ratified the three open questions the same day (see the bottom of this
doc): campaign verdicts auto-publish once the CLI exists, `tailnet`
visibility ships in v1 (default `private`), name `beast-artifact` / conf
`BEAST_ARTIFACT=true` / port `:8446` locked. Sibling of
[BEAST_CHAT_PLAN.md](BEAST_CHAT_PLAN.md); same publish/auth conventions,
own port. Build lane = a worktree (`../openbeast-artifact`). Unlike
beast-chat, nothing here touches an eval-era file, so it can merge any
time (see "Known dependencies").

The one-line pitch: what Claude Code's Artifact tool does for a Claude
session, OpenBeast does for its own. The model (or any script on the rig)
hands over an HTML file and gets back a durable URL on the tailnet,
`https://beast:8446/a/<uuid>`. Private to the publisher by default.
Publishing the same artifact again keeps the URL and adds a version.
Opens on a phone. Rendered inside a sandbox so a model-authored page can
never touch the rest of the rig.

## What Claude Code's artifact system actually is (the reference)

Pinned from the live tool contract so we copy the right things and skip
the rest on purpose.

| Claude Code behavior | Copy? | Notes |
|---|---|---|
| HTML file → `https://claude.ai/code/artifact/<uuid>`; private by default; share menu | **yes** | private = owner only; "tailnet" = anyone on the tailnet |
| Redeploy same path → same URL, new version; version labels; version picker | **yes** | versions immutable, `label` optional |
| Page wrapped in a skeleton at publish time: doctype, charset, viewport, small reset, `color-scheme`, `data-theme` stamp | **yes** | author writes `<title>` + `<style>` + body content, no `<html>/<head>` |
| `<title>` from the first 8 KB, `description` as gallery subtitle, emoji `favicon` fixed for life | **yes** | |
| Supporting files (`{"app.js": "dist/app.js"}`), one page + files per version, 16 MB / 64 MB / 255-file caps | **yes** | |
| Own origin per artifact; strict CSP: scripts only from cdnjs / jsdelivr / tailwind / jquery, styles only fonts.googleapis, fonts gstatic, everything else blocked, no fetch, no downloads | **yes, adapted** | we cannot mint origins on a tailnet host, so isolation is iframe `sandbox` + CSP `sandbox` (opaque origin) + `connect-src 'none'` |
| `localStorage` works (per-origin) | **no** in v1 | opaque origin = storage throws; authoring rules say wrap in try/catch anyway |
| `list` / `read` actions | **yes** | tool + CLI + gallery |
| Markdown publish lane | later | Phase 4 |
| Comments, watch/republish notifications, pin, `db`/`user`/`assets` capabilities, ask-Claude | **no** in v1 | comments become interesting only once beast-chat exists (a comment = a steer); noted in Phase 4 |
| Gallery of the user's artifacts | **yes** | mobile-first, read-only in v1 |

## What we verified (ground truth, 2026-09-14)

**Already there, reusable as-is**

| Piece | Where | Note |
|---|---|---|
| Tool surfaces: runner registry (10) vs MCP/WebUI (15) | `agents/tools.py:1727` `_TOOL_REGISTRY`; `agents/mcp_server.py` `@_tool()`; `agents/openapi_tools.py:70` `TOOL_NAMES` | two registries, different counts, different era rules |
| App factory + auth + audit + metrics for a FastAPI tool server | `agents/openapi_tools.py:146 create_app()` | pattern for the artifact server; tests use `TestClient(create_app())` |
| JWT / header identity of the WebUI caller | `openapi_tools.py:132 identity_from()` | gives the publisher's login for ownership |
| Per-user file shards | `openapi_tools.py:186 shard_for()`, `$OPENBEAST_FILES_DIR` 0700 | artifact store lives beside them, not inside them |
| Injective id sanitizer | `openapi_tools.py:86 _sanitize()` | reuse for any user-derived path segment |
| Secret bootstrap idiom | `scripts/lib/conf.sh:241-259`, `scripts/setup-mcpo-keys.sh:40-75` | `openssl rand -hex 32` fallback `od`; conf 0600 |
| Proof-of-locality token | `agents/edge.py:412-460` | the publish route is loopback-only in v1; this is how we prove it |
| Tailnet publish, own port | `scripts/setup-tailscale.sh:158-192` (`--publish-slot`) | `--publish-artifact` occupies the same verb slot |
| Login allowlist read-auth, device-scope write-auth | `docs/BEAST_CHAT_PLAN.md` §Publish + auth | copied verbatim; `ARTIFACT_OPERATORS` defaults to `CHAT_OPERATORS` |
| Stdlib HTML server | `extensions/dashboard/dashboard.py:341-366` | 25 lines; sets only Content-Type and Content-Length |
| Hand-built precedent | `scratch/spare-memory-meta.html` (33 KB, 2026-09-08) | the exact artifact shape, made by hand because no tool exists |

**The gaps**

1. **No HTTP egress from the file shards.** No route anywhere serves a
   file to a browser (`FileResponse`, `StaticFiles`, `Content-Disposition`
   appear nowhere). Users get files out via `read_file` paste or `ls` on
   the rig. "Viewable from a phone" is new surface, not a re-plumb.
2. **No security headers anywhere in the repo.** Zero occurrences of
   `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`.
   Serving model-authored HTML on the tailnet is a new threat class for
   this codebase, and this plan writes the repo's first CSP.
3. **No authoring guidance.** Neither system prompt nor any of the 14
   skills mentions HTML. The model has never been told how to write a
   self-contained page.
4. **No durable ids or versions for anything a model produces.** Files in
   shards are mutable paths; there is no "this exact thing, forever".

**Two facts that shape the design**

- **Era rule.** `evals/cache.py:46-59` hashes `system-prompt.md`,
  `system-prompt-tools.md`, `opencode.json`, `agents/runner.py`,
  `agents/tools.py` into every cache key. A tool registered only in
  `mcp_server.py` + `openapi_tools.py` (like `skill` and the six
  agent-management tools) leaves all five byte-identical. So: **MCP/WebUI
  surface + a CLI, never the runner registry, in v1.** Background agents
  publish through the CLI via their `bash` tool. `docs/TODO.md:29-33`
  independently gates runner-registry additions behind a v5 eval win.
- **One host, one cert.** Claude gives every artifact its own origin.
  MagicDNS gives us one name and no wildcard. Isolation therefore comes
  from the browser sandbox model, not from origins: an opaque-origin
  iframe with a strict CSP. That is a stronger default than Claude's on
  storage (none) and the same on network (none).

## What beast-artifact is and is not

**Is:** a publish-and-view service for model- or script-authored HTML on
the rig: durable UUID URLs, immutable versions, private by default, a
phone-friendly gallery, a tool for chat agents and a CLI for everything
else.

**Is not:** a general file server for shards (a download link for
arbitrary files is a different, smaller feature). Not a hosting service
for the public internet (tailnet only, no funnel). Not Open WebUI's
built-in inline artifacts, which stay off (`docs/TOOLS.md:113`): those are
ephemeral, unshareable, and rendered in the WebUI origin. Not a CMS: no
editing in the browser.

## Architecture

Five pieces, dependency order.

### 1. Store (`$OPENBEAST_FILES_DIR/artifacts/`)

```
artifacts/
  <uuid4>/
    meta.json        # id, owner, title, description, favicon, visibility,
                     # created_at, versions:[{n, ts, label, sha256, bytes, files:[...]}], current
    v1/index.html    # exactly what was handed over (no skeleton baked in)
    v1/files/...     # supporting files at their published paths
    v2/...
  index.jsonl        # append-only publish log: ts, id, n, owner, bytes (for gallery + audit)
```

- Full `uuid4()` (Max: "UUID-ed"); no sortable prefix, the URL should
  not leak creation time. `meta.json` written atomically (mkstemp +
  rename, `clients.sh:154` pattern). Directory 0700.
- Versions are immutable. `current` = highest `n` unless rolled back by
  the CLI. Caps mirror Claude: 16 MB page or text file, 15 MB binary,
  255 files, 64 MB per version. Enforced at publish.
- `visibility ∈ private | tailnet`. Default `private`. There is no
  "public" state; the tailnet is the perimeter.
- Owner = the publisher's identity: WebUI JWT/header login when published
  through the tool; the first `ARTIFACT_OPERATORS` entry when published
  through the CLI on the rig.

### 2. Server (`agents/artifact_server.py`, `:3004`, loopback, FastAPI `create_app()`)

| Route | Purpose |
|---|---|
| `GET /` | gallery: the caller's artifacts (and `tailnet`-visible ones), newest first, title + description + version count + age |
| `GET /a/{id}` | **viewer shell**: title bar, version picker, theme toggle, copy-link; embeds the page in a sandboxed iframe pointing at the raw route |
| `GET /a/{id}/v/{n}` | shell pinned to a version |
| `GET /raw/{id}/v/{n}/` | the artifact document: stored HTML wrapped in the skeleton at serve time, with the isolation headers below |
| `GET /raw/{id}/v/{n}/{path}` | supporting files, content type from the published extension, same headers |
| `GET /api/artifacts`, `GET /api/artifacts/{id}` | JSON listing / meta (versions, sizes, urls) |
| `POST /api/artifacts` | publish: multipart or JSON `{html, title?, description?, favicon?, files?, artifact_id?, label?, visibility?}` → `{id, url, version}` |
| `PATCH /api/artifacts/{id}` | visibility / description / current-version rollback |
| `DELETE /api/artifacts/{id}` | remove (CLI `--yes` only) |
| `GET /api/artifacts/health`, `/metrics` | conventions |

**Skeleton, applied at serve time** (so the stored file stays exactly
what the author wrote): `<!doctype html><html data-theme=…><head><meta
charset><meta viewport><style>:root{color-scheme:light dark}
body{margin:0;font:14px system-ui}img{max-width:100%}[hidden]{display:
none!important}</style></head><body>…</body></html>`. Theme stamp comes
from the shell's toggle via query string; default un-stamped so
`prefers-color-scheme` rules.

**Isolation headers on every `/raw/` response** (the repo's first CSP):

```
Content-Security-Policy:
  sandbox allow-scripts allow-forms allow-modals allow-popups;
  default-src 'none';
  script-src 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com https://code.jquery.com;
  style-src 'unsafe-inline' https://fonts.googleapis.com;
  font-src https://fonts.gstatic.com data:;
  img-src 'self' data: blob:;
  media-src 'self' data: blob:;
  connect-src 'none'; frame-src 'none'; object-src 'none';
  form-action 'none'; base-uri 'none'; frame-ancestors 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cross-Origin-Resource-Policy: same-origin
```

- `sandbox` without `allow-same-origin` gives the document an opaque
  origin: no cookies, no storage, no ability to ride the viewer's tailnet
  identity into Open WebUI on `:443` or any other service. `connect-src
  'none'` closes fetch/XHR/WebSocket. No `allow-downloads`, no
  `allow-top-navigation`. The shell embeds with the same `sandbox`
  attribute so the policy holds even if a header is ever dropped.
- The CDN allowlist is Claude's, verbatim, so pages written for one
  system render in the other.
- Shell and gallery responses carry `frame-ancestors 'none'` and a
  strict `script-src 'self'`; they are ours, not model-authored.

### 3. Tool (`publish_artifact`, `list_artifacts`) on the MCP/WebUI surface

- `agents/mcp_server.py`: two `@_tool()` delegates with rich docstrings
  (the docstring is the WebUI tool description, and it is **not** an
  era file, so the authoring rules live there: write `<title>` +
  `<style>` + body, no `<html>/<head>`; inline all CSS/JS or use the CDN
  allowlist; assets as data URIs; no fetch; wrap storage in try/catch;
  design both themes via tokens; pick an emoji favicon once).
- `agents/openapi_tools.py`: `TOOL_NAMES` 15 → 17; not in `GUEST_TOOLS`
  (guests get 404, as for every non-web tool).
- Implementation in a new module `agents/artifact.py` (store + client
  helpers) so `agents/tools.py` stays byte-identical. The tool POSTs to
  the server on loopback with the locality token; the WebUI caller's
  identity (`identity_from`) becomes the owner.
- `publish_artifact(path, title="", description="", favicon="",
  artifact_id="", label="", files="", visibility="private")` → returns
  `Published <title> → https://beast:8446/a/<id> (v3)`. Same
  `artifact_id` = same URL, new version. Returns a string, never raises
  (tool contract).
- `list_artifacts(limit=25)` → one line per artifact: title, url,
  versions, visibility, updated.

### 4. CLI (`scripts/artifact.sh`)

`publish <file.html> [--title] [--description] [--favicon] [--id]
[--label] [--file published=source]… [--visibility tailnet]`, `list`,
`show <id>`, `versions <id>`, `rollback <id> <n>`, `visibility <id>
private|tailnet`, `remove <id> --yes`. Talks to `:3004` with the locality
token. This is how background agents (via `bash`), campaign scripts, and
Max publish without touching the runner registry. First planned consumer:
the T1.17 verdict scripts publish their tables as pages when they land.

### 5. Publish + auth (`setup-tailscale.sh --publish-artifact`)

- `tailscale serve --bg --https=8446 http://127.0.0.1:3004`, own
  `--unpublish-artifact`, same MagicDNS/cert pre-checks.
- **Read auth = tailnet identity**, copied from beast-chat:
  `Tailscale-User-Login` must be in `ARTIFACT_OPERATORS` (defaults to
  `CHAT_OPERATORS`). Unlisted login → 404 everywhere. Listed login sees
  the gallery, their own artifacts, and any `tailnet`-visible artifact.
  A `private` artifact of another owner → 404, never 403.
- **Write auth = loopback only in v1.** Publish, patch, delete require the
  locality token. Nothing on the phone path can create or change an
  artifact; it can only view. Remote publish (device key with `artifact`
  scope) is a one-line addition later and deliberately not in v1.
- Audit: `.run/artifact-audit.jsonl` (`ts, login, route, id, n, outcome,
  ms`). Publish rows log sha256 + bytes, never content.
- Not behind beast-gate (same reasoning as beast-chat and beast-slot).

### Integration points

- **Open WebUI**: the tool's return string contains the URL; WebUI renders
  it as a link. No WebUI configuration change.
- **beast-chat**: a `publish_artifact` result or a CLI publish inside a
  session shows up in the transcript; `chat_server` surfaces
  `GET /api/chat/sessions/{id}/artifacts` by scanning for the URL prefix.
  Later, an artifact comment can become a steer (Phase 4).
- **Dashboard extension**: one line, "artifacts: N, last published …".
- **doctor.sh / healthcheck.sh**: probe `:3004/api/artifacts/health`.

## Phases

| Phase | Scope | Est. | Ships as |
|---|---|---|---|
| 0 | Store module (`agents/artifact.py`), server with `create_app()`, raw + shell routes, skeleton, **CSP + sandbox**, caps, tests incl. a header-assertion test and a "page cannot fetch / cannot reach parent" test via TestClient | 1 day | PR A |
| 1 | `publish_artifact` + `list_artifacts` on MCP/WebUI, `TOOL_NAMES` 17, count pins updated (`test_mcp_allowlist.py:43,55`, `test_identity_server.py:5,72`), `scripts/artifact.sh`, `BEAST_ARTIFACT=true` conf, `start.sh` wiring, doctor row | ½ day | PR A |
| 2 | Gallery + viewer shell: version picker, theme toggle, copy link, mobile layout; favicon/title/description handling | ½ day | PR B |
| 3 | `--publish-artifact` on :8446, `ARTIFACT_OPERATORS`, visibility, audit, docs: `BEAST_ARTIFACT.md`, `FEATURES.md` bullet, `TOOLS.md` "15" → "17" sweep (`TOOLS.md:7,123,156`, `FEATURES.md:17`, `ARCHITECTURE.md:27,111,192,229`, `REFERENCE.md:56,387,484`, `RBAC_PLAN.md:7,90,111,169`, `README.md:16,279,301`, `setup-mcpo-keys.sh:5`), `REMOTE_ACCESS_PLAN.md` note | ½ day | PR B |
| 4 (optional) | Markdown lane (`.md` → rendered page, same skeleton); `publish-a-page` skill (**rolls the eval era**: `system-prompt-tools.md` regenerates — land at a campaign boundary with beast-chat PR A); comments → beast-chat steer bridge; remote publish via `artifact` device scope | 1 day | PR C |

Total: about 2½ working days for v1 (Phases 0–3), GPU-free.

## Verification checklist

- [ ] Publish `scratch/spare-memory-meta.html` from the CLI: URL returned,
      opens on the phone over the tailnet, renders identically to the file
      opened locally.
- [ ] Publish again with the same id: same URL, `v2`, picker shows both,
      `/a/<id>/v/1` still renders the first.
- [ ] A test page that tries `fetch('https://beast/')`, `localStorage`,
      `top.location`, `<a download>`, and `document.cookie`: every attempt
      fails inside the sandbox; the shell and gallery are unaffected.
- [ ] A page loading Chart.js from cdnjs and a Google font renders; the
      same page pointing at unpkg gets a blocked script (matches Claude).
- [ ] Unlisted tailnet login: 404 on gallery, shell, raw. Listed
      non-owner: 404 on a private artifact, 200 after
      `artifact.sh visibility <id> tailnet`.
- [ ] `POST /api/artifacts` from the tailnet without the locality token:
      404. From loopback with it: 201.
- [ ] Caps: 17 MB page rejected with a clear message; 256th file rejected.
- [ ] `agents/tools.py`, `runner.py`, `system-prompt*.md`, `opencode.json`
      byte-identical before/after PR A + B (`evals/cache.py context_hash()`
      unchanged; assert in a test).
- [ ] `tests/run_tests.sh` + CI green; tool counts 17 everywhere the
      sweep lists; doctor shows the artifact row.
- [ ] Audit file 0600, no HTML bodies inside.

## Out of scope (deliberately)

- Public-internet sharing (funnel), share-by-token links, expiring links.
- Per-artifact origins / wildcard certs.
- Runtime capabilities (shared db, user identity, asset uploads,
  ask-the-model) inside pages.
- Browser-side editing, comments UI, pinning, watch notifications (v1).
- Serving arbitrary shard files (a separate "download link" feature).
- `localStorage` inside artifacts (opaque origin). Revisit only if a real
  page needs it; the fix is `allow-same-origin` on a second, dedicated
  port, never on `:8446`.

## Known dependencies / caveats

- **Era:** none. PR A/B touch no `CONTEXT_FILES`, so they can merge and be
  pulled at any row boundary. The Phase 4 skill is the one exception
  and is scheduled with beast-chat PR A.
- **The isolation is only as good as the headers.** A regression test
  must pin the exact CSP string on `/raw/` and the `sandbox` attribute in
  the shell; both belong in `tests/test_artifact_server.py` from day one.
- `Tailscale-User-Login` is forgeable by local processes (inside the
  existing loopback trust model). Reads only; writes need locality.
- Two published ports appear in quick succession (`:8445` chat, `:8446`
  artifact). `setup-tailscale.sh` gains a table of mounts and a
  `--status` printout so this stays legible.
- Tool count moves 15 → 17 across ~20 prose sites; the sweep list above
  is exhaustive as of today, and `test_identity_server.py:72` will catch
  the code side.
- Open WebUI may in future enable its own inline artifacts; irrelevant to
  this design, but the tool description should say "durable, shareable
  URL" so the model picks ours when a link is what's wanted.

## Decisions (Max, 2026-09-14)

1. **Campaign verdicts auto-publish.** Once `scripts/artifact.sh` exists,
   the T1.17 capability verdicts, the greedy-floor verdict, and the Tier-3
   verdict each publish their table as a page (one line per script, in
   Phase 1's PR). Stable ids per verdict so reruns become versions.
2. **`tailnet` visibility ships in v1.** Default stays `private`;
   `artifact.sh visibility <id> tailnet` and the tool's `visibility`
   parameter flip it.
3. **Name, conf key, port: locked.** `beast-artifact`,
   `BEAST_ARTIFACT=true`, `:8446` (`--publish-artifact` /
   `--unpublish-artifact`). Loopback server on `:3004`.
