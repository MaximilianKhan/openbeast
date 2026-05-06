---
name: codebase-onboarding
description: Orient yourself in an unfamiliar codebase BEFORE editing. Activate at the start of any non-trivial task in a repo you don't already know cold — wrong file, wrong style, wrong layer is the most common local-model failure mode and this skill prevents it.
allowed_tools: [bash, read_file, list_files, grep]
recommends_subagent: false
---

# Codebase onboarding

The single most common failure mode in coding agents: **edit before understand**.
You change the wrong file, miss the existing helper, fight the project's style,
break an invariant nobody told you about. The cure is a small fixed cost: 5–15
minutes of orientation. Pay it once per repo, save hours.

## The orientation pass (in this order)

### 1. Read the README

Always. Even if it's bad, you learn what the project thinks it's about. Look
for:
- One-sentence description (what is this thing)
- Build / install / run instructions (how does it actually start)
- Architecture diagram or "how it works" section (mental model)
- Status (early-stage? frozen? actively maintained?)

If there are multiple READMEs (root + subdir), read the root first; subdirs are
component-level detail.

### 2. Map the structure

```bash
ls -la
tree -L 2  # or: find . -maxdepth 2 -type d | sort
```

Identify:
- **Entry points** — `main.py`, `cmd/*/main.go`, `bin/`, `scripts/`, `*.sh` at root
- **Source vs config** — `src/`, `lib/`, `internal/` are usually source; `.github/`, `docker-compose.yml`, `Makefile` are infra
- **Tests** — `tests/`, `test/`, `*_test.go`, `test_*.py` — the test layout tells you what the project considers worth testing
- **Docs** — separate from code, often the most useful file for onboarding

### 3. Find the entry points

How does the program actually start? Trace from one of:
- `main()` function in the language's convention
- `package.json` `scripts.start` / `bin`
- `pyproject.toml` `[project.scripts]`
- `Makefile` default target
- `start.sh` / `run.sh` / `serve.sh`

Read 50-100 lines from the entry point. This shows you the highest-level
architecture: what's wired to what.

### 4. Understand the build and test

```bash
# Look for the convention
ls Makefile pyproject.toml package.json go.mod Cargo.toml CMakeLists.txt 2>/dev/null

# Look at what scripts exist
cat Makefile 2>/dev/null | head -30
cat package.json 2>/dev/null | jq .scripts 2>/dev/null
cat pyproject.toml 2>/dev/null | grep -A 20 'scripts'

# Find existing test commands
grep -r "pytest\|npm test\|go test\|cargo test" Makefile package.json *.sh 2>/dev/null | head
```

Run the tests. If they pass, you have a green baseline. If they fail, that's
crucial information — don't change anything until you understand why.

### 5. Read recent activity

```bash
git log --oneline -20         # what's been happening
git log --since="1 month ago" --pretty=format:'%h %s' | head -30
```

Recent commits reveal:
- Active areas (where the team is investing)
- Recurring problem areas (commits with "fix" in the same module)
- Conventions for commit messages (mimic them)

If you see lots of recent activity in a file you're about to edit — coordinate
or wait. If you see none for years — be more skeptical the code is correct.

### 6. Identify conventions

Read 2-3 random source files in the area you'll touch. Note:
- Indentation (tabs vs spaces, 2 vs 4)
- Naming (snake_case, camelCase, PascalCase — separately for funcs, vars, types)
- Comment style (docstrings? inline? minimal?)
- Error handling pattern (exceptions vs Result vs error returns)
- Dependency injection style (constructor args, globals, DI container?)
- Logging style (logger.info vs print vs structured)

Match what you see. Don't impose your preferences on someone else's code.

## Specific moves by language

### Python

```bash
cat pyproject.toml setup.cfg requirements*.txt 2>/dev/null
ls src/ lib/ 2>/dev/null
find . -name "__init__.py" -not -path "*/node_modules/*" -not -path "*/.venv/*" | head
python3 -c "import ast; ast.parse(open('src/main.py').read())"  # smoke test imports
```

### Go

```bash
cat go.mod
find . -name "*.go" -path "*/cmd/*" | head    # entry points
go test ./... 2>&1 | head -30                  # smoke run
```

### JavaScript/TypeScript

```bash
cat package.json
ls src/ lib/ pages/ app/ 2>/dev/null   # framework hints (Next, etc.)
npm test 2>&1 | head -30
```

### C/C++

```bash
cat CMakeLists.txt Makefile 2>/dev/null
find . -name "*.c" -o -name "*.h" -o -name "*.cpp" -o -name "*.hpp" | head -20
```

## The orientation summary

Before you write a single line of code, write down (in your head or as a note):

1. **What is this project?** — one sentence
2. **What's the architecture in broad strokes?** — 2-3 sentences  
3. **What's the entry point for the part I'm changing?** — file:line
4. **What conventions am I matching?** — 2-3 specifics
5. **How do I run the tests?** — exact command
6. **Are tests green right now?** — yes/no
7. **What's my actual change?** — one sentence; if you can't, you're not ready

If any of these are blank, you're not done orienting. Don't edit yet.

## Anti-patterns

- **"Let me just look at the file and edit it."** No. Read up the call chain
  to understand context first.
- **"I'll add a helper to do X."** Check if the helper already exists. Use
  `grep` aggressively. Reinventing existing utilities is a tell that you
  skipped this skill.
- **"The README is too long, I'll skim."** Read it. Even bad READMEs are 5-10
  minutes; the wrong abstraction costs hours.
- **"I'll figure out the build later."** No — you'll need to verify your
  change works, which means you need to be able to run the project. Sort that
  out first.

## When this skill matters most

- First contact with any new repo
- Coming back to a project after >2 weeks away
- After a major refactor by someone else (the layout has changed)
- Before any change that touches >2 files
- Before adding a new module or package

## When you can skip it

- Trivial one-line typo fixes
- Documentation-only changes
- You wrote the code yourself last week

## Done criteria

You're oriented when:
- [ ] You can describe the project in one sentence without looking
- [ ] You know the entry point for what you're changing
- [ ] You ran the tests at least once and saw the result
- [ ] You can describe the convention you're matching (style, error handling, naming)
- [ ] You have grep'd for existing helpers in the area you'll modify
- [ ] You know whether the change you're about to make crosses module boundaries

Now you can edit.
