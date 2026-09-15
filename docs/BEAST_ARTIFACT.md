# beast-artifact — a URL for anything the model renders

**Status: v1 (2026-09-14).** Opt-in (`BEAST_ARTIFACT=true`), loopback server
on `:3004`, published to the tailnet on `:8446`. Design, decisions and the
phase list live in [BEAST_ARTIFACT_PLAN.md](BEAST_ARTIFACT_PLAN.md); this
page is how you use it.

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

  writes: loopback only, proof-of-locality token
  reads:  tailnet identity, ARTIFACT_OPERATORS allowlist
```

The load-bearing fact: **the page runs in a sandbox with an opaque origin.**
A model wrote it, so it is treated as hostile — it has no cookies, no
storage, no network, and no way to reach the viewer's session on Open WebUI
or anything else on your tailnet. See
[The security posture](#the-security-posture), which is the section worth
reading twice.

## Quickstart

```bash
echo "BEAST_ARTIFACT=true" >> openbeast.conf
./stop.sh && ./start.sh -d

./scripts/artifact.sh publish page.html --title "Weekly numbers"
#   → https://beast:8446/a/3f1c9e4a-77b2-4d0e-9a51-0d2e6b8c4411  (v1)
```

That URL works on the rig immediately. To reach it from your phone, publish
the port once:

```bash
./scripts/setup-tailscale.sh --publish-artifact      # needs sudo
```

In chat, the same thing is one sentence: *"write me a page showing X and
publish it."* The model calls `publish_artifact` and answers with the link.

## The URL and version model

| Path | What it serves |
|---|---|
| `/` | the gallery — your artifacts, newest first |
| `/a/<id>` | the **viewer shell** at the current version |
| `/a/<id>/v/<n>` | the shell pinned to version `n` |
| `/raw/<id>/v/<n>/` | the artifact document itself, sandboxed (this is what the shell frames) |
| `/raw/<id>/v/<n>/<path>` | a supporting file of that version |
| `/api/artifacts` | JSON listing; `/api/artifacts/<id>` for one artifact's meta |

- **The id is a full `uuid4`.** No sortable prefix and no slug: the URL
  should not leak when a thing was made or what it is about.
- **`/a/<id>` follows the current version; `/a/<id>/v/<n>` never moves.**
  Send someone the plain link when you want them to see your latest thinking,
  the pinned link when you are citing a specific result.
- **Versions are immutable.** Publishing with the same `--id` writes `v(n+1)`
  and leaves every earlier version byte-identical on disk. Nothing overwrites
  a published version, ever — `rollback` only moves the `current` pointer.
- The shell's version picker lists every version with its optional `--label`;
  choosing one navigates to that pinned URL.
- **The stored file is exactly what you handed over.** The doctype, charset,
  viewport and a small reset are wrapped around it *at serve time*, so what
  comes back out of `v1/index.html` is what the author wrote.

## Visibility

Two states, and there is no third:

| `visibility` | Who can open it |
|---|---|
| `private` (default) | you — the login that published it |
| `tailnet` | any login in `ARTIFACT_OPERATORS` |

**There is no "public".** Your tailnet is the perimeter; beast-artifact is
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
talks to `:3004` on loopback and presents the proof-of-locality token, so it
only works *on the rig*.

```bash
./scripts/artifact.sh publish <file.html> [options]
./scripts/artifact.sh list [--limit N]
./scripts/artifact.sh show <id>
./scripts/artifact.sh versions <id>
./scripts/artifact.sh rollback <id> <n>
./scripts/artifact.sh visibility <id> private|tailnet
./scripts/artifact.sh remove <id> --yes
```

`publish` options:

| Option | Effect |
|---|---|
| `--title "…"` | gallery + shell title. Omitted → the page's own `<title>` (scanned in the first 8 KB) |
| `--description "…"` | one sentence, shown as the gallery row's subtitle |
| `--favicon "📊"` | one or two emoji; **fixed for the life of the artifact** — people find a tab by its icon |
| `--id <uuid>` | publish *into* an existing artifact: same URL, next version |
| `--label "…"` | a few words naming this version in the picker |
| `--file published=source` | add a supporting file (repeatable) — `--file app.js=dist/app.js` publishes at `app.js` next to the page |
| `--visibility tailnet` | publish straight to tailnet-visible (default `private`) |

Caps, enforced at publish and mirroring what Claude Code's artifacts accept:
**16 MB** for the page or any text file, **15 MB** per binary file, **255**
files and **64 MB** total per version. A rejection names the file and the cap
it broke.

## The two tools

`publish_artifact` and `list_artifacts` ship on the **MCP / Open WebUI
surface** (17 tools) and are deliberately **not** in the autonomous runner's
10-tool registry — see [TOOLS.md](TOOLS.md). Background agents publish through
the CLI instead, which keeps the runner's tool-selection pressure exactly
where it was.

```
publish_artifact(path, title="", description="", favicon="",
                 artifact_id="", label="", files="", visibility="private")
    → "Published Weekly numbers → https://beast:8446/a/<id> (v3)"

list_artifacts(limit=25)
    → one line per artifact: title, url, versions, visibility, updated
```

- `path` is a file the model already wrote with `write_file`. Write the page
  first, publish second — the tool does not take inline HTML.
- Passing `artifact_id` from a previous result is how a model *updates* a page
  it published earlier in the same conversation instead of minting a new URL.
- Both return a string and never raise, like every other tool.
- Neither is in `GUEST_TOOLS`: a guest-role WebUI account gets **404** for
  them, as it does for every non-web tool ([RBAC_PLAN.md](RBAC_PLAN.md)).

## Publishing on the tailnet

```bash
./scripts/setup-tailscale.sh --publish-artifact      # + :8446
./scripts/setup-tailscale.sh --unpublish-artifact
```

This maps `tailscale serve --bg --https=8446 http://127.0.0.1:3004`, with the
same MagicDNS and cert pre-checks as every other published port. OpenBeast now
publishes several, so `setup-tailscale.sh --status` prints the mount table:
`:443` WebUI, `:8443` inference, `:8444` slot discovery, `:8889` search,
`:8446` artifacts.

**Read auth is tailnet identity.** Tailscale's proxy passes
`Tailscale-User-Login`; the server checks it against `ARTIFACT_OPERATORS` in
`openbeast.conf` (a comma-separated list of logins) and 404s everything for a
login that is not listed. Set it before you publish the port:

```bash
echo 'ARTIFACT_OPERATORS=you@example.com' >> openbeast.conf
```

**Write access is loopback-only in v1.** Publish, patch, rollback and delete
all require the proof-of-locality token, which only a process on the rig can
read. Nothing arriving over the tailnet can create, change or remove an
artifact — a phone can view and nothing else. Remote publish (a device key
carrying an `artifact` scope) is a small addition and deliberately not in v1.

Every request writes one line to `.run/artifact-audit.jsonl` (mode 0600):
`ts, login, route, id, n, outcome, ms`, plus `sha256` and `bytes` on a
publish. Never page content.

beast-artifact does **not** sit behind beast-gate — that gate is the
*inference* edge, and this is not the inference path.

## The security posture

The page came out of a language model. The design assumes it is hostile and
takes its capabilities away rather than auditing it.

### Two locks, both mandatory

1. **`sandbox` on the frame.** The viewer shell embeds the artifact as
   `<iframe src="/raw/…" sandbox="allow-scripts allow-forms allow-modals
   allow-popups">`. No `allow-same-origin`, which is the whole point: the
   document gets an **opaque origin**, so it has no cookies, no
   `localStorage`, no `IndexedDB`, no same-origin access to anything, and no
   ability to ride your tailnet identity into Open WebUI on `:443`.
   Also absent: `allow-downloads` and `allow-top-navigation`.
2. **A CSP header on every `/raw/` response**, so the policy holds even when
   the page is opened directly rather than through the shell:

```
Content-Security-Policy:
  sandbox allow-scripts allow-forms allow-modals allow-popups;
  default-src 'none';
  script-src 'unsafe-inline' https://cdnjs.cloudflare.com
             https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com
             https://code.jquery.com;
  style-src 'unsafe-inline' https://fonts.googleapis.com;
  font-src https://fonts.gstatic.com data:;
  img-src 'self' data: blob:;  media-src 'self' data: blob:;
  connect-src 'none';  frame-src 'none';  object-src 'none';
  form-action 'none';  base-uri 'none';  frame-ancestors 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cross-Origin-Resource-Policy: same-origin
```

This is the first CSP in the OpenBeast codebase. A regression test pins the
exact header string and the exact `sandbox` attribute; if you are changing
either, that test failing is the feature working.

### What a page can and cannot do

| Can | Cannot |
|---|---|
| Run its own inline JavaScript | `fetch` / `XHR` / `WebSocket` anywhere — `connect-src 'none'` |
| Load a script from the four allowlisted CDNs | Load a script from any other host (unpkg, esm.sh, your own server) |
| Load a stylesheet from `fonts.googleapis.com` and its font files from `fonts.gstatic.com` | Load a stylesheet, image, or media file from any other external host |
| Show images, audio and video embedded as `data:` URIs | Read or write cookies, `localStorage`, `sessionStorage`, `IndexedDB` |
| Draw inline SVG, canvas, animations | Start a download — `<a download>` and script-driven saves are inert |
| Open a popup, submit a form to itself, use `alert`/`confirm` | Navigate the top window, or frame another page |
| Be framed by the shell on this host | Be framed by any other origin (`frame-ancestors 'self'`) |

The CDN allowlist is **Claude Code's, verbatim**, so a page written for one
system renders in the other. The one deliberate difference: Claude gives every
artifact its own origin and therefore working `localStorage`. We serve one
host with one cert and cannot mint origins, so isolation comes from the
sandbox instead — stricter on storage (there is none), identical on network.

### What the service itself does

- The shell and the gallery are **ours**, not model-authored, and carry their
  own, looser policy (`frame-ancestors 'none'`, a strict `script-src` that
  admits only their inline block). Keep those two policies separate.
- The artifact store is `0700` under `$OPENBEAST_FILES_DIR/artifacts/`,
  alongside the per-user file shards rather than inside them. `meta.json` is
  written atomically (mkstemp + rename) so a crash mid-publish cannot leave a
  half-parsed artifact.
- Content types on supporting files come from the **published** extension, and
  `nosniff` stops a browser from guessing otherwise.
- `Tailscale-User-Login` is forgeable by a process already on the rig. That is
  inside the existing loopback trust model, and it only ever buys *reads* —
  writes need the locality token.

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

# the isolation headers are actually on the raw route
curl -sD- -o /dev/null http://127.0.0.1:3004/raw/<id>/v/1/ | grep -i 'content-security-policy'

# writes are loopback-only: from another tailnet device, want 404
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
| 404 on someone else's link | That artifact is `private`. Its owner runs `artifact.sh visibility <id> tailnet` |
| **502** from `https://beast:8446` | The port is published but nothing is listening — you ran `--publish-artifact` without turning `BEAST_ARTIFACT` on, or the server died. Same trap as `--publish-slot` and the dashboard extension ([BEAST_SLOT.md](BEAST_SLOT.md)) |
| The page renders blank | Almost always `localStorage` or `fetch` in the page's startup path. Both throw here. Open the browser console — the error is in the frame's context, not the shell's |
| A chart library never loads | It is not on the allowlist, or the URL is not an exact pinned version on `cdnjs`. Non-allowlisted hosts fail **silently**, with no visible error |
| An external image is missing | `img-src` admits `'self'` and `data:` only. Embed it as a `data:` URI |
| A download button does nothing | `allow-downloads` is deliberately absent. The sandbox is working |
| The theme toggle reloads the artifact | Expected. The page lives in an opaque origin, so the shell cannot script into it; re-serving with `?theme=` is the only channel, and it only happens on an explicit toggle |
| The publish is rejected for size | Caps are 16 MB page / 15 MB binary / 255 files / 64 MB per version. Shrink the embedded `data:` URIs first — they are usually the cause |
| Remote access dies mid-view while localhost is fine | Suspect a full-tunnel VPN, not the stack (README § Remote access) |

## Not in v1

Deliberately out: public-internet sharing, share-by-token or expiring links,
per-artifact origins, browser-side editing, comments, pins, republish
notifications, runtime capabilities inside pages (shared storage, viewer
identity, asking the model), a general download route for arbitrary shard
files, and `localStorage` inside artifacts.

Queued behind v1: a Markdown publish lane, a `publish-a-page` skill (it
regenerates `system-prompt-tools.md`, so it rolls the eval cache era and has
to land at a campaign boundary), remote publish via a device scope, and — once
beast-chat exists — turning a comment on an artifact into a steer.
