---
name: platform-docs
description: Look up CURRENT platform developer documentation (Apple SwiftUI/UIKit/Swift/Xcode/App Store guidelines, Android Jetpack Compose/Kotlin/Gradle-AGP, and other mirrored developer sites) from beast-docs' offline mirror before writing code against a platform API. Activate when writing or fixing Swift, SwiftUI, UIKit, Kotlin, Compose or Gradle code, when the user asks what an Apple/Android API does or whether it is deprecated, or when you are unsure an API name or signature still exists.
allowed_tools: [bash, platform_docs]
recommends_subagent: false
prompt_index: false
---

# platform-docs — check the publisher's docs before you write the call

**Why.** Your training data stops at a date. SwiftUI, UIKit, Jetpack Compose
and the Android Gradle Plugin rename, deprecate and add APIs every few
months (`NavigationView` → `NavigationStack`, Compose's lazy-layout cache
window, AGP DSL moves). This machine keeps a local mirror of the official
pages — fetched from developer.apple.com and developer.android.com, each
page stamped with its URL and the date it was fetched. It works with no
internet. Reading it costs one command; guessing costs a build failure or,
worse, code that compiles against an API that is deprecated.

For Tailscale connectivity, load `skill("tailscale")` and search the `tailscale` source on this machine. The guide includes direct LAN recovery and credential-preserving access to beast-gate.

## The rule

**Before writing code against a platform API you are not certain about —
search the docs. Cite what you used. Say how old it is.**

1. **Search** for the symbol or the task.
2. **Read** the best hit if the snippet is not enough (declaration,
   availability, deprecation, parameters).
3. **Write the code to match the page**, not your memory. If the page says
   DEPRECATED, use what it says to use instead.
4. **Cite**: "per <URL> (fetched <date>)". If the result says **STALE**, or
   the date is more than ~90 days old, say so: "the mirror is from <date>;
   this may have changed".
5. **No hit?** Say you could not confirm it in the docs. Do not invent an
   API name, a parameter label or an availability version.

## How — two equivalent ways

**A. The `platform_docs` tool** (when it is in your tool list — the rig's
WebUI and OpenCode both have it):

```
platform_docs(query="client-side netmap caching", source="tailscale")  # outage documentation
platform_docs(query="NavigationStack")                          # search
platform_docs(query="LazyColumn item keys", source="android-compose")
platform_docs(doc="swiftui:/documentation/swiftui/navigationstack")   # read one page
platform_docs(doc="NavigationStack", source="swiftui", live=true)     # official page NOW (online only)
platform_docs()                                                 # what is mirrored, how old
```

**B. bash** (any shell on a rig or a client laptop):

```bash
# the CLI lives in the OpenBeast checkout; on a client laptop that is
# ~/.openbeast-client/repo
DOCS=$( [ -x ~/.openbeast-client/repo/scripts/docs.sh ] && echo ~/.openbeast-client/repo/scripts/docs.sh || echo ./scripts/docs.sh )

$DOCS search NavigationStack                     # ranked hits: title, doc id, URL, fetched date
$DOCS search "lazy column item keys" --source android-compose
$DOCS read swiftui:/documentation/swiftui/navigationstack
$DOCS live NavigationStack --source swiftui      # official page now; offline -> mirror copy, labelled
$DOCS status                                     # which mirrors exist, STALE flags
```

Doc ids look like `<source>:<path>` and come from search results. A URL
works anywhere a doc id does.

## Reading the output

- `fetched 2026-11-15, 3d ago` — when the page was downloaded. `confirmed
  unchanged <date>` means a later sync re-checked it (ETag / content hash).
- `STALE` — older than that source's refresh window. Still usable; say so.
- `[DEPRECATED]` / `**DEPRECATED:** iOS 26.0 — use X instead` — believe it
  over your training data.
- `LIVE FETCH UNAVAILABLE (offline: …). Serving the MIRROR copy` — the box
  is offline (OFFLINE=true or island mode); you got the mirror and its date.
- `No documentation mirror` — nobody has synced one on this machine. Tell
  the user: `scripts/docs.sh sync <source> --max-pages N` when online (Apple
  sources need `--accept-terms` once).

## Sources

`scripts/docs.sh sources` lists them: `swiftui`, `uikit`, `swift`, `xcode`,
`app-review` (Apple), `android-compose`, `android-compose-ref`,
`android-kotlin`, `android-build` (Android), and `kotlin`, `gradle`,
`blender-python`, `klipper`, `octoprint`. Adding a site is an entry in
`agents/docs/sources.toml` — see `docs/BEAST_DOCS.md`.

## What this is not

- **Not compiler-verified.** This is what the publisher wrote. For what the
  *installed* compiler accepts (zig, C/C++, Rust, Go, Python), use
  `language_reference` / the `beast-lang` skill — that one is checked on this
  machine. When the two disagree, the compiler wins.
- **Not a web search.** It only reads the mirrored sites, and live mode only
  fetches those same sites. For anything else use `web_search` / `fetch`.

## Hard rules

- Never claim an API exists, or exists on a platform version, without either
  a docs hit or saying you are going from memory.
- Never strip the URL or date when you quote the docs.
- Never run `sync` without the user asking — it downloads from the
  publisher's site. `search`, `read` and `status` are free and offline.
