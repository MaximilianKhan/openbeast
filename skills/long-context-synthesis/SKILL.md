---
name: long-context-synthesis
description: Read, structure, and synthesize a large input (10K+ line PR, 30+ files, paper, transcript, log dump) without drowning. Activate when the input doesn't fit in working memory — when "just read it linearly" would consume your context budget without producing structured understanding. This is Kimi 2.6's natural strength encoded as a method.
allowed_tools: [bash, read_file, grep, list_files]
recommends_subagent: true
---

# Long-context synthesis

The local model has 512K tokens of context. Most of the time, that's overkill
— a single file fits in a fraction. But sometimes you face a real giant: a
10K-line PR, a 30-file codebase you're new to, a 200-page transcript, a
multi-hour log dump.

The wrong move is to read it linearly and let it fill your working memory
until you're at 80% context with no structured understanding. The right move
is **chunked synthesis**: pass over the whole thing structurally, then deeper
on the parts that matter, then synthesize.

This is what Kimi 2.6 does naturally with its multi-million-token windows.
You can do it with 512K if you're disciplined.

## The four-pass method

### Pass 1: Structural skim (cheap, broad)

Goal: understand the **shape** of the input. What's in it, in what order,
roughly how big each piece is.

For a codebase:
```bash
tree -L 2                                     # directory layout
find . -name "*.py" -o -name "*.go" | head -50  # file inventory
wc -l <files>                                 # size distribution
```

For a document / transcript:
- Read the table of contents (or build one: scan headings, list them out)
- Note section sizes (line counts)
- Identify the "spine" of the argument (intro → claims → evidence → conclusion?)

For a PR:
```bash
git diff --stat <base>..<head>     # which files changed and by how much
git log <base>..<head> --oneline   # commit by commit summary
```

Output of this pass: a **map** of the input. Not understanding yet; just
shape. Write it down.

### Pass 2: Identify the high-density regions

Not every part is equally important. The 10K-line PR might have 9000 lines
of mechanical changes (rename, move, formatter) and 1000 lines that actually
do something. Identify which is which.

Heuristics:
- **PR diffs:** test files often contain the spec; read them first to learn
  what the change is "supposed" to do. Lines added > lines deleted suggests
  new feature; balanced suggests refactor; deleted >> added suggests cleanup.
- **Codebases:** look for `main`, entry points, `__init__.py` of top-level
  packages — that's where the architecture is encoded.
- **Documents:** the abstract / executive summary is usually a faithful
  index. The conclusion often gives away the answer; the body justifies it.
- **Logs:** error patterns, transitions, state changes are dense; routine
  heartbeats are noise.

Output: an annotated map. "These 5 of 30 files contain the substance.
These 3 sections of the document are the spine. These 100 lines of the log
are the actual incident."

### Pass 3: Focused deep reads

NOW read the high-density regions carefully. For each:

- Read in full (not skimmed).
- Take notes: what's the claim, what's the evidence, what's the
  consequence. 1-2 sentences per region.
- Cross-reference: does what's here contradict or support what's elsewhere?
- Mark gaps: things you don't understand, terms you don't know,
  assumptions that aren't justified.

Output: per-region summaries (the "raw materials" for synthesis).

### Pass 4: Synthesize

Bring it together. The synthesis should answer:

1. **What is this, in one paragraph?**
2. **What's the core claim or change?**
3. **What's the evidence / mechanism?**
4. **What are the gaps, risks, or open questions?**
5. **What's the verdict / recommendation / summary the user actually needs?**

The synthesis is shorter than the regional summaries combined. That's the
point — you're collapsing the input into something portable.

## Specific techniques

### The TOC technique

For any large structured input, build a table of contents first:

```bash
# For markdown: extract headings
grep -nE "^#{1,3} " /path/to/doc.md

# For code: list top-level definitions
grep -nE "^(def |class |func |fn )" /path/to/file.py
```

Now you have a navigable index. Read selectively.

### The grep-driven read

When you have a specific question, don't read linearly — search:

```bash
grep -rn "session_expiry" .
grep -rn "TODO\|FIXME\|XXX" .   # known soft spots
grep -rn "deprecated" .          # what's being phased out
```

Each grep result is an entry point into the parts that matter for your
question. Read 50 lines around each match.

### The diff-of-diffs technique (for PRs)

For a 30-commit PR, instead of reading every commit:

```bash
git log --oneline <base>..<head>     # the commit list (read this first)
git diff <base>..<head> -- <key file>  # one file at a time
```

Often 80% of the substance is in 20% of the commits. Identify the key
commits from messages alone, read those carefully, skim the rest.

### The "twin pass" for codebases

For an unfamiliar codebase, do two complementary passes:
- **Top-down:** start from `main` / entry point; trace the call graph 2-3
  levels deep. This gives you architecture.
- **Bottom-up:** look at the leaf utilities (functions in `utils/`,
  `helpers/`, `lib/`). They reveal what primitives the project considers
  important.

The intersection of "where does main go?" and "what's reused everywhere?" is
the project's actual core.

## Anti-patterns

- **Linear read of a giant input.** You'll be at 80% context with no
  structured understanding. Always pass 1 first.
- **Trusting summaries you didn't make yourself.** If a doc has an executive
  summary, read it AS A HINT, then verify against the body. Summaries lie or
  go stale.
- **Falling for narrative arc.** A long PR description that reads well may
  hide critical complexity. Read the diff, not just the description.
- **Reading without taking notes.** If you read 5 files and can't remember
  what was in file 3, you didn't really read it. Notes externalize.
- **Refusing to skim.** The instinct to "be thorough" can be a trap. Pass 1
  is supposed to be fast and shallow. Don't deep-read until you've mapped.

## When to spawn a sub-agent

For genuinely large work, this is a textbook `start_skill_agent` use case:

```
start_skill_agent("long-context-synthesis",
  "Read the entire diff at /tmp/work/big.patch and produce a synthesis: \
   what does this PR do, what are the risks, what should the reviewer focus on?")
```

The sub-agent does the four-pass reading independently and returns a
distilled summary. Your main conversation stays responsive while the
sub-agent grinds.

You can also spawn multiple sub-agents in parallel for different regions of
the input — one per top-level subsystem in a large codebase, then
synthesize their outputs.

## Done criteria

You've successfully synthesized when:
- [ ] You can describe the input in one paragraph without referring back
- [ ] You know which 20% of the input contains the substance
- [ ] Your notes per region are crisp 1-2 sentence summaries
- [ ] The final synthesis answers the user's actual question
- [ ] You've flagged any open questions or gaps explicitly
- [ ] The synthesis is significantly shorter than the input (5-10x reduction
      typical; 20-50x for routine logs / boilerplate-heavy code)
