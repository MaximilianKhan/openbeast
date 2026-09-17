---
name: beast-lang
description: Work with beast-lang, this repo's offline language library — look up what the INSTALLED compiler confirmed about zig/C/C++/Rust/Go/Python, add or fix a verified claim, rebuild the escalation index, review model-drafted claims. Activate when touching agents/lang/, agents/packs/, tests/fixtures/zig016/ or scripts/lang-*.sh, when a task says "pack", "claim", "escalation card" or "language_reference", or when you are about to write zig 0.16 / C++ / Python code here and are unsure an API still exists.
allowed_tools: [bash, read_file, write_file, edit_file, grep, language_reference]
recommends_subagent: false
prompt_index: false
---

# beast-lang — the offline language library

**What it is.** A corpus of facts about programming languages in which every
line was confirmed by the toolchain *installed on this machine*. The rule the
whole package hangs on (`docs/BEAST_LANG_PLAN.md` §3):

> A document may PROPOSE a claim; only the toolchain may CONFIRM one.

Three tiers, and only the first two are ever shown to a model:

| tier | meaning | where |
|---|---|---|
| **VERIFIED** | a *claim*: the OLD form was compiled and **failed**, the NEW form **compiled**, here | `agents/lang/claims/*.json` |
| **GENERATED** | the toolchain was *asked* (std's top-level names, feature macros per `-std`, stdlib modules) | `agents/lang/introspect.py`, live, never served from a file |
| **CURATED** | somebody wrote it and no fixture covers it | never auto-injected |

Delivery is **push** (a ≤2k-token pack for the task's language), **escalate**
(a compile error selects the card that fixes it) and, last, **pull** (the
`language_reference` tool and this skill). Local models do not call optional
tools (§2.1) — pull is for you, a cloud model, and for people.

This skill is `prompt_index: false`: it is reachable with `skill("beast-lang")`
but deliberately not in the local model's always-on menu in
`system-prompt-tools.md` (that file is hashed into the eval cache era).

## Look something up

```bash
./scripts/lang-library.sh where            # where the L0 corpus lives (outside git)
./scripts/lang-library.sh list             # what was acquired, with provenance
./scripts/lang-library.sh pack [lang]      # what a model would receive; --print for the text
python3 agents/lang/verify.py [--lang zig] # every claim, judged on THIS machine, now
python3 agents/lang/escalate.py --check    # is the committed error->card index still true here
python3 agents/lang/escalate.py --lang zig --diagnostic "error: ... no member named 'io'"
python3 agents/lang/reference.py zig std.io     # one topic: VERIFIED cards + GENERATED facts
./scripts/lang-introspect.sh render zig    # the GENERATED lines, straight from the toolchain
```

On the MCP/WebUI surface the same three lookups are one tool:
`language_reference(language, topic="", error="")` — `error` → the cards for
that diagnostic, `topic` → the matching verified lines, neither → the pack. It
never improvises; "no verified reference for …" is a real answer and means
*read the compiler's message*, not *trust your memory*.

**Before writing zig here, read the pack** (`agents/packs/zig-0.16.md`, or
`lang-library.sh pack zig --print`). `std.io`, `std.fs.File`, `std.time`,
managed `ArrayList.init` are all gone in 0.16 and your training data says
otherwise.

## Add a claim

A claim is one JSON record in `agents/lang/claims/<lang>-*.json`:

```json
{"id": "string-maketrans-gone",
 "topic": "str translation",
 "summary": "`string.maketrans` is gone (Python 2) — use `str.maketrans(...)` then `s.translate(t)`",
 "old": ["import string\nt = string.maketrans('a', 'b')\n"],
 "new": ["s = 'abc'\nt = str.maketrans('a', 'b')\nout = s.translate(t)\n"],
 "doc_must_contain": ["str.maketrans"],
 "note": "why this is true, for the next maintainer"}
```

- `old` — snippets that **must FAIL** on the installed toolchain. `new` —
  snippets that **must COMPILE**. Both halves are compiled; that is the claim.
- **AVAILABILITY claims** (C/C++/Rust: "this needs C++20") give `new` only plus
  `"old_variant": "c++17", "new_variant": "c++20"` — the *same* code is compiled
  under both levels. Do not duplicate the snippet into `old`; a copy drifts.
- **`summary` is mandatory in practice.** It is the one line that ships — the
  fixtures are whole programs and never reach a model. A claim without it
  verifies and is then **undeliverable**: no pack line, no escalation card
  (every zig claim was silently in that state once). Put the exact names in
  `` `backticks` `` — the topic lookup and the duplicate check read them.
- A snippet is inline code. A one-line string ending in `.zig/.c/.cpp/.rs/.go/.py`
  is read as a fixture *path* relative to the set's `fixture_dir`.
- **zig is different:** `agents/lang/claims/zig-0.16.json` is GENERATED. Add the
  entry (with its `summary`) to `tests/fixtures/zig016/MANIFEST.json`, add the
  `<entry>.old.N.zig` / `<entry>.new.N.zig` fixture files, then
  `python3 agents/lang/claims/regen_zig.py` (`--check` is what CI runs).

Then, in this order:

```bash
python3 agents/lang/verify.py --lang <lang>          # must print VERIFIED for yours
python3 agents/lang/escalate.py --rebuild --lang <lang>   # compile the OLD forms, index their REAL diagnostics
python3 agents/lang/escalate.py --check              # committed index == a rebuild
python3 -m pytest tests/test_lang_verify.py tests/test_lang_escalate.py tests/test_lang_packs.py -q
```

Read the verdict, do not argue with it:

| verdict | what it is telling you |
|---|---|
| `NOT_A_BREAK` | the old form still compiles — it was not removed; do not present it as gone |
| `NEW_FAILS` | your replacement does not compile — the claim would teach something false |
| `BACKWARDS` | old compiles, new does not — you have it exactly inverted |
| `FIXTURE_BROKEN` | both fail — the snippet is wrong, not the language |
| `UNVERIFIABLE` | no toolchain here (Swift), a timeout, or a refused snippet — **never** a pass |

The rebuild prints which claims **no error message can select** (their OLD form
fails with nothing but compiler boilerplate). That is an honest outcome — the
card still ships in the pack — not something to fix by hand-editing the index.
The index is generated from real diagnostics; a hand-written signature is a
guess about somebody else's error format.

## Model-drafted claims (phase 3)

`./scripts/lang-synthesize.sh draft <lang>` has a model read the L0 corpus and
draft candidates; each goes through the same verifier; survivors land in
`agents/lang/claims/staging/`, which **nothing serves**. Review them — *is the
summary true and useful, not merely compilable?* — delete what is not, then
`./scripts/lang-synthesize.sh promote <file> --all` (re-verifies, all-or-nothing,
rebuilds the index). `--dry-run` prints the prompts and touches nothing. It is
a GPU job: it refuses while `scripts/gpu-lease.sh status` says `HELD`, and it
has **no default endpoint** (`OPENBEAST_LANG_SYNTH_URL`).

## Hard rules

1. **No driver executes a snippet.** Compile, syntax-check or type-check only.
   Python gets *static* attribute resolution in a separate, isolated
   interpreter — never a run. Phase 3 has a model writing these; a verifier
   that runs them is remote code execution in a test harness's costume.
2. **A refusal or a timeout is not a verdict.** A snippet that reaches for a
   host file (`#include "/etc/passwd"`, `@embedFile("/…")`, `include_str!`,
   `import unittest.__main__`) is refused *before* the toolchain sees it, and a
   refused OLD form has **not** been shown to fail. Do not loosen the scans in
   `drivers.py` to make a claim pass; change the claim.
3. **`-pedantic-errors` is load-bearing.** Without it gcc accepts its GNU
   extensions at every `-std` level, so "this needs C++20" cannot fail and
   every availability claim becomes unfalsifiable.
4. **Never hardcode an artifact URL.** The acquirer resolves through version
   indexes / release APIs at acquisition time and fails loudly on a miss. Two of
   ten URLs recalled from memory while writing the plan were already 404.
5. **A false "confirmed" is worse than silence.** Packs, cards and
   `language_reference` all say *confirmed on this machine*. Never add a fuzzy
   match, a "did you mean", a fallback tier or a generic-error card to look
   helpful — when in doubt the answer is nothing. (`escalate.py`'s DECOYS exist
   because the first matcher handed the ArrayList card to every type mismatch.)
6. **The drift guard is not optional.** A pack, an index entry or a generated
   fact stamped for another toolchain version is refused, not served.
7. **The corpus stays out of git** (cppreference is CC-BY-SA, ISO drafts are
   not redistributable). Ship the acquirer, not the library.
8. **Era lock.** `agents/runner.py`, `agents/tools.py`, both system prompts,
   `opencode.json` and `evals/SUITE_VERSION` are hashed into the eval cache.
   beast-lang's serving path is wired through `agents/lang/__init__.py`
   (`safe_pack`, `safe_escalation`, `safe_reference` — they never raise and are
   silent under `OPENBEAST_EVAL`); nothing in this package needs those files,
   and `language_reference` is deliberately **not** in the runner's registry.

## Tests follow the house doctrine

Build the case (write the claim set into `tmp_path`, stub the model, stub
`drivers._run` rather than asking `which gcc`), make stubs record their calls,
assert the negative control, and never read the ambient machine — CI has an old
gcc, no zig, no GPU. The python driver is the one toolchain present everywhere.
