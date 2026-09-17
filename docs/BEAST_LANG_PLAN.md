# beast-lang — the offline language library (design, 2026-09-15)

**Max's ask (2026-09-15):** bring down documentation for every major language,
let a local model look up the current API and version semantics without
reaching the web, pull every revision of a language, synthesize the whole
thing into succinct summaries the model can consult before it writes code,
and probably ship a skill.

**And the justification Max added on the day (2026-09-15), which belongs at
the top rather than in a footnote: this is what lets us run projects with no
internet at all.** OpenBeast's whole premise is "no cloud, no API keys", and
beast-lang is the piece that finishes the sentence. A model can only write
*current* code if something on the machine knows what current means — and
without this, that knowledge comes from a web search or from weights frozen
two versions ago. With it, it comes from the compiler sitting next to the
model. A rig in a closed network, on an air-gapped site, or on a plane writes
the same correct zig 0.16 as one with a fibre line.

That reframes the feature from "nice documentation tooling" to **a
prerequisite for closed-network deployment**, which is a situation Max
expects to be in.

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

### 2.3 Max's correction (2026-09-15): measurability is not the scope gate

I originally proposed gating the language list on what our suite can measure.
Max rejected that, correctly:

> *"I want this implemented so that ANY model dropped in (irrespective of our
> 27B testing) can automatically and auto-magically know how to use a
> language. Yes, in our case Zig and C++ are saturated with errors, and that's
> fine. This is to future proof us in the case that in the future we need to
> pull in this information for a model. What we can do is FF certain languages
> via an allow list for an openbeast-deployment."*

The point is infrastructure, not this quarter's model. A pack is a statement
about a **toolchain**, not about our 27B — it stays true when the model is
replaced, and the next model dropped in may be strong enough to use notes our
current one ignores. Gating on our own measurability would have built a
feature that only ever serves the model we happen to be running.

So the design changes in one specific way: **which languages are active is a
DEPLOYMENT decision, expressed as an allow list.**

- `LANG_PACKS=auto` (default) — every language with an installed toolchain and
  verified claims. This is the "auto-magically" part: drop a model in, and it
  gets whatever this machine can prove.
- `LANG_PACKS=cpp,zig` — one rig only writes C++ and zig and pays nothing for
  the rest.
- `LANG_PACKS=off` — disabled. An explicitly empty value means off too; only
  an absent key means `auto`.

Measurement keeps its own separate life: the zig A/B still tells us whether
the push layer moves *our* model, and harder per-language units remain worth
authoring for that purpose. But they are no longer a precondition for shipping
the capability.

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

## 5. The gate (answered in §9.6 — kept for the reasoning)

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

## 9. Decisions — answered by Max, 2026-09-15

1. **Name: `beast-lang`.** Locked.
2. **Language scope: build them all; the deployment picks** (§2.3). Not gated
   on what our eval can measure.
3. **Swift.** No toolchain on this rig, so every Swift claim is
   `UNVERIFIABLE` by construction. Resolved in a way that serves both of Max's
   answers: Swift is **acquired and kept as CURATED-only** — the corpus exists
   so a rig that *does* install a Swift toolchain gets it for free, the claims
   are labelled unverified, and `active_packs()` can never serve them because
   `pack_for()` requires an installed toolchain. Nothing is dropped, and
   nothing unverified can reach a model.
4. **Rust offline docs: reference git + toolchain introspection**, no prose
   corpus (Max: "whatever you deem is best"). No rustup on this rig, and
   `rustc` is the authority anyway.
5. **GPU for synthesis: when Max goes to bed.** Phases 0-2 needed none.
6. **Stopping rule after the zig A/B: my call** — a null result does not kill
   beast-lang, it moves the bet from the push layer to the escalation layer
   (§7 P4), which is better targeted regardless.

## 10. Status

- **Phase 0 — the library: SHIPPED** (PR #65). `scripts/lang-library.sh
  acquire|check|list|verify|pack|where`.
- **Phase 2 — the verifier: SHIPPED** (PR #66). 25 claims VERIFIED across
  zig/cpp/python; three of the first ten claims were wrong and it caught all
  three.
- **Pack resolution + the allow list + the drift guard: SHIPPED** (this
  change). `agents/lang/packs.py`.
- **Phase 1 — toolchain introspection: SHIPPED.** `agents/lang/introspect.py`
  + `scripts/lang-introspect.sh`. Six probes, all mechanical, none of which
  executes a snippet or imports a module:

  | lang | what the toolchain is asked | what it yields here |
  |---|---|---|
  | **cpp** | `g++ -std=<lvl> -dM -E` at five levels, diffed | the AVAILABILITY axis, generated: `__cpp_concepts` appears at c++20 and is absent at c++17, so "concepts need C++20" is *observed*. Plus `clang++` as a second opinion — 40 feature macros the two compilers disagree about, i.e. exactly where a claim proved on one is not a language fact |
  | **c** | the same, four levels | only `__STDC_VERSION__` MOVES between c99 and c23 — nothing is newly defined — so an additions-only map rendered C as having no facts at all. Value changes are tracked for this reason |
  | **zig** | `zig env` → parse `lib/std/std.zig` | 89 top-level names, 58 of them namespaces, at their exact spelling. `std.Io` vs `std.io` is the suite's most expensive trap and a model handed the real list does not have to guess |
  | **python** | `sys.stdlib_module_names` | 297 modules of the interpreter that will actually run the code |
  | **go** | `go list std` | 362 packages |
  | **rust** | `rustc --edition=N --emit=metadata` | which editions the installed rustc ACCEPTS — the refusal is the fact |

  Three design decisions worth keeping:

  1. **Nothing serves from a file.** A full probe of all six costs ~0.2s
     (measured), so the pack path asks the toolchain live on every call. That
     is the L1 rule — the installed toolchain is ground truth — made
     structural: there is no stale-artifact path to guard because there is no
     artifact on the serving path. `agents/lang/generated/` is gitignored
     per-rig state, like the L0 corpus. `check` exists for the one question a
     written file can still get wrong: *was it edited?*
  2. **The allow list is no longer gated on hand-authored claims.** It was,
     and that made L1 pointless: `go` — whose toolchain is installed and can
     be asked directly — was unreachable no matter what `LANG_PACKS` said.
     Eligibility is now *claims ∪ probes*, which is what "any model dropped
     in can know how to use a language, irrespective of what we tested"
     actually requires. Breadth is close to free: a pack is injected only for
     the language a task is in. This rig now serves **six** languages; it
     served three.
  3. **GENERATED lines are labelled and dropped first.** The header says how
     many lines were confirmed by compiling and how many by asking the
     toolchain, because the two tiers earn belief differently — and under
     budget pressure the generated tier is what goes, since VERIFIED means a
     fixture proved a migration.

  Two bugs this phase produced and the tests now pin: `check` compared the
  stored `sha256` *field* against a fresh probe, so a hand edit that left the
  field alone reported **OK** — the one thing it exists to catch; and the
  rendering budget was counted in LINES, so a twelve-line cap was a 5,500
  character pack. The budget is characters, the truncation notice has room
  reserved *before* facts are spent, and both are mutation-checked.
- **Phase 4 — escalation (Tier 1.5): THE MATCHER IS BUILT AND MEASURED.**
  `agents/lang/escalate.py`. A compile error selects the card that fixes it,
  and the matching index is **generated, not hand-written**: every claim
  already ships OLD fixtures that must fail, so compiling them yields the real
  diagnostic a model will see. Nobody guesses what zig says about `std.io`;
  zig is asked. 86 signatures across zig/cpp/python from 49 real diagnostics.
  - **Round-trip** (the index maps its own evidence): 49/49.
  - **Held-out** (stale code the index has never seen): **14/14 have the right
    card in the top 3, 13/14 rank it #1.** The one exception is the `std.io`
    ambiguity where `stdin` outranks `stdout` — both cards are true, and the
    shipped 2-card limit delivers the right one either way.
  - Refuses to speak when it should: no match, no index, a claim with no
    one-line summary, or an index whose stamped toolchain differs from the
    installed one (diagnostics move between releases).
  - **What building it exposed:** every zig claim was VERIFIED but
    UNDELIVERABLE — the set is generated from the fixture manifest, which
    carried no `summary` — so escalation silently matched nothing for the one
    language it matters most for (zig is 9/30 on the suite; everything else is
    saturated). Summaries now live in `MANIFEST.json`, the source of truth.
  - **Still to do: the wiring, ~5 lines, era-locked.** The checker that
    produces the diagnostic lives in `agents/tools.py` and the injection point
    in `agents/runner.py` — both are cache-hashed, and the Tier-3 A/B has five
    cells outstanding. `runner.py --context-file` already exists, so no new
    plumbing is needed once the lock lifts.
- **Phase 5 — the pull surface: SHIPPED.** Both halves, and neither touches
  the runner.
  - **`language_reference(language, topic="", error="")`** on the MCP/WebUI
    surface (`agents/mcp_server.py`, `agents/openapi_tools.py`; 17 → 18
    tools). `error` → the escalation cards for that diagnostic, `topic` → the
    VERIFIED claims whose topic / summary / named identifiers match, most
    specific first, plus what the toolchain says about that ONE name
    (GENERATED, labelled: "`std.io` does NOT exist … the installed spelling is
    `std.Io`"); neither → the pack. It goes through two new facade calls
    (`safe_reference`, `safe_languages` — no-raise, silent under eval), is
    imported lazily, clips at ~8 KB on a line boundary and says so, and answers
    an unserved language with the list this rig *can* serve. **It never
    improvises:** no fuzzy match, no third tier; a miss is "no verified
    reference for …". Admin profile only — `GUEST_TOOLS` stays web-only.
    **Not in the runner's registry** (§7 P5: it must clear a v5-suite eval
    first), which a test asserts by *reading* `agents/tools.py`.
  - What building it exposed: a bare topic like `maketrans` produced
    "`import maketrans` does NOT exist" — true, useless, and it read as a
    verdict on the function the caller asked about. An ABSENCE line is now
    stated only when the caller named the namespace (`import imp`, `std.io`).
  - **The `beast-lang` skill** (`skills/beast-lang/SKILL.md`): what the library
    is, how to look something up, how to add a claim, what each verdict means,
    the hard rules. It carries `prompt_index: false`, a new generator opt-out:
    the skill menu lives in `system-prompt-tools.md`, which is era-locked, so
    before this *any* new skill either rolled the eval era or failed
    `test_scripts.sh`. It is the right answer on its merits too — skills fire
    ~0% on local models (§2.1) and the menu is paid for every turn.
- **Phase 3 — synthesis: THE HARNESS IS BUILT; the real run is pending the
  GPU.** `agents/lang/synthesize.py` + `scripts/lang-synthesize.sh`. Note that
  a pack does NOT need an LLM: the verified claims already *are* the summary,
  one `summary` line each. The local model's job in phase 3 is to DRAFT new
  candidate claims from the corpus, which the verifier then accepts or
  rejects. That ordering is what keeps a model-written claim from ever
  reaching a pack unverified, and it is now structural:

  ```
  L0 corpus + L1 facts -> bounded prompts -> model -> strict parse
     -> duplicate check -> verify.verify (the REAL drivers) -> claims/staging/
                                        reviewed by a person -> promote
  ```

  - **Tested end to end with a stub model; no endpoint has been touched.** The
    model is an interface (`draft(prompt) -> str`). The HTTP client has **no
    default URL** — `OPENBEAST_LANG_SYNTH_URL` unset is a refusal, never a
    fallback to whatever `:8080` is serving — and `draft` refuses while
    `scripts/gpu-lease.sh status` says `HELD` by anyone but its own
    `gpu-lease.sh run` ancestor. Unknown is not free either.
  - **Never repaired.** Prose, fences, `<think>` blocks and a broken envelope
    are tolerated; a *record* is taken only if it parses exactly as written. A
    trailing comma or a reply cut off mid-record costs that record, counted.
  - **Two holes closed before they shipped.** `verify.Claim` reads a one-line
    snippet ending in `.py`/`.c` as a fixture *path*, so a drafted
    `"new": ["/home/u/secret.py"]` was a file-read primitive — refused, and the
    Claim is built from newline-terminated snippets as the belt. And `verify()`
    calls a claim with no OLD form VERIFIED; a drafted claim must show what
    stopped working.
  - **Staging is not served** (`load_claims` lists one directory, no
    recursion — pinned by a test). `promote` re-validates hand edits,
    re-verifies, is all-or-nothing, writes `claims/<lang>-synthesized.json`
    (never a hand-authored set, never the generated zig set) and rebuilds that
    language's escalation index.
  - **A bug this found in phase 4:** `escalate.py --rebuild --lang X` wrote the
    one-language result over the whole index, deleting every other language;
    a full `--rebuild` on a box without zig deleted zig. `write_index()` now
    merges, atomically, byte-identical for a full rebuild.
  - **Still to do: the run itself** (§11), then a human review of whatever it
    stages. P3's *other* output in §7 — model-written pack prose — is
    deliberately NOT built: every pack line is a claim summary or a generated
    fact, and a free-prose tier would be CURATED by definition.

## 11. How to run synthesis

Not before the GPU campaign is over: drafting runs on the card, and every
candidate is compiled on the CPU next to whatever is being measured.

```bash
cd ~/Documents/openbeast                 # the MAIN tree, not a worktree: the
                                         # lease file lives in ITS .run/, and a
                                         # worktree's copy of gpu-lease.sh reads
                                         # an empty one and says FREE
./scripts/gpu-lease.sh status            # must say FREE
./scripts/lang-library.sh list           # is there a corpus for the language?

# 1. Look at what WOULD be sent. No model call, no candidate compiled, no lease.
./scripts/lang-synthesize.sh draft python --dry-run --match 'whatsnew/3\.1[2-4]'

# 2. The run. The endpoint is this rig's own llama-server (./start.sh, the
#    default model — today Qwen3.8-27B-Uncensored MTP Q5 on :8080); there is no
#    default, so it is named explicitly, and the lease is held for the run.
OPENBEAST_LANG_SYNTH_URL=http://127.0.0.1:8080/v1 \
  ./scripts/gpu-lease.sh run "beast-lang P3 python" -- \
  ./scripts/lang-synthesize.sh draft python --match 'whatsnew/3\.1[2-4]'

# 3. Review. Is each summary TRUE and useful, not merely compilable? Delete
#    the ones that are not, straight out of the staging file.
./scripts/lang-synthesize.sh status
$EDITOR agents/lang/claims/staging/python-<date>.json

# 4. Promote what survived (re-verifies; all or nothing; rebuilds the index),
#    then the usual gates.
./scripts/lang-synthesize.sh promote agents/lang/claims/staging/python-<date>.json --all
python3 agents/lang/verify.py --lang python && python3 agents/lang/escalate.py --check
```

Knobs: `OPENBEAST_LANG_SYNTH_KEY` (bearer token, when `LLAMA_API_KEY` is set
on the rig), `OPENBEAST_LANG_SYNTH_MODEL` (llama-server ignores it),
`OPENBEAST_LANG_SYNTH_THINKING=off` (sends `enable_thinking: false`; unset
sends nothing), `OPENBEAST_LANG_SYNTH_MAX_TOKENS` (default 16384 — a reasoning
model that spends its budget thinking returns an empty answer, which would
read as malformed), `--max-prompts` (8), `--max-candidates` (40),
`--max-prompt-chars` (12000), `--source FILE` for material outside the library
(zig's release notes, say — there is no zig corpus directory on this rig, and
with no source material the run exits 3 rather than draft from thin air). The
JSON report lands in `.run/lang-synth/`; exit codes are 0 ok · 2 config ·
3 no corpus / no toolchain · 4 lease held · 5 every model call failed.

Order of languages, by where the instrument has range (§2.2): **zig first**
(needs `--source` with the 0.15/0.16 release notes, or an acquired corpus),
then python and cpp, whose corpora are already on disk.
