# beast-docs — current platform developer docs, mirrored offline

**The problem.** The local model has a fixed training cutoff and no live
learning. SwiftUI, UIKit, Jetpack Compose and the Android Gradle Plugin
rename, deprecate and add APIs every few months. A model asked to write a
SwiftUI navigation flow today will reach for `NavigationView`, which Apple's
own page now marks deprecated in favour of `NavigationStack`. beast-lang
already solved this for *languages*, using the installed compiler as ground
truth. Nothing solved it for *platform frameworks*. This rig has no Swift
toolchain and no Android SDK, so the only authority available is the
publisher's documentation.

**What it is.** `scripts/docs.sh` and `agents/docs/`. They keep a local
mirror of the official pages: developer.apple.com DocC JSON,
developer.android.com's own markdown, and any static-HTML developer site.
Every page carries its URL and the date it was fetched. The mirror can be
searched with no network. A single page can be fetched live when the network
is up, and the live path knows about island mode. A mirror can be built on a
connected box and carried to an air-gapped one as a hash-verified tarball.

```bash
./scripts/docs.sh sources                                   # what can be mirrored
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms
./scripts/docs.sh sync android-compose --max-pages 300
./scripts/docs.sh search NavigationStack
./scripts/docs.sh read swiftui:/documentation/swiftui/navigationstack
./scripts/docs.sh live NavigationStack --source swiftui     # the official page, now
./scripts/docs.sh status                                    # pages, age, STALE flags
./scripts/docs.sh pack mirror.tar.gz && ./scripts/docs.sh unpack mirror.tar.gz   # on the offline box
```

A model reaches it in two ways: the **`platform_docs` tool** on the
MCP/WebUI surface, and **`scripts/docs.sh` through bash**. The
**`platform-docs` skill** teaches when to use each (§5).

---

## 1. How it relates to beast-lang

beast-lang ([`BEAST_LANG_PLAN.md`](BEAST_LANG_PLAN.md)) acquires language
corpora, introspects the installed toolchains and serves verified claims.
beast-docs borrows its design wherever the problem matches and departs from
it where the problem differs. Each departure is stated.

| | beast-lang | beast-docs |
|---|---|---|
| ground truth | the **installed toolchain** (L1) | the **publisher's page**. No toolchain here can confirm a SwiftUI claim (no Swift, no Android SDK on the rig) |
| trust tier | VERIFIED / GENERATED; CURATED is never served | everything is a DOCUMENT, in beast-lang's terms CURATED. It is **served only on request, labelled with its provenance, and never injected unasked** |
| corpus location | outside git (`$OPENBEAST_LANG_DIR`); "ship the acquirer, not the corpus" (§6 licensing) | same rule: `$OPENBEAST_DOCS_DIR`, default `~/.openbeast/docs`. Apple's text is "All rights reserved" (§2.3) |
| provenance | manifest per artifact: URL, sha256, licence, fetch date | manifest per source: URL → file, `fetched_at`, `checked_at`, ETag, Last-Modified, sha256, licence. Each page file also carries a header with the same facts |
| acquisition | explicit, operator-run, idempotent, resumable, OFFLINE-aware; URLs resolved through indexes, never hard-coded artifacts | same: `docs.sh sync` is operator-run, incremental and resumable, and refuses under OFFLINE / island. Discovery goes through the site's own index (DocC topic graph, `llms.txt`, sitemap) |
| serving facade | no-raise, lazy, bounded (`agents/lang/__init__.py`) | the same shape (`agents/docs/__init__.py`) |
| pull surface | `language_reference` on MCP/WebUI, admin-only, **not** in the runner | `platform_docs`, with the same placement and the same reasons |
| "not a web fetcher at agent time" (§8) | strict | **relaxed on purpose, and fenced.** Max asked for live traversal. Live mode fetches only hosts named in `sources.toml`, obeys robots.txt, uses the `fetch` tool's SSRF machinery, and with no route it does not dial at all |

The two tools answer different questions and never compete. For **what
will compile here** (zig, C/C++, Rust, Go, Python), use `language_reference`.
For **what the platform vendor says the API is** (SwiftUI, Compose, AGP), use
`platform_docs`. When they disagree, the compiler wins. The skill says so.

---

## 2. Research (2026-11-15, primary sources)

### 2.1 Apple

- **Pages are a JS app; the content is DocC JSON.**
  `https://developer.apple.com/tutorials/data/documentation/swiftui.json`
  returned 200 `application/json` with `ETag` and `Last-Modified` and
  `Cache-Control: max-age=300` (fetched 2026-11-15 19:25 UTC). The JSON is
  the DocC RenderNode format, `schemaVersion 0.3.0`. Top-level keys:
  `abstract, hierarchy, identifier, kind, legalNotices, metadata,
  primaryContentSections, schemaVersion, sections, topicSections, variants,
  references`. Per-symbol pages work the same way:
  `/tutorials/data/documentation/swiftui/navigationstack.json` and
  `/…/view/navigationdestination(for:destination:).json` both returned 200.
  `metadata.platforms[]` carries `introducedAt`, `deprecatedAt` and
  `message`. For example `NavigationView` now reads `deprecatedAt 27.3`,
  "use NavigationStack or NavigationSplitView instead".
- **An index endpoint exists.** `https://developer.apple.com/tutorials/data/index/swiftui`
  returned 200: 1.4 MB of JSON, `schemaVersion 0.1.2`, keys
  `includedArchiveIdentifiers, interfaceLanguages`. It is not used yet,
  because the topic graph is enough for bounded crawls. It is the obvious
  discovery source for a full mirror (§8).
- **robots.txt** (`https://developer.apple.com/robots.txt`, 2026-11-15):
  `User-agent: *` disallows `/cgi-bin/ /click/ /documentation/dataformats/
  /reference/ /search/ /survey/ /temp/ /unsubscribe/` and some forum paths.
  **`/tutorials/data/` and `/documentation/` are allowed.** There is no
  Crawl-delay. *Note:* one of our fetches that day got the HTML 404 page
  instead of the file, which robots semantics treat as "no rules". That is
  why sync checks robots against **both** the JSON URL it requests and the
  human page that URL stands for: a 404'd robots.txt must not open
  `/documentation/dataformats/`.

### 2.2 Android

- **The site serves its own markdown.** Every DevSite page is also published
  at `<page>.md.txt` (`text/markdown`). For example
  `https://developer.android.com/develop/ui/compose/lists.md.txt` returned
  200 on 2026-11-15. Sections publish an **`llms.txt`** index of those files.
  `https://developer.android.com/develop/ui/llms.txt` lists 622 pages, 383
  of them under `/develop/ui/compose`. That is cleaner than scraping HTML, so
  the Android sources use the `markdown` adapter. The `.md.txt` files send no
  ETag or Last-Modified, so incremental sync compares content hashes instead
  (§3.3). The HTML pages send Last-Modified only.
- **robots.txt** (2026-11-15): `User-Agent: *` disallows `/*?*authuser=`,
  `/assets/css/ /assets/images/ /assets/js/ /guide/samples/ /images/
  /partners/ /sdk/OLD_RELEASENOTES /sdk/RELEASENOTES /sdk/older_releases
  /shareables/`, with `Allow: /images/social/`. A separate
  `User-agent: Google-DevRel` group allows everything. There is no
  Crawl-delay. The `Sitemap: https://developer.android.com/sitemap.xml` is an
  index of 10 children holding 186,094 URLs; 77,161 of them carry no `?hl=`,
  and 339 sit under `/develop/ui/compose`. A child sitemap is 1–37 MB
  depending on compression, so sitemap discovery exists for full mirrors
  only.
- **No official downloadable docs bundle.** The SDK repository manifest
  (`https://dl.google.com/android/repository/repository2-3.xml`, 652
  packages) has no `docs` package any more.
- **Licence** (`https://developer.android.com/license`): "Unless otherwise
  noted, the documentation on this site, including any code shown in it, is
  made available to you under the Apache 2.0 license … All other content on
  this site … is licensed under the Creative Commons Attribution 2.5
  license." Text extracted from OpenJDK (`java.*`, `javax.*`) is GPLv2 with
  the Classpath Exception. The page footer reads: "Content and code samples
  on this page are subject to the licenses described in the Content
  License."

### 2.3 Licensing, and the gate it produced

Every Apple DocC JSON carries `legalNotices.copyright: "Copyright © 2026
Apple Inc. All rights reserved."` and points to
`https://www.apple.com/legal/internet-services/terms/site.html`. We could not
fetch that page from here (DNS failure), so the following comes from an
archived copy dated 2020-06-16. That copy says no content "may be copied,
reproduced, republished, … or distributed in any way (including
'mirroring')" except as expressly provided, and forbids "'deep-link',
'page-scrape', 'robot', 'spider' or other automatic device" access. We have
**not** verified whether the current wording carries a personal-use
carve-out.

Hence:

- Apple sources are `requires_ack = true`. The first sync **refuses** until
  the operator passes `--accept-terms`, and the acceptance is recorded in the
  mirror's manifest. That decision belongs to the operator. beast-docs makes
  it explicit and does not make it silently.
- Nothing mirrored is committed. The test fixtures are **synthetic**: our
  own text in the DocC / DevSite shapes.
- `pack` exists so you can carry **your own** mirror to **your own** offline
  machine. Do not hand an Apple mirror to anyone else. The source's licence
  line is printed by `status`, `read` and `live`.
- Android's Apache-2.0 / CC-BY-2.5 terms allow redistribution with
  attribution, and the licence line carries that attribution.

### 2.4 Existing documentation MCP servers (none works offline for Apple/Android)

| server | how it fetches | offline |
|---|---|---|
| **Context7** (`github.com/upstash/context7`, ~64.9k★, MIT shim, pushed 2026-11-14) | hosted endpoint `mcp.context7.com`. The README says the "API backend, parsing engine, and crawling engine — are private" | **no** |
| **sosumi.ai** (`github.com/NSHipster/sosumi.ai`, 492★, MIT, 2026-11-13) | Apple DocC JSON (`tutorials/data/documentation/<path>.json`, `tutorials/data/index/<framework>`) → markdown. Hosted on Cloudflare; self-hostable. "No permanent archives are maintained" | **no** |
| **kimsungwhee/apple-docs-mcp** (1,388★, MIT, 2026-03-17) | DocC JSON, `technologies.json`, 30-min memory cache | **no** |
| **MightyDillah/apple-doc-mcp** (628★, MIT, 2026-05-13) | DocC JSON via `tutorials/data`, memory cache | **no** |
| **Google Developer Knowledge MCP** (`developers.google.com/knowledge/mcp`) | remote service; needs an API key or OAuth and a Cloud project; covers Android | **no** |
| **DevDocs** (`github.com/freeCodeCamp/devdocs`, MPL-2.0) | offline docsets. Has Kotlin; **no Swift, Apple, Android or Jetpack docsets** | yes, but not for this |
| **Zeal / Dash** | offline docsets. Zeal has "Android (Kotlin)" 16/17 and Swift only up to 4.2; Dash (paid) lists iOS/macOS/Swift | yes, but stale or paid |

The established technique is the DocC JSON → markdown conversion that
sosumi and the apple-docs MCPs use, and beast-docs uses it too. What none of
these servers provides is the property OpenBeast needs: a durable, local,
provenance-stamped mirror that answers with no network and can be carried
to an air-gapped box. Wrapping any of them would also put a cloud dependency
on the offline path, so beast-docs is built in-repo, stdlib only.

---

## 3. Design

### 3.1 Sources are data — `agents/docs/sources.toml`

Each `[[source]]` entry sets:

- `name`, `title`
- `adapter`: `apple-docc-json`, `html` or `markdown`
- `hosts`: the only hosts the source may ever touch, redirects included
- `roots`, `allow_prefixes`, `deny_prefixes`
- `discovery`: `links`, `llms-txt` or `sitemap`, plus an `index_url`
- `content_selectors` and `strip_selectors` (HTML only)
- `max_pages`, `max_depth`, `delay`, `refresh_days`
- `license`, `robots_note`, `requires_ack`

The file ships with 14 sources:

- Apple: `swiftui`, `uikit`, `swift`, `xcode`, and `app-review` (HTML)
- Android: `android-compose`, `android-compose-ref`, `android-kotlin`,
  `android-build`
- Others: `kotlin`, `gradle`, `blender-python`, `klipper`, `octoprint`

The last five have selectors that have **not** been live-checked. They are
marked as such and should get `--max-pages 3` before a big crawl.

Adding a developer site is an entry in this file, not a code change. The
parser is `tomllib` when present. On Python < 3.11 (a Mac's
`/usr/bin/python3` is 3.9) it is a small parser for exactly this subset,
which refuses anything else. A test checks that both parsers give identical
results on the shipped file.

### 3.2 Adapters (pure functions, golden-tested)

- **`docc.py`** turns DocC JSON into markdown with these parts: title, role
  and module line, abstract, **Availability** (per-platform `introducedAt+`,
  `(beta)`), **DEPRECATED** (grouped by message, plus the deprecation
  summary), Declaration (rebuilt from tokens), Parameters, the discussion
  (headings, paragraphs, code listings with language, asides as `> **Note:**`,
  lists, term lists, tables, tab navigators, image alt text), Mentioned in,
  and Topics / Relationships / See Also, each with the target's abstract and
  URL. `links()` returns the topic graph, hierarchy first.
- **`htmlmd.py`** does three jobs:
  - The HTML adapter is a forgiving stdlib `html.parser` tree with a tiny
    selector language. It keeps the main content only and drops chrome:
    scripts, nav, buttons, `aria-hidden`, `hidden`, and `strip_selectors`.
    A custom element that wraps blocks (DevSite's `<devsite-code>` around
    `<pre>`) is treated as a block, so code is not flattened.
  - The markdown adapter takes the site's own `.md.txt`. It reduces links to
    their text (it handles nested parentheses and link text that wraps
    across lines), drops `<br />` and the index pointer, and uses the page's
    own H1, falling back to the `llms.txt` title.
  - Parsers for `llms.txt` and sitemaps.

### 3.3 Sync — incremental, resumable, polite

`docs.sh sync <source> [--max-pages N] [--root URL] [--accept-terms] [--force]`
does a BFS over the source's scope.

- **Fresh pages are skipped.** A page checked within `refresh_days` is not
  requested again, but its stored links still feed the frontier. Repeated
  small syncs therefore walk deeper instead of re-checking the same first N
  pages.
- **Stale pages get a conditional GET** with `If-None-Match` and
  `If-Modified-Since`. A **304, or a 200 whose rendered markdown hashes the
  same, rewrites nothing**: only `checked_at` moves, and `fetched_at` keeps
  meaning "when these bytes arrived".
- **Resumable.** The frontier is saved every 10 pages and on exit (^C, a
  dead network, the page budget), and the next sync resumes it. `--root`
  jumps the queue without discarding the saved frontier.
- **Polite.**
  - One request at a time, `delay` seconds apart per host (default 1.0 s).
    A robots `Crawl-delay` wins when larger.
  - Our own User-Agent: `openbeast-docs/1.0 (…)`.
  - robots.txt follows RFC 9309: longest match wins and Allow wins ties,
    Google's `*` and `$` wildcards are supported, and a group naming our
    token beats `*`. A 4xx robots.txt means no rules; **a 5xx or an
    unreachable robots.txt means the whole host is refused**.
- **Scoped.**
  - A URL outside the source's `hosts` is never contacted, and that covers
    redirects.
  - Requests go through `agents/tools.py`'s `_Pinned*Handler`s and
    `_fetch_url_blocked`: resolve, vet every address, dial the vetted IP,
    re-vet each redirect hop. These are imported, not copied.
  - There is a whole-request deadline (60 s) and a 16 MB body cap.
  - `OPENBEAST_DOCS_ALLOW_PRIVATE=1` drops only the address vet. It exists
    for an intranet docs server, and for the tests.
- **Offline-aware.** Under `OFFLINE=true`, parsed the way `conf.sh` and
  `lang-library.sh` parse it, or in island mode
  ([`BEAST_ISLAND.md`](BEAST_ISLAND.md)), sync refuses at once with exit
  code 3 and points to pack/unpack.

### 3.4 The store

```
~/.openbeast/docs/                 OPENBEAST_DOCS_DIR
  index.sqlite                     search index over every source (derived; `docs.sh reindex`)
  <source>/manifest.json           URL -> {doc_id, file, fetched_at, checked_at, etag,
                                   last_modified, sha256, title, links, kind, deprecated},
                                   frontier, robots notes, last_sync, terms_accepted_at
  <source>/pages/<slug>-<h8>.md    front-matter header (title, url, fetched_from, fetched_at,
                                   etag, last_modified, licence, sha256) + markdown
```

A page enters the manifest only after its bytes are on disk and hashed,
which is beast-lang's manifest rule.

### 3.5 Search

**SQLite FTS5** is present in the stdlib `sqlite3` on both this Mac (3.39.3)
and the rig. It is probed at runtime rather than assumed, and
`OPENBEAST_DOCS_NO_FTS=1` forces the fallback. Ranking works in three steps:

- An **exact title match** comes first: asking for `NavigationStack` means
  that page, not the guide that says the word forty times.
- Then **bm25**, weighted title 12 > camelCase-split title 6 > headings 3 >
  body 1. All terms are tried first, then any term.
- A camelCase query matches both the joined token and its parts.

Without FTS5, a plain table is scored in Python. A parametrized test checks
that both backends put the same document first.

Every hit prints title, doc id, URL, `fetched <date>, <n>d ago`, a
`[DEPRECATED]` flag when the page carries one, and **STALE** past the
source's refresh window.

### 3.6 Live mode and the offline fallback

`docs.sh live <url | Symbol> [--source S] [--save]` fetches one page now,
through the source's adapter.

- **Resolving the argument.** A URL must fall inside a configured source.
  For a bare symbol, an exact title in the mirror is tried first; failing
  that, for a DocC source, the URL is guessed:
  `View.navigationTitle(_:)` → `…/view/navigationtitle(_:)`.
- **Offline, or when the fetch fails**, it **does not dial**. It serves the
  mirror copy, and the first line says so:
  `LIVE FETCH UNAVAILABLE (offline: ISLAND MODE (no internet route since …)).
  Serving the MIRROR copy instead — check its date below.`
- If the page is not in the mirror either, live mode says that and returns
  the best mirror search hits for the reference.
- `--save` writes the live result into the mirror.

### 3.7 Carrying a mirror to an offline box

- **`pack <out.tar.gz> [--source S]`** writes a tar.gz of the chosen sources
  plus `BUNDLE.json`, which lists every file's sha256, and writes
  `<out>.sha256` beside the tarball.
- **`unpack <tarball> [--sha256 H]`** works in this order:
  1. Requires an expected hash, from the sidecar or `--sha256`, and checks
     the tarball against it.
  2. Refuses absolute paths, `..`, links and devices.
  3. Extracts to a staging directory.
  4. Requires the member set to equal `BUNDLE.json` exactly, and re-hashes
     every file against it.
  5. Only then swaps each source in, and reindexes.

The rule is the one `scripts/bundle.sh` follows: nothing is trusted because
it arrived. A tampered page inside a re-hashed tarball is caught by the
per-file hashes. The tarball hash is integrity, not authenticity. If you
need authenticity, sign `<out>.sha256` the way `bundle.sh sign` does.

---

## 4. Freshness contract

- Every answer (`search`, `read`, `live`, the tool) carries the **source URL
  and `fetched_at`**. When a later sync re-confirmed the page, it also
  carries "confirmed unchanged `<date>`".
- `status` and every hit flag **STALE** once `checked_at` is older than the
  source's `refresh_days`, which is 14–60 days by source.
- `sync` is incremental through ETag and Last-Modified where the publisher
  sends them (Apple), and through the content hash where it does not
  (Android `.md.txt`).
- The skill tells the model to say "the mirror is from `<date>`" when it is
  STALE or more than ~90 days old.

---

## 5. How the model reaches it — and why both ways

**(a) The `platform-docs` skill, plus `scripts/docs.sh` through bash.** This
is the right shape for **client-mode OpenCode**. There, bash runs on the
laptop, the slim checkout ships `agents/`, `scripts/` and `skills/`, and the
mirror is the laptop's own. `docs.sh` picks a usable python itself:
`OPENBEAST_PYTHON`, then the client venv, then python3.13 down to 3.10, then
python3. The code runs on 3.9 too. The skill is `prompt_index: false`
because the always-on menu in `system-prompt-tools.md` is paid for on every
local-model WebUI turn. It is reachable with `skill("platform-docs")`, and
`skill()` lists it.

**(b) The `platform_docs` MCP tool — yes, worth it too, for three reasons:**

1. **Open WebUI has no CLI discovery.** A WebUI chat model cannot be relied
   on to find `scripts/docs.sh` on the rig. A typed tool with a docstring
   that says "call it before writing SwiftUI" is the only reliable way in.
2. **The answer is identical either way.** The tool is a thin wrapper over
   the same no-raise facade the CLI uses: provenance, freshness and clipping
   are implemented once.
3. **It costs nothing where cost matters.** It is on the MCP/WebUI surface
   only, admin profile only, and **not in the runner's 10-tool registry**.
   That registry's selection accuracy at 27B is what the repo protects, and
   `agents/tools.py` is era-hashed. A test pins its absence by reading
   `agents/tools.py` and `agents/runner.py`. The surface goes from 18 tools
   to 19, and the count tests and docs moved with it.

The honest caveat comes from beast-lang §2.1: local models seldom call
optional tools, and skills fire close to 0% on them. Pull is what we can
build without touching the era-locked prompt. A **push** path would be the
measurable next step: inject the doc card for the API a Swift/Kotlin task
names, the way the zig pack is injected (§8). It needs an eval with Swift
or Kotlin headroom before it can be shown to help.

---

## 6. Tests

`tests/test_beast_docs.py` (33) and `tests/test_platform_docs_tool.py` (2)
are hermetic. A stub HTTP server on 127.0.0.1 serves synthetic fixtures
from `tests/fixtures/beast_docs/`. They pin:

- **Adapters**: golden markdown for a DocC symbol, a deprecated DocC symbol,
  a DevSite-shaped HTML page and a `.md.txt` page, plus URL mapping,
  `llms.txt` parsing and sitemap parsing.
- **Incremental sync**:
  - an unchanged ETag is a **304 with no rewrite**: the page files'
    mtimes are unchanged and `fetched_at` is preserved;
  - a body with no validators and unchanged content is not rewritten;
  - a changed body updates the page and the index;
  - fresh pages are not re-requested;
  - an interrupted sync **resumes**.
- **Politeness and scope**:
  - robots refusal by page URL, with the page never requested;
  - a robots 5xx refuses the whole host; a robots 404 means allowed;
  - the robots parser handles wildcards, longest match and agent groups;
  - a redirect off the source's hosts is refused;
  - a private address is refused without the opt-in;
  - the `--accept-terms` gate holds.
- **Search**: exact title first, camelCase handling, the source filter,
  deprecation flags, and FTS5 agreeing with the scan.
- **Status**: STALE flags.
- **Live, offline and island**: live goes through the adapter, including a
  guessed symbol URL and `--save`. Under `OFFLINE=true`, and under a
  monkeypatched island state, sync refuses and live serves the mirror
  **with zero requests made**. A live miss with no mirror copy says so.
- **Bundles**: the pack → unpack round trip on an "offline" store, and
  refusals for a wrong hash, a tampered member inside a re-hashed tarball,
  and a `../` member. A refusal installs nothing.
- **CLI**: `docs.sh` end to end, including exit codes 0/1/2/3.
- **Config**: the shipped `sources.toml` is valid and every source states a
  licence; the TOML subset parser matches `tomllib` and refuses everything
  else.
- **Tool**: `platform_docs` (search, read, status, live, input clip,
  admin-only), and its absence from `agents/tools.py` and `agents/runner.py`.

The tests are registered as a named step in CI's `pytest-named` job and
ignored in the full run, as `tests/test_ci_workflow.py` requires, and as a
named block in `tests/run_tests.sh`.

```bash
python3 -m pytest tests/test_beast_docs.py tests/test_platform_docs_tool.py -q
BEAST_DOCS_UPDATE_GOLDEN=1 python3 -m pytest tests/test_beast_docs.py -q   # accept a deliberate rendering change
```

---

## 7. Proven live (2026-11-15, this Mac, a scratch store)

| run | result |
|---|---|
| `sync swiftui --max-pages 30 --accept-terms --root …/swiftui/navigation` | 30 new, 0 errors, 31 HTTP requests (1 robots + 30 pages) at 1 req/s; 146 queued |
| the same with `--force --max-pages 5` | Apple answered **304** for the already-mirrored root, so nothing was rewritten; 4 new pages |
| `sync android-compose --max-pages 30` | `llms.txt` discovery found 383 in-scope pages of 622; 30 new, 0 errors, 32 requests |
| `search NavigationStack` | #1 `NavigationStack` (exact title), then "Understanding the navigation stack" and the `navigationDestination` modifiers |
| `search LazyColumn` | #1 "Lazy lists and lazy grids" (`/develop/ui/compose/lists`) |
| `search NavigationView` | #1 `NavigationView [DEPRECATED]` |
| `OPENBEAST_OFFLINE=true live NavigationStack` | `LIVE FETCH UNAVAILABLE (offline: OFFLINE=true …). Serving the MIRROR copy` |
| `pack` → `unpack` into a second store with `OFFLINE=true` | 69 files verified, 67 pages indexed, search answers. A 63-page mirror is about 2 MB on disk and 190 KB packed |

---

## 8. Open risks and next steps

- **Apple's terms.** See §2.3. The current terms page was not re-read (DNS
  failure from here), and the archived wording forbids mirroring and robots.
  The `--accept-terms` gate makes this the operator's explicit decision.
  Before any shared or commercial deployment, someone should read the live
  terms.
- **Formats can drift.** DocC `schemaVersion 0.3.0` and Android's `.md.txt`
  and `llms.txt` are not documented contracts. The golden tests catch
  rendering regressions, but not upstream changes. The adapters already
  render unknown DocC block types by recursing into `content` /
  `inlineContent`.
- **Bounded crawls are breadth-first from the roots.** A full SwiftUI mirror
  is several thousand pages, roughly 1–2 hours at 1 req/s. Apple's
  `tutorials/data/index/<framework>` endpoint would give a complete page list
  for full mirrors, and is not wired yet.
- **The secondary sources are unchecked.** The `kotlin`, `gradle`,
  `blender-python`, `klipper` and `octoprint` selectors were not live-checked.
- **Pull, not push.** See §5. The measurable win would be a
  harness-injected doc card for Swift/Kotlin tasks, and it needs eval units
  with headroom in those languages first.
- **No bundle.sh integration yet.** `bundle.sh build --with-docs` should
  carry a docs tarball alongside the weights, signed by the same key.
- **`doctor.sh` does not report mirror staleness yet.** `docs.sh status` is
  the only report today.
