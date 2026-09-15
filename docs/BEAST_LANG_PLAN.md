# beast-lang — the offline language library (design, 2026-09-15)

**Max's ask (2026-09-15):** bring down documentation for every major language,
let a local model look up the current API and version semantics without
reaching the web, pull every revision of a language, synthesize the whole
thing into succinct summaries the model can consult before it writes code,
and probably ship a skill.

This document is the plan. It is deliberately argumentative in two places,
because the repo already has measured evidence that contradicts the obvious
version of this feature, and building the obvious version would repeat a
deletion we already made on purpose.

---

## 0. The name is not beast-lsp

`LSP` means Language Server Protocol. We installed four real language servers
on this rig this morning (`gopls`, `rust-analyzer`, `pyright-langserver`,
`bash-language-server`), and `docs/LANG_AWARENESS_PLAN.md §4` is a parking
note for a *deleted* LSP sidecar tier. A third thing called "beast-lsp" would
collide with both.

**Proposed: `beast-lang`**, and the corpus it owns is **the library**.
`beast-ref` is the runner-up. Naming is Max's call; everything below is
independent of it.

---

## 1. What already exists — do not rebuild it

| Tier | What | State |
|---|---|---|
| **1 — push diagnostics** | after every write, the language's checker runs and its output lands in the tool result | **shipped** as beast-assist (v1.2.0), opt-in, honestly documented as a small effect inside a ±5-14 churn floor |
| **1.5 — doc-site escalation** | a compile error *triggers* a documentation lookup | **banked, unbuilt** (`origin/docs/lang-pack-abstraction`) |
| **2 — LSP sidecar** | warm language servers, hover/signature tools | **DELETED** by adversarial review. Reasons in §4 of that doc, and one of them is the load-bearing constraint for this whole design — see §2 below |
| **3 — awareness packs** | a ≤2,000-token version-pinned digest injected into the system prompt for the task's language | **built for zig only** (`agents/packs/zig-0.16.md`), opt-in `BEAST_PACKS=1`, **its A/B is running on the GPU as this is written** |
| **4-6 — language-pack abstraction, go/C/C++ edition axis, per-project toolchain version gap** | banked design notes | **banked, unbuilt** (`origin/docs/cpp-go-editions`, `origin/docs/zig-version-gap`) |

beast-lang is the generalization of Tier 3 with a real corpus behind it, plus
the Tier 1.5 escalation that was banked and never built. It is not a new
direction; it is the direction, scaled — and the zig pack is its working
prototype, including the parts that are hard (verification, era policy,
provenance, budget).

---

## 2. Two measured facts that shape the design

### 2.1 Local models do not call optional tools

This is the repo's own finding, and it is why Tier 2's pull half was deleted:

> *The pull half (hover/signature tools) contradicts §1's own evidence (models
> don't call optional tools), and cannot fix staleness: hover on a removed
> symbol returns nothing; discovering the new name is knowledge = Tier 3.*
> — `docs/LANG_AWARENESS_PLAN.md §4`

The production roadmap records the same thing about skills: **skills fire
~0% on local models.** A 27B model in a tool loop does not stop to consult a
reference; it writes the code it believes in.

So "a system for any local AI to be able to look up the language" cannot be
*primarily* a lookup. Delivery has to be, in priority order:

1. **PUSH** — the harness injects the right pack for the task's language,
   unasked. This is Tier 3, already built for zig.
2. **ESCALATE** — a *compile error* triggers a targeted card injection on the
   next turn. Triggered, not optional, and it fires exactly when the model has
   demonstrated it holds a stale belief. This is Tier 1.5, and it is the
   highest-value unbuilt piece in the repo.
3. **PULL** — a `language_reference` tool, last, and only if it earns a
   v5-suite win. For a *cloud* model (Claude Code working in this repo) pull
   is fine and a skill is the right shape; for the local 27B it is decoration
   until measured otherwise.

### 2.2 Our eval cannot measure a pack for any language except zig

From the IQ3 capability rows measured yesterday, per-language passes out of
the v5-fast 112:

| language | GSQ-RCO row | UD row |
|---|---|---|
| python | 28/28 | 25/28 |
| rust | 11/11 | 9/11 |
| go | 18/19 | 19/19 |
| c | 13/14 | 13/14 |
| cpp | 9/10 | 8/10 |
| **zig** | **9/30** | **2/30** |

Every language except zig is at or near saturation. **A pack for Rust cannot
be shown to help on this suite, because there is no headroom to move.** zig is
the only language where the instrument has range, which is precisely why the
zig pack was built first and why its A/B is the one that exists.

The consequence is a scheduling fact, not an opinion: **building packs for
five more languages buys product value and zero evidence.** If we want
evidence per language, the suite needs harder units in that language first —
and authoring those is real work (each needs a deterministic checker and a
tripwire classification).

---

## 3. Architecture — four layers

```
L0  THE LIBRARY          acquired, versioned, provenance-stamped corpus on disk
                         (NOT in git — see §6)
        |
L1  GROUND TRUTH         the INSTALLED toolchain, introspected.
                         Authoritative over every document in L0.
        |
L2  SYNTHESIS            (a) always-on pack   <=2k tokens, per installed version
                         (b) topic cards      ~200-400 tokens each, retrievable
                         (c) migration maps   version N -> version M deltas
        |
L3  DELIVERY             push (per-task language) | escalate (on compile error)
                         | pull (tool/skill, gated)
```

### L1 is the layer that matters most

The zig experience settled this: **documents tell you what the language says;
the installed compiler tells you what will compile, and only the second one
matters to an agent about to write a file.** `zig 0.16` removed `std.io`
outright while every document, blog post and model weight on earth still
describes it.

So the rule is: **a document may propose a claim; only the toolchain may
confirm one.** Concretely, every synthesized line is one of

- **VERIFIED** — a compile fixture proves the NEW form compiles and (where
  it is a migration claim) the OLD form fails, on the installed toolchain.
  Only VERIFIED lines are ever auto-injected.
- **GENERATED** — mechanically extracted from the installed toolchain
  (signatures, symbol lists), checksum-pinned and regenerable. Safe to inject.
- **CURATED** — a human or a model wrote it and no fixture covers it. Labelled
  as such, never checksum-pinned as if generated, and **never auto-injected.**
  Available to pull, marked unverified.

`agents/packs/gen_zig_pack.py` + `tests/fixtures/zig016/` (39 OLD fixtures
that must fail, 35 NEW that must compile, cross-checked both ways by a
manifest) is the existing implementation of exactly this discipline. beast-lang
generalizes that machinery; it does not invent it.

---

## 4. Per-language feasibility — probed 2026-09-15, not recalled

Installed on this rig right now: `zig 0.16.0`, `go 1.26.2`, `rustc 1.98.1`,
`gcc 16.2.1`, `clang 22.1.8`, `python 3.14.7`. **No Swift toolchain.**

| language | best offline source | revision axis | verifiable here? |
|---|---|---|---|
| **zig** | the installed `lib/std/*.zig` source itself (no docs artifact exists; `zig env` is ZON, `lib_dir` resolves) + per-release tarballs from `ziglang.org/download/index.json` ✅ reachable | release tags 0.11…0.16 | **yes** — `zig build-exe -fno-emit-bin` |
| **C++** | `cppreference` offline archive ✅ (13.2 MB `.tar.xz`, latest tag `v20250209`) — already annotated per standard, which *is* the revision axis; plus WG21 working drafts (PDF) | C++98/03/11/14/17/20/23/26 via cppreference's own `since-c++N` markers | **yes** — `g++ -std=c++N -fsyntax-only` against two compilers (gcc 16, clang 22) |
| **C** | WG14 drafts ✅ (`n3220.pdf` reachable) + cppreference's C section | C89/99/11/17/23 | **yes** — `gcc -std=cN -fsyntax-only` |
| **Go** | `$GOROOT/doc` on disk ✅ + `go doc` for every std package, offline + the spec in the go repo ✅ | release notes per `go1.N` | **yes** — `go vet` / `go build` |
| **Rust** | the reference git ✅ + std docs (**gap**: no `rustup` here, this is the Arch system package, and `rust-docs` did not resolve as a package — needs a decision, see §9) | editions 2015/2018/2021/2024 + stable releases | **yes** — `rustc --edition N --emit=metadata` |
| **Python** | the stdlib source is on disk ✅ (`/usr/lib/python3.14`) + official per-version doc bundles (the exact archive URL I guessed 404'd — resolve at acquisition time, never hardcode) | 3.x minor versions | **yes** — `py_compile` + `ast` |
| **Swift** | `swift-book` git ✅ (Apache-2.0) + `swift-evolution` proposals as markdown ✅ | Swift 4/5/6 language modes | **NO — no toolchain installed.** A Swift pack would be CURATED-only and therefore never auto-injected. Either install a toolchain or accept Swift as pull-only |

Two of the ten URLs I assumed from memory returned 404. **Design consequence:
resolve artifacts through version indexes and release APIs at acquisition
time; never hardcode an artifact URL.** The acquirer must fail loudly on a
miss rather than silently shipping a stale corpus.

### On "every revision via PDF"

Honest answer: **PDF is the worst available source and is only necessary for
C and C++.** Everything else has a machine-readable artifact — a git repo, a
doc tarball, or the installed source tree. PDF extraction destroys exactly the
structure we need (code blocks, signatures, version markers). So the acquirer
is source-shaped per language, with PDF as the documented fallback for WG14 /
WG21 drafts only.

Also worth correcting the framing: the authoritative offline corpus is **not
massive.** cppreference is 13 MB compressed. The whole primary corpus across
seven languages is likely under 1 GB, against 395 GB free. What is large is
the *synthesis* space — topics × versions × languages — and that is bounded by
GPU time, not disk.

---

## 5. The gate — and why this needs Max's explicit call

The Tier-3 zig A/B is on the GPU right now. Its pre-registered ship rule is
`net ≥ 7 ∧ p < 0.05 ∧ champion guard clean`, and the campaign's **Clause 2
says stop after this arm regardless.**

That clause exists because the beast-assist campaign taught us the expensive
version of this lesson: seven paired cells, two models, and the honest verdict
was "effect ≈ +2-5 net on the targeted language, inside a ±5-14 churn floor,
flat-null on the champion." We shipped it default-OFF and assigned the decisive
proof to Tier 3.

So, plainly:

- **If the zig pack wins its A/B**, the push layer is validated and scaling to
  more languages is justified — for the languages where we can *measure* it,
  which today is only zig (§2.2).
- **If it does not win**, a 2k-token push pack does not move a 27B model, and
  the bet moves to the **escalation** layer (inject on demonstrated error),
  which is a different and better-targeted mechanism.

Either way, L0 (acquire) and L1 (verify) are worth building now: they are the
long pole, they need no GPU, and every later layer depends on them. **L2
synthesis is where the GPU cost and the open questions live, and it should not
start before the verdict.**

---

## 6. Licensing and what ships

- **cppreference** is CC-BY-SA. Derived summaries inherit share-alike, so
  shipping them in an Apache-2.0 repo needs attribution and care.
- **ISO standards** are not redistributable; the WG14/WG21 *drafts* are the
  free, citable artifacts.
- Therefore: **ship the acquirer and the synthesizer, not the corpus.** The
  library lives outside git at `$OPENBEAST_LANG_DIR` (default
  `weights/../lang-library/`), exactly as model weights and bulk research data
  already do. `openbeast doctor` reports its presence, version and staleness.
- Any pack committed to the repo carries a provenance header naming its
  sources and their licenses — the zig pack's header is the template.

---

## 7. Phases

**P0 — the library (no GPU, no era roll).** `scripts/lang-library.sh acquire`
per language: resolve the artifact through an index/API, fetch, checksum,
unpack into `$OPENBEAST_LANG_DIR/<lang>/<version>/`, write a provenance
manifest (source URL, sha256, license, fetch date, tool version it describes).
Idempotent, resumable, `--check` verifies what is on disk against the manifest.

**P1 — toolchain introspection (no GPU).** One `introspect` module per
language producing the L1 ground truth: symbol tables with signatures, from
the installed toolchain only. `gen_zig_pack.py` is the reference
implementation; the work is abstracting its area/rank/budget machinery behind
a per-language driver. Output is checksum-pinned and regenerable.

**P2 — the verifier (no GPU, needs compilers).** Generalize
`tests/fixtures/zig016/`: a claim is a `{lang, version, old, new, topic}`
record, and the verifier compiles both halves on the installed toolchain and
stamps VERIFIED / GENERATED / CURATED. **This is the gate every synthesized
line must pass**, and it is what makes the corpus trustworthy rather than
merely large.

**P3 — synthesis (GPU).** For each `(language, version)`: a ≤2k-token pack and
a set of topic cards, drafted by the local model from L0 + L1 and then **run
through P2**, with unverifiable lines demoted to CURATED rather than silently
kept. This is the step that turns "a massive repository" into "succinct
summaries", and it is the one that needs the GPU.

**P4 — escalation (Tier 1.5) (small GPU cost to A/B).** When the checker
reports an error whose shape matches a known migration class, attach that
topic card to the next turn. Triggered by evidence, cheap, and aimed at the
exact moment the model is provably wrong. On the current suite this is the most
promising unbuilt mechanism in the repo.

**P5 — pull surfaces (gated).** A `language_reference` tool for the MCP/WebUI
surface (**not** the runner's 10-tool registry — tool-selection accuracy at
27B degrades with registry size, which is why beast-artifact's tools stayed
off it), plus an agent **skill** for cloud models working in this repo. Both
must clear a v5-suite eval before joining the runner registry.

---

## 8. What this does NOT become

- Not an LSP sidecar. §4 of the language-awareness plan deleted that with
  measured reasons; nothing here revives it.
- Not a RAG system over documentation. The local model will not query it
  (§2.1), and retrieval quality is unmeasurable against a saturated suite.
  Selection is done by the *harness*, from the task's language and the
  *checker's error*, both of which are known facts rather than guesses.
- Not a web fetcher at agent time. Acquisition is an explicit, offline,
  operator-run step. The agent never reaches the network for documentation.

---

## 9. Open questions — Max's call

1. **Name.** `beast-lang` (recommended) / `beast-ref` / something else.
2. **Language scope for v1.** Recommended: **zig, C++, C, Go, Rust, Python**
   — the six the eval suite actually covers and the toolchains we can verify
   against. **Swift is the odd one out**: no toolchain here, so it would be
   CURATED-only and never auto-injected. Install a Swift toolchain, or accept
   Swift as pull-only-and-unverified, or drop it from v1?
3. **Rust offline docs.** No `rustup` on this rig (Rust is the Arch system
   package) and `rust-docs` did not resolve as a package. Options: install
   `rustup` alongside for its doc component, use the reference git plus
   toolchain introspection only, or skip Rust's prose corpus and do
   signatures-only. Recommended: reference git + introspection; prose is the
   least load-bearing part.
4. **Suite headroom.** Do we author harder units for C++/Rust/Go/Python so a
   pack for those languages can be *measured*? Without it we are shipping
   product value on faith for five of six languages. This is the single
   biggest scope decision in the plan.
5. **GPU budget for P3.** Synthesis is the only GPU-hungry phase. Schedule it
   after the current queue (~26 h), or interleave?

I am proceeding with **P0 → P1 → P2** now, because they need no GPU, roll no
cache era, and are prerequisites under every answer to the questions above.
