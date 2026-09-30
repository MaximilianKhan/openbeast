# beast-artifact — a URL for anything the model renders

**Status: shipped in v1.3.0 (2026-09-14); this page describes `main` as of
2026-09-30.** Since v1.6.0: one owner for everything the rig publishes, admins,
managing pages from a phone, per-version delete and opt-in retention, links
back to the beast-chat session that made a page, and a paged, searchable
gallery. Upgrading a rig that has operators configured? Read
[UPDATING.md](UPDATING.md) first. Opt-in (`BEAST_ARTIFACT=true`), loopback
server on `:3004`, published to the tailnet on `:8446`. Design, decisions and
the phase list live in [BEAST_ARTIFACT_PLAN.md](BEAST_ARTIFACT_PLAN.md); this
page is how you use it, and wins where the two differ.

## The concept

A model can already write you a page. What it cannot do is *give you the
link*. Output ends up pasted into a chat window, or written to a file on the
rig that you then have to go and find, and either way it is gone the moment
the conversation scrolls.

**beast-artifact takes a self-contained HTML file and hands back a durable
URL on your tailnet.** Publish the same artifact again and the URL stays the
same — you get a new version behind it. Open it on your phone. Send it to
another device you own. It is private to you until you say otherwise.

```
PUBLISHERS (all on the rig, all loopback)         VIEWERS (anything on the tailnet)

  publish_artifact  ─┐                              📱 phone / tablet
  (WebUI + OpenCode) │                              💻 any browser
                     │                                      │
  scripts/artifact.sh├──▶ artifact server :3004 ◀── tailscale :8446
  (CLI, scripts,     │      store · versions              read-only
   background agents)│      gallery · viewer shell
                     │            │
  campaign verdicts ─┘            ▼
                        $OPENBEAST_FILES_DIR/artifacts/<uuid>/
                          meta.json · v1/ · v2/ · v3/   (0700)

  publish: loopback only, proof-of-locality token
  manage:  the token, OR tailnet login + an `artifact`-scoped device key
           (pin, tags, share, rollback, delete — never publish)
  read:    tailnet identity, ARTIFACT_OPERATORS allowlist
```

The load-bearing fact: **the page runs in a sandbox with an opaque origin.**
A model wrote it, so it is treated as hostile — it has no cookies, no
storage, no `fetch`/`XHR`/`WebSocket`, no subresource from a host outside the
allowlist, and no way to reach the viewer's session on Open WebUI or anything
else on your tailnet. What it is *not* is unable to transmit at all: no CSP
directive governs a document navigating **itself**, so a raw page opened as a
top-level document can still reach an external URL by setting `location` or
opening a popup. That is exactly why the page is boxed in an opaque origin
and treated as hostile rather than trusted. See
[The security posture](#the-security-posture), which is the section worth
reading twice.

## Quickstart

```bash
echo "BEAST_ARTIFACT=true" >> openbeast.conf
echo "ARTIFACT_OPERATORS=you@example.com" >> openbeast.conf   # your tailnet login
./stop.sh && ./start.sh -d

./scripts/artifact.sh publish page.html --title "Weekly numbers"
#   → http://localhost:3004/a/3f1c9e4a-77b2-4d0e-9a51-0d2e6b8c4411  (v1)
```

The link printed before the port is published is a **loopback** URL, and no
browser can open it: the viewer refuses a caller that presents no tailnet
login, and a browser on the rig presents none (`artifact.sh list/show` works
right away). `ARTIFACT_OPERATORS` is what lets *you* open the rig's private
pages from a phone: everything the rig publishes is owned by the rig, and the
first login on that list administers it. Leave it out and no tailnet login can
open a private page the rig published — `publish` warns you every time until
it is set. Then publish the port once:

```bash
./scripts/setup-tailscale.sh --publish-artifact      # needs sudo
```

From then on every URL is the tailnet form
`https://beast.<tailnet>.ts.net:8446/a/<id>`, and it opens on the phone and
in the rig's own browser. Without `ARTIFACT_OPERATORS`, private pages (the
default) open for nobody from a phone. `./scripts/doctor.sh` warns about
that and about a published `:8446` with no server behind it.

In chat, the same thing is one sentence: *"write me a page showing X and
publish it."* The model calls `publish_artifact` and answers with the link.

## The URL and version model

| Path | What it serves |
|---|---|
| `/` | the gallery — pinned first, then newest; `?page=N`, `?q=`, `?tag=`, `?session=` (see *The gallery*) |
| `/a/<id>` | the **viewer shell** at the current version |
| `/a/<id>/v/<n>` | the shell pinned to version `n` |
| `/raw/<id>/v/<n>/` | the artifact document itself, sandboxed (this is what the shell frames) |
| `/raw/<id>/v/<n>/<path>` | a supporting file of that version |
| `/raw/<id>/v/<n>/~<token>/…` | the same document and files under a **capability path** — what the shell actually frames (see *Why supporting files need a token* below) |
| `GET /api/artifacts` | JSON listing: `limit` (1–200, default 25), `offset`, `q`, `tag`, `session`, `owner`; the answer carries `total` |
| `GET /api/artifacts/<id>` | one artifact's meta and version list |
| `PATCH /api/artifacts/<id>` | manage: `current` (rollback), `description`, `pinned`, `tags`, `visibility`, `owner` |
| `DELETE /api/artifacts/<id>` | delete the page and every version |
| `DELETE /api/artifacts/<id>/v/<n>` | delete one old version (never the current one, never the last) |
| `POST /api/artifacts` | publish — the locality token only |

- **The id is a full `uuid4`.** No sortable prefix and no slug: the URL
  should not leak when a thing was made or what it is about.
- **`/a/<id>` follows the current version; `/a/<id>/v/<n>` never moves.**
  Send someone the plain link when you want them to see your latest thinking,
  the pinned link when you are citing a specific result.
- **Versions are immutable.** Publishing with the same `--id` writes `v(n+1)`
  and leaves every earlier version byte-identical on disk. Nothing overwrites
  a published version, ever — `rollback` only moves the `current` pointer.
- **A page holds at most 200 versions.** The 201st publish is refused, and
  the error points at `artifact.sh prune <id> --keep N --yes`, which deletes
  old versions and keeps the URL (see *Deleting, pruning and retention*).
- The shell's version picker lists every version with its optional `--label`;
  choosing one navigates to that pinned URL.
- **The stored file is exactly what you handed over.** The doctype, charset,
  viewport and a small reset are wrapped around it *at serve time*, so what
  comes back out of `v1/index.html` is what the author wrote.

## The gallery

`/` lists what you may open: 100 rows a page, **pinned pages first**, then
newest first, with "*first*–*last* of *total*" and newer/older links. Each row
shows the title, favicon, description, age, visibility, "v*current* of
*count*", and the page's tags as chips. An admin also sees each page's owner
when it is not them.

The filter box narrows the rows on the current page as you type. **Enter**
searches everything on the server (`?q=`, a case-insensitive substring of the
title, description, id and tags, 100 characters max). `?tag=<tag>` and
`?session=<id>` filter the same way; a tag chip in the viewer links to its
filter, and an active filter shows above the list with a *clear* link. A
filter that matches nothing says so instead of pretending the store is empty.

## Visibility

Two states, and there is no third:

| `visibility` | Who can open it |
|---|---|
| `private` (default) | its owner, and the rig's admins |
| `tailnet` | any login in `ARTIFACT_OPERATORS` (any identified login when the list is empty) |

### Who owns a page

- **Published through Open WebUI:** the forwarded email of whoever asked
  (identity forwarding must be on; without it the publish is refused rather
  than attributed to someone else).
- **Published by the rig itself** — `scripts/artifact.sh`, campaign scripts
  (`publish-verdict.sh`), background agents, OpenCode on the rig, the tool
  server when a call carries no identity: the **rig** principal, `rig`. It is
  one stable owner whatever the allowlist says. No login header can claim
  `rig` or `local`.
- **A beast-chat transcript export:** the tailnet login that pressed Export
  ([BEAST_CHAT.md § Console features](BEAST_CHAT.md#console-features)).

Ownership never changes on republish. An admin changes it explicitly:
`artifact.sh chown <id> <login|rig>`, or `PATCH {"owner": …}`.

### Who administers

*Admins* see and manage every page (read, share, pin, tag, roll back, chown,
delete), and the gallery and viewer show them each page's owner. The admins
are:

1. the **locality token** — anything on the rig, the CLI included;
2. the logins in `ARTIFACT_ADMINS` (`openbeast.conf` or
   `OPENBEAST_ARTIFACT_ADMINS`) when it is set;
3. otherwise the **first** `ARTIFACT_OPERATORS` entry (falling back to
   `CHAT_OPERATORS`), the rig's own human.

Not every operator. On a multi-user rig, operators' private pages stay
private from each other; list the logins in `ARTIFACT_ADMINS` if you want
several admins. With no operator configured, nobody on the tailnet is an
admin: the first login to show up is never auto-trusted, and every rig
publish prints a warning with the one-line fix until an operator is set.
When `ARTIFACT_ADMINS` is unset and more than one operator is listed, every
start prints a line on stderr and writes an `admin-default` audit row naming
the implicit admin, since that login can manage the other operators' private
pages. Set `ARTIFACT_ADMINS` to choose explicitly.

The model's `list_artifacts` tool never gets the admin view, even on the rig:
it sees the rig's own pages and `tailnet` ones, so another owner's private
titles never enter a model's context.

### Upgrading: `local` pages and operator-owned pages

- **Owner `local`** (every page a rig with no allowlist published before
  this model) is re-owned to `rig` at server start: once, idempotent, locked
  per page, with a `reown` row in `index.jsonl` for each page and one audit
  summary row. Until that runs, `local` already reads as the rig.
- **Owned by the first operator** (what the CLI wrote on an allowlisted rig
  before this model) is left alone, because nothing on disk tells a CLI
  publish from a browser publish by the same person. The rig may still add a
  version to those pages, and to any admin's page, with `artifact.sh publish
  <f> --id <id>`; it never rewrites the owner. It cannot republish into
  another operator's page. `artifact.sh chown <id> rig` hands one over for
  good.

### There is no "public"

Your tailnet is the perimeter; beast-artifact is
never exposed with `tailscale funnel`, for the same reason nothing else in
OpenBeast is.

A login that is not in `ARTIFACT_OPERATORS` gets **404** on the gallery, the
shell and the raw route alike. A listed login asking for someone else's
`private` artifact also gets **404**, never 403 — a refusal that distinguishes
"not yours" from "does not exist" tells a prober which ids are real.

```bash
./scripts/artifact.sh visibility <id> tailnet     # share with the household
./scripts/artifact.sh visibility <id> private     # take it back
```

## The CLI — `scripts/artifact.sh`

The CLI is the general publisher: shell scripts, cron, campaign runs, and
background agents (which reach it through their `bash` tool) all use it. It
talks to `:3004` on loopback and presents the proof-of-locality token on write
verbs, so publishing only works *on the rig*. The token goes to curl through a
0600 `--config` file in the CLI's own temp directory, never as a `-H` argument
— `/proc` makes argv world-readable, so a header on the command line hands the
write credential to every uid on the box.

```bash
./scripts/artifact.sh publish <file.html> [options]
./scripts/artifact.sh list [--json] [--limit N | --all] [--session ID] [--tag T]
./scripts/artifact.sh show <id>
./scripts/artifact.sh versions <id>
./scripts/artifact.sh rollback <id> <n>
./scripts/artifact.sh visibility <id> private|tailnet
./scripts/artifact.sh pin <id>  |  unpin <id>
./scripts/artifact.sh tag <id> [TAG]...          # replaces the tags; none clears
./scripts/artifact.sh chown <id> <login|rig>     # admin: hand a page over
./scripts/artifact.sh prune <id> --keep N --yes  # delete old versions, keep the URL
./scripts/artifact.sh remove <id> [--version N] --yes
```

The CLI acts as the rig, so it can manage every page (it is an admin). `list`
shows 25 rows by default and says "Showing 25 of N" when there are more;
`--all` lists everything, `--session` and `--tag` filter, `--json` gives the
API's answer. `show <id> [--json]` prints one page's meta. `tag` replaces the
page's tags with the ones given (none clears them). `chown`, `prune` and
`remove` are covered below. When `OPENBEAST_SESSION_ID` is set, `publish`
stamps it as the version's source session (see *Where a page came from*).

`publish` options:

| Option | Effect |
|---|---|
| `--title "…"` | gallery + shell title. Omitted → the page's own `<title>` (scanned in the first 8 KB, on every version), else the stored title |
| `--description "…"` | one sentence, shown as the gallery row's subtitle |
| `--favicon "📊"` | one or two emoji, the tab icon; **fixed for the life of the artifact** — the first one given sticks, later ones are ignored |
| `--id <uuid>` | publish *into* an existing artifact: same URL, next version |
| `--label "…"` | a few words naming this version in the picker |
| `--file published=source` | add a supporting file (repeatable) — `--file app.js=dist/app.js` publishes at `app.js` next to the page |
| `--visibility tailnet` | publish straight to tailnet-visible (default `private`). **Creation only**: a republish keeps the page's visibility, and `publish` prints the effective one (and a warning if you asked for another) |

Caps, enforced at publish and mirroring what Claude Code's artifacts accept:
**16 MB** for the page or any text file, **15 MB** per binary file, **255**
files and **64 MB** total per version — and **200 versions** per page. A
rejection names the file and the cap it broke; a full page names `prune`.
The same caps hold for `publish_artifact`, which calls the store in process.
A disk-full publish answers **507** and leaves nothing half-written.

### Verdicts and the leaderboard: `scripts/publish-verdict.sh`

A campaign stage can publish its verdict to a URL that stays the same across
reruns:

```bash
./scripts/publish-verdict.sh tier3-zig "$OUT/verdict.txt"         # .txt → escaped <pre> page
python3 evals/scoring.py --html "$OUT/board.html" \
  && ./scripts/publish-verdict.sh leaderboard "$OUT/board.html"
```

The id is `uuid5(NAMESPACE_URL, "openbeast:verdict:<slug>")`, so each rerun
adds a version to the same page. Each version is labelled
`<git short sha> era=<eval era>` unless you pass `--label`; `--title` sets the
title. A `.txt` (or any non-HTML) file is wrapped in a page, HTML-escaped
inside `<pre>`. Pages are always private and owned by the rig, so they open
for the rig's admins (set `ARTIFACT_OPERATORS`, or share one with
`artifact.sh visibility <id> tailnet`). The script never fails its caller:
with `BEAST_ARTIFACT` off, the server down or a bad argument it prints one
stderr line and exits 0. `scoring.py --html PATH|-` writes a self-contained
leaderboard page (no script, no external request, every value escaped). No
campaign script calls either yet; hooking them in is one line each, as above.

## The two tools

`publish_artifact` and `list_artifacts` ship on the **MCP / Open WebUI
surface** (18 tools) and are deliberately **not** in the autonomous runner's
10-tool registry — see [TOOLS.md](TOOLS.md). Background agents publish through
the CLI instead, which keeps the runner's tool-selection pressure exactly
where it was.

```
publish_artifact(path, title="", description="", favicon="",
                 artifact_id="", label="", visibility="")
    # no `files=` — supporting files are CLI-only (artifact.sh --file pub=src)
    → 'Published "Weekly numbers" → https://beast:8446/a/<id> (v3, id <id>, visibility private)'
    #   plus a NOTE: line when the page will not open where you expect

list_artifacts(limit=25)
    → one line per artifact: title, url, versions, visibility, updated
    #   never the admin view: other owners' private pages are not listed
```

- `path` is a file the model already wrote with `write_file`, and it must be
  **inside the caller's workspace** (and not the store's own tree): publishing
  mints a durable URL on the tailnet, so the tool will not turn an arbitrary
  file on the rig into one. Write the page first, publish second — the tool
  does not take inline HTML. The human publishes anything with the CLI.
- `visibility` applies when the page is first published only; the result
  always names the page's actual visibility. With no `<title>` in the page and
  no `title`, the filename names it.
- Under OpenCode (which starts the tool server with your plain shell
  environment) the tools read `BEAST_ARTIFACT` from the rig's
  `openbeast.conf`. On a client machine they answer that artifacts are
  published on the rig.
- Passing `artifact_id` from a previous result is how a model *updates* a page
  it published earlier in the same conversation instead of minting a new URL.
- A publish stamps `OPENBEAST_SESSION_ID` as the source session when the
  tool server's environment carries one.
- Both return a string and never raise, like every other tool.
- Neither is in `GUEST_TOOLS`: a guest-role WebUI account gets **404** for
  them, as it does for every non-web tool ([RBAC_PLAN.md](RBAC_PLAN.md)).

## Publishing on the tailnet

```bash
./scripts/setup-tailscale.sh --publish-artifact      # + :8446
./scripts/setup-tailscale.sh --unpublish-artifact
```

This maps `tailscale serve --bg --https=8446 http://127.0.0.1:3004` — or, on a
rig with a specific `BIND_HOST`, that address instead of `127.0.0.1`, because
the server listens only where it binds — with the same MagicDNS and cert
pre-checks as every other published port. (Tailnet logins are honoured only
from a peer on the rig itself: loopback, or — for a LAN `BIND_HOST` — a
connection made from that same address, which is what `tailscale serve` on
the rig does. Any other host presenting the header is anonymous.) OpenBeast now
publishes several ports, so every `setup-tailscale.sh` setup run (with or
without a `--publish-*` flag) prints the mount table after configuring serve —
`:443` WebUI, `:8443` inference, `:8444` slot discovery, `:8445` chat, `:8446`
artifacts, `:8889` search — each marked published or not. To just look, run
`./scripts/setup-tailscale.sh --status`: it prints `tailscale serve status`
and the same table, and changes nothing (no sudo, no conf write).

**The URLs follow the mount.** With nothing configured, the store asks
`tailscale serve status` which name it publishes `:8446` under and hands out
`https://<that name>:8446/a/<id>` — the name the certificate was issued for,
which is the tailnet machine name and **not** the OS hostname (they are chosen
independently). Until you run `--publish-artifact` nothing listens on `:8446`,
so the URL is the loopback one, `http://localhost:3004/a/<id>` — which **no
browser can open** (the viewer refuses anonymous callers, and only `tailscale
serve` or the CLI's locality token supplies an identity), so the tools say so
next to the link instead of handing over a dead one. The
answer is cached for a minute, so publishing the port needs no restart. Set
`ARTIFACT_BASE_URL` in `openbeast.conf` only if you front the viewer yourself.

**Read auth is tailnet identity.** Tailscale's proxy passes
`Tailscale-User-Login`; the server checks it against `ARTIFACT_OPERATORS` in
`openbeast.conf` (a comma-separated list of logins) and 404s everything for a
login that is not listed. Set it before you publish the port:

```bash
echo 'ARTIFACT_OPERATORS=you@example.com' >> openbeast.conf
```

**Publishing is loopback-only.** It requires the proof-of-locality token,
which only a process on the rig can read. Managing a page from a phone is the
one remote write, and it has its own section below.

Every request **that reaches the server** writes one line to
`.run/artifact-audit.jsonl` (mode 0600): `ts, login, method, route, id, n,
outcome, ms`. `login` is who the server decided the caller was (null when
refused); a login header it did not honour is kept as `claimed_login` with the
socket `peer`; `local: true` marks the rig and `device` a phone's key. A
publish adds `id`, `n`, `owner`, `sha256` and `bytes`; a PATCH names what
`changed`. Never page content. That covers `scripts/artifact.sh` and every
tailnet viewer.

The file is bounded per caller, per 5-minute window: unidentified callers
get 1000 rows per refusal reason (`DENY_AUDIT_ROWS`), and each tailnet login
gets 2000 rows (`LOGIN_AUDIT_ROWS`), so one client polling health in a loop
cannot fill the disk. Past a budget, that caller's rows are counted in
`/metrics` only, and one `denied: "audit-budget"` row says so. The rig's own
LOCAL calls and every successful write are always logged.

It does **not** cover the model's tools. `publish_artifact` and
`list_artifacts` call the store in process — no HTTP hop, so no audit row. A
publish through them still appends the store's own ledger,
`<store>/index.jsonl` (`ts, id, n, owner, bytes, sha256`); a list through them
leaves no trace at all. If you need every model-side read audited too, route
the tools through the server instead — that is the only way to get it, and it
is deliberately not built.

beast-artifact does **not** sit behind beast-gate — that gate is the
*inference* edge, and this is not the inference path.

## Managing pages from a phone

Publishing stays on the rig. **Managing** a page — pin, tags, share/unshare,
rollback, delete — can also come from a phone, with two things together:

1. your tailnet login, which `tailscale serve` injects (from a peer on the
   rig; see above), and
2. a device key enrolled with the **`artifact` scope**:

```bash
./scripts/clients.sh enroll phone --label "My phone" --scope artifact   # prints the key once
./scripts/clients.sh scope phone add artifact      # or grant it to a device you already have
```

In the viewer, the **⋯** button opens the **Manage** sheet. It shows only
when your login owns the page or is an admin. Paste the key under *Device
key*; it is kept in that browser's `localStorage` (the shell's origin — the
sandboxed page cannot read it). The sheet then does:

- **Pin / Unpin.** Pinned pages sort first in the gallery and are never
  touched by retention.
- **Tags.** Up to 16 per page, each lowercased, letters, digits, space,
  `.`, `_`, `-`, 32 characters max. Saving replaces the page's tags.
- **Share with tailnet / Make private**, with a confirm before sharing.
- **Delete**, which deletes every version and requires typing the page id.

The same calls are open to any client holding such a key: `PATCH` and both
`DELETE` routes from the URL table, with the key as `Authorization: Bearer`
or `X-OpenBeast-Device-Key`. Rules:

- The store still requires **owner or admin**. A keyed login asking about a
  page it cannot manage gets the same flat 404 as a page that does not exist,
  checked *before* its request body is validated, so a bad tag on someone
  else's private page cannot confirm the page exists. Your own page gets the
  readable 400.
- Missing, unknown, revoked and unscoped keys all get the same flat 404.
- Bodies are capped at **64 KB**, and each device at **60 changes a minute**
  (429 past that).
- `owner` (chown) is admin-only.
- Every change is an audit row carrying `device` and what `changed`.
- `POST /api/artifacts` (publish) stays locality-only: a device key never
  creates a page.

## Deleting, pruning and retention

```bash
./scripts/artifact.sh remove <id> --yes              # the page and every version
./scripts/artifact.sh remove <id> --version 3 --yes  # one old version
./scripts/artifact.sh prune <id> --keep 20 --yes     # all but the newest 20
```

A version delete never removes the version `current` points at (roll back
first) or the last remaining one (remove the page instead), and it only
removes versions the page's meta names — the store never guesses what is
debris. `prune` keeps the current version whatever `--keep` says. Every
deletion is a ledger row in `<store>/index.jsonl`; through the server it is
an audit row too.

**Retention is opt-in.** `ARTIFACT_RETAIN_DAYS=N` in `openbeast.conf` (or
`OPENBEAST_ARTIFACT_RETAIN_DAYS`) makes the server delete, once a day, every
**unpinned** page not updated for N days. Pinned pages are never touched, and
neither is a page whose timestamp cannot be read. Unset or `0` (the default)
keeps everything. Each deletion is an audit row and a ledger row with reason
`retention`. There is no store-wide byte quota.

## Where a page came from: session links

beast-chat exports `OPENBEAST_SESSION_ID` into every agent and job it starts,
and `scripts/job.sh run` does the same. When a publish sees it —
`artifact.sh publish`, `publish_artifact`, a transcript export — the session
id is recorded on the version and as the page's latest `source_session`
(validated to the ledger's id shape; anything else is dropped, never an
error).

- The viewer shows **made by session `<id>`** for the version on screen —
  a pinned `/a/<id>/v/<n>` names the session that made version `n`, not the
  latest publisher, and a version published outside any session names none.
  It is a link to
  `https://<rig>:8445/#/s/<id>` only when `tailscale serve` publishes the
  chat console on `:8445` (same detection as the artifact URL); otherwise it
  is plain text. `CHAT_BASE_URL` in `openbeast.conf` overrides the console's
  base URL, and `off` or `none` turns the link off.
- `/?session=<id>`, `GET /api/artifacts?session=<id>` and `artifact.sh list
  --session <id>` list every page that session published any version of,
  including pages a later session has republished since.

## The security posture

The page came out of a language model. The design assumes it is hostile and
takes its capabilities away rather than auditing it.

### Two locks, both mandatory

1. **`sandbox` on the frame.** The viewer shell embeds the artifact as
   `<iframe src="/raw/…" sandbox="allow-scripts allow-forms allow-modals
   allow-popups allow-popups-to-escape-sandbox">`. No `allow-same-origin`,
   which is the whole point: the
   document gets an **opaque origin**, so it has no cookies, no
   `localStorage`, no `IndexedDB`, no same-origin access to anything, and no
   ability to ride your tailnet identity into Open WebUI on `:443`.
   Also absent: `allow-downloads` and `allow-top-navigation`.
2. **A CSP header on every `/raw/` response**, so the policy holds even when
   the page is opened directly rather than through the shell:

```
Content-Security-Policy:
  sandbox allow-scripts allow-forms allow-modals allow-popups
          allow-popups-to-escape-sandbox;
  default-src 'none';
  script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com
             https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com
             https://code.jquery.com;
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com data:;
  img-src 'self' data: blob:;  media-src 'self' data: blob:;
  connect-src 'none';  frame-src 'none';  object-src 'none';
  form-action 'none';  base-uri 'none';  frame-ancestors 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cross-Origin-Resource-Policy: same-origin
```

This is the first CSP in the OpenBeast codebase. A regression test pins the
exact header string and the exact `sandbox` attribute; if you are changing
either, that test failing is the feature working. `'unsafe-eval'` is there
because Alpine.js, Vue's in-DOM templates and friends compile at runtime; it
grants nothing `'unsafe-inline'` does not already grant inside this sandbox.
`allow-popups-to-escape-sandbox` lets a link the page opens in a new tab be the
real site rather than a crippled opaque-origin copy — one of our own `/raw/`
pages opened that way still carries this CSP by header. External links in a
page open in a new tab (the viewer adds a small click handler at serve time):
a plain link used to navigate the frame into the shell's `frame-src 'self'`
and replace the page with "This content is blocked".

### Why supporting files need a token

Through v1.4.0 a page published with `--file app.js --file style.css --file
chart.png` rendered **none of them** in a browser (measured in headless
Chromium; every header test passed throughout, because a header test never
asks a browser). Two independent causes:

- `script-src` and `style-src` had no `'self'`, so the page's own
  `<script src="app.js">` and `<link href="style.css">` were CSP violations.
- The sandboxed document has an **opaque origin**, which makes it cross-origin
  to everything — this server included. Its `<img src="chart.png">` is
  therefore a cross-site no-cors load, and `Cross-Origin-Resource-Policy:
  same-origin` refuses exactly that.

Dropping CORP on supporting files would let any page open in your browser
embed them (the tailnet identity is attached by the network, not by a cookie,
so it rides along on cross-site requests). Instead the shell frames the
artifact under `/raw/<id>/v/<n>/~<token>/`, where `<token>` is an HMAC over
the id and version keyed by `.run/artifact-raw.key` (0600, persisted so open
tabs survive a restart). Relative URLs in the page inherit the token; files
under that path are served `Cross-Origin-Resource-Policy: cross-origin` with
`Access-Control-Allow-Origin: *` (module scripts and fonts are CORS-mode
fetches, and the sandbox's `Origin` is the literal `null`). A third-party
page cannot learn the token — it cannot read the shell that carries it. The
framed artifact itself *can* (it is in its own `location.href`), which unlocks
only that version's files — files it can already load — and it cannot mint a
token for any other id or version. The untokenized file route keeps
`same-origin`. **The token is not an identity:**
every read gate above still applies to the capability path, and a wrong token
is the same flat 404 as everything else.

### What a page can and cannot do

| Can | Cannot |
|---|---|
| Run its own inline JavaScript | `fetch` / `XHR` / `WebSocket` anywhere — `connect-src 'none'` |
| Load a script from the four allowlisted CDNs | Load a script from any other host (unpkg, esm.sh, your own server) |
| Load a stylesheet from `fonts.googleapis.com` and its font files from `fonts.gstatic.com` | Load a stylesheet, image, or media file from any other external host |
| Show images, audio and video embedded as `data:` URIs, or as `--file` supporting files (served with byte ranges, so video seeks and plays on iOS) | Read or write cookies, `localStorage`, `sessionStorage`, `IndexedDB` |
| Use `eval` / `new Function` (runtime template compilers) | — |
| Draw inline SVG, canvas, animations | Start a download — `<a download>` and script-driven saves are inert |
| Open a popup, use `alert`/`confirm`, navigate **itself** to an external URL | Submit a form anywhere, including to itself — `form-action 'none'` |
| — | Navigate the top window from inside the shell's frame, or frame another page |
| Be framed by the shell on this host | Be framed by any other origin (`frame-ancestors 'self'`) |

The CDN allowlist is **Claude Code's, verbatim**, so a page written for one
system renders in the other. The one deliberate difference: Claude gives every
artifact its own origin and therefore working `localStorage`. We serve one
host with one cert and cannot mint origins, so isolation comes from the
sandbox instead — stricter on storage (there is none), identical on network.

### What the service itself does

- The shell and the gallery are **ours**, not model-authored, and carry their
  own policy (`frame-ancestors 'none'`, and a `script-src` that is exactly the
  sha256 of each inline block the template ships — no `'self'`, because a
  model-authored `/raw/…/x.js` is same-origin, and no `'unsafe-inline'`).
  Keep those two policies separate.
- The artifact store is `0700` under `$OPENBEAST_FILES_DIR/artifacts/`,
  alongside the per-user file shards rather than inside them. `meta.json` is
  written atomically (mkstemp + rename) so a crash mid-publish cannot leave a
  half-parsed artifact.
- Content types on supporting files come from the **published** extension, and
  `nosniff` stops a browser from guessing otherwise.
- `Tailscale-User-Login` is forgeable by a process already on the rig. That is
  inside the existing loopback trust model, and it only ever buys *reads* —
  writes need the locality token.
- **The header counts only from a peer on the rig.** The server binds
  `OPENBEAST_BIND`, so `BIND_HOST=0.0.0.0` or a LAN address puts `:3004`
  off the box — and a LAN host (or a tailnet node dialling `100.x:3004`
  directly) could send `Host: localhost` plus the owner's login and read
  every private page, allowlist or not. So the header counts from loopback,
  or from a peer whose address IS the address the connection was accepted
  on — only this host can connect from its own address, which is exactly
  what `tailscale serve` does when `BIND_HOST` is a LAN address. Any other
  peer that presents the header is anonymous (404), and the rig's own names
  (`rig`, `local`) are never accepted from a header at all. The peer is the real socket peer:
  uvicorn runs with `proxy_headers=False`, because `tailscale serve` always
  adds `X-Forwarded-For: <tailnet IP>` and uvicorn's default would otherwise
  rewrite the loopback peer to it. Review 2026-09-29.
- **`Cross-Origin-Opener-Policy: same-origin`** on every answer except the
  capability tree (`/raw/<id>/v/<n>/~<token>/…`), the flat 404 included.
  The identity rides the network, not a cookie, so without it any site the
  viewer visits could `window.open` a shell URL and count frames to learn
  which private pages and versions exist.
- **The `Host` header is pinned** (`TrustedHostMiddleware`, the outermost
  middleware, sharing the one allowlist in `agents/hostpolicy.py` with
  beast-chat). A browser cannot forge `Host`, and that is what closes DNS
  rebinding: without this, a page served from `http://evil.example:3004/`
  that rebinds the name to `127.0.0.1` becomes **same-origin** with this
  server, and same-origin means it may set request headers — including the
  `Tailscale-User-Login` header above. On a single-user rig the owner string
  was the constant `local`, so nothing even had to be guessed: the page could
  read the gallery and every `private` artifact. Found in the v1.4.0
  adversarial review; the middleware runs before the identity gate, so a
  rebound `Host` cannot even write an audit row. Extra names go in
  `OPENBEAST_ARTIFACT_ALLOWED_HOSTS`.
- **Audit rows an unidentified caller can mint are budgeted AND byte-capped.**
  Refusals were budgeted from the start; `/api/artifacts/health` — the one
  route exempt from the anonymity gate — was not, so it was an
  unauthenticated, unrotated append. The row's `login` field is also capped
  at 128 chars: a bounded row *count* with an unbounded row *size* is not a
  bound (an 8 KB header bought an 8 KB row). Both fixed in the v1.4.0 review.
- A `PATCH` checks ownership first (a caller who can neither own nor
  administer the page gets the flat 404 before its body is read), then
  validates the body, then applies its fields in a fixed order — `current`,
  `description`, `pinned`, `tags`, `visibility`, `owner` — because they are
  independent locked writes with no rollback. `current` is the one that can
  fail after a valid body (no such version), so it goes first; `visibility`
  (the *widening* write) and `owner` (which changes who can read) go last: a
  mixed body that fails part-way can never leave the artifact shared while
  telling the caller the request failed.

## Writing a page

These are the authoring rules the tool descriptions teach the model. They
apply equally when you write a page by hand.

1. **Write the body, not the document.** Start with `<title>`, then `<style>`,
   then your content. The doctype, charset, viewport and a small reset are
   added at serve time — do not write `<html>`, `<head>` or `<body>` yourself.
2. **Self-contained or allowlisted, nothing else.** Inline all CSS and JS.
   Embed images as `data:` URIs. If you need a library, take it from
   `cdnjs.cloudflare.com` at a pinned exact version, in its UMD build, loaded
   *before* the inline script that uses it.
3. **Never `fetch`.** There is no network. Bake the data into the page as a
   JavaScript literal.
4. **Wrap storage in `try`/`catch`.** `localStorage` throws in an opaque
   origin. A page that assumes it works renders blank instead of degrading.
5. **Design both themes.** Define a light palette as custom properties on
   bare `:root`, redefine them in a `prefers-color-scheme: dark` block, and
   give `body` an explicit background — a transparent body borrows whatever
   is behind the frame.
6. **Make wide things scroll inside themselves.** Tables, diagrams and code
   blocks get their own `overflow-x: auto` container; the page body must never
   scroll sideways, because half your viewers are on a phone.
7. **Title it like a name, not a sentence.** Two to four words, distinctive
   enough to pick out of a gallery list. The description carries the
   explanation.
8. **Pick the emoji favicon once.** It is how people find the tab again, so it
   stays for the life of the artifact.

`scratch/spare-memory-meta.html` is the hand-built page that predates this
service, and is a fair example of the shape.

## Verification

```bash
# the service is up
curl -s http://127.0.0.1:3004/api/artifacts/health

# publish, then fetch it back
./scripts/artifact.sh publish scratch/spare-memory-meta.html --title "Spare memory"
./scripts/artifact.sh list

# the isolation headers are actually on the raw route.
# The token is REQUIRED: locality is proven by the X-OpenBeast-Local header,
# never by the socket peer, so a bare loopback curl resolves to `anonymous`
# and gets the flat 404 — which carries no CSP at all. Without the token this
# check greps nothing on a perfectly healthy server and cannot tell you
# anything. Pass it through a 0600 config file, never -H: /proc makes argv
# world-readable (see "The CLI — scripts/artifact.sh" above).
printf 'header = "X-OpenBeast-Local: %s"\n' "$(cat .run/artifact-local.token)" \
  > /tmp/art.cfg && chmod 600 /tmp/art.cfg
curl -sD- -o /dev/null -K /tmp/art.cfg \
  http://127.0.0.1:3004/raw/<id>/v/1/ | grep -i 'content-security-policy'
rm -f /tmp/art.cfg

# publishing is loopback-only: from another tailnet device, want 404
curl -o /dev/null -w '%{http_code}\n' -X POST https://beast:8446/api/artifacts
```

Then the checks a header assertion cannot make for you: open the URL on your
phone, confirm it renders the same as the file does locally, publish a second
version and confirm the picker shows both and that `/a/<id>/v/1` still serves
the first.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `artifact.sh` says the server is unreachable | `BEAST_ARTIFACT` is not `true`, or the stack was not restarted after setting it. `./start.sh --status` |
| **404 on everything** from a phone, while the rig works | Your tailnet login is not in `ARTIFACT_OPERATORS`. This is the intended answer for an unlisted login — it is not a bug, and it is not 403 |
| The gallery on your phone is empty, but `artifact.sh list` is not | No operator is configured, so the rig's private pages open for nobody. Set `ARTIFACT_OPERATORS=you@example.com` and restart; the rig's pages (old `local` ones included) then open for you |
| 404 on someone else's link | That artifact is `private`. Its owner runs `artifact.sh visibility <id> tailnet` |
| **502** from `https://beast:8446` | The port is published but nothing is listening — you ran `--publish-artifact` without turning `BEAST_ARTIFACT` on, or the server died. Same trap as `--publish-slot` and the dashboard extension ([BEAST_SLOT.md](BEAST_SLOT.md)) |
| A publish is refused at 200 versions | The per-page version cap. `./scripts/artifact.sh prune <id> --keep 20 --yes` frees room and keeps the URL |
| **429** from the Manage sheet | More than 60 changes a minute from that device key. Wait a minute |
| The Manage sheet's actions answer 404 | The key is missing, unknown, revoked or lacks the `artifact` scope (`./scripts/clients.sh show phone`), or your login neither owns the page nor is an admin. All of these are the same 404 on purpose |
| No ⋯ (Manage) button in the viewer | Your login neither owns the page nor administers it. The rig's pages are managed by admins (`ARTIFACT_ADMINS`, else the first operator) |
| An operator can now open another operator's private page | With `ARTIFACT_ADMINS` unset, the first operator is the admin. Set `ARTIFACT_ADMINS` to the logins you mean (the start-time `admin-default` audit row names the implicit admin) |
| `artifact <id> is busy` | Another publish holds that page's lock. Locking is **per page**, so every other page and every read is unaffected; the call gives up after `OPENBEAST_ARTIFACT_LOCK_TIMEOUT` (10s) rather than hanging — an environment variable only, **not** an `openbeast.conf` key (`conf.sh` greps a fixed set of keys and does not map this one), so export it in the unit or the shell that starts the stack. Retry. If it persists, a publisher died mid-write — the next publish steps over the half-written version (it is never served, and never deleted: the only copy of a real version is not something a damaged `meta.json` gets to vote on) and continues |
| `not your artifact` | Pages are owned by whoever published them. Re-describing, rolling back, re-sharing, pinning and deleting are owner-or-admin; republishing is owner-only (plus the rig, into the first operator's or an admin's page). The message is deliberately the same whoever you are, and deliberately says nothing about who the owner is |
| A page the model published is 404 to you | Your tailnet login and your Open WebUI identity are different names for you. The publisher is recorded from the forwarded email, so the chat UI must have identity forwarding on (`ENABLE_FORWARD_USER_INFO_HEADERS`) — without it the publish is refused rather than attributed to someone else. The Open WebUI id is recorded too, but only as provenance: it never grants a read. An admin can hand the page to your tailnet login: `artifact.sh chown <id> you@example.com` |
| **507** on publish | The rig's disk is full. The error names the failing write; nothing half-written is left behind |
| The page renders blank | Almost always `localStorage` or `fetch` in the page's startup path. Both throw here. Open the browser console — the error is in the frame's context, not the shell's |
| A chart library never loads | It is not on the allowlist, or the URL is not an exact pinned version on `cdnjs`. Non-allowlisted hosts fail **silently**, with no visible error |
| The link the model gave you does not open | Through v1.4.0 the URL was built from the OS hostname, which is neither the tailnet name nor a name the certificate covers. It now follows `tailscale serve` (see *Publishing on the tailnet*). A `http://localhost:3004/…` link means the port is not published: run `./scripts/setup-tailscale.sh --publish-artifact` (a browser cannot present an identity to the loopback viewer, so that link is a 404 even on the rig) |
| `--file` assets (script, stylesheet, image) do not load | Open the page through the shell (`/a/<id>`), whose frame uses the capability path. A hand-typed `/raw/<id>/v/<n>/` still serves the page, but its supporting files stay `same-origin` and a sandboxed document cannot load those (see *Why supporting files need a token*) |
| An external image is missing | `img-src` admits `'self'` and `data:` only. Embed it as a `data:` URI |
| A download button does nothing | `allow-downloads` is deliberately absent. The sandbox is working |
| The theme toggle reloads the artifact | Expected. The page lives in an opaque origin, so the shell cannot script into it; re-serving with `?theme=` is the only channel, and it only happens on an explicit toggle |
| The publish is rejected for size | Caps are 16 MB page / 15 MB binary / 255 files / 64 MB per version. Shrink the embedded `data:` URIs first — they are usually the cause. From the CLI a 404 on publish means the request body was over the server's size gate **or** the `--id` belongs to a page the rig may not republish into; the message names both, and `artifact.sh chown <id> rig` fixes the second |
| Remote access dies mid-view while localhost is fine | Suspect a full-tunnel VPN, not the stack (README § Remote access) |

## Not built

Deliberately out: public-internet sharing, share-by-token or expiring links,
per-artifact origins, browser-side editing, comments, republish
notifications, runtime capabilities inside pages (shared storage, viewer
identity, asking the model), a general download route for arbitrary shard
files, and `localStorage` inside artifacts.

Not built yet: a Markdown publish lane, a `publish-a-page` skill (it
regenerates `system-prompt-tools.md`, so it rolls the eval cache era and has
to land at a campaign boundary), remote *publish* (a device key can manage
pages, not create them), a store-wide byte quota, and turning a comment on an
artifact into a steer.
