# Eval suite changelog

## v5.0, harness10: forced-act turn, done-when-verified, planning lines, algorithmic pack, web tools not advertised, reasoning text logged; arms U, U_8k, UP, UPA (2026-11-19) — era roll

**Era `036638ddd68698ce` → `9bcd5aa6030836d1`, harness9 → harness10
(fingerprint unchanged, `cd729e38ca1e3030`: run_eval's agent-shaping code did
not change; `agents/runner.py` and `agents/tools.py` did).** From the
investigation of DeepSeek-V4.1-Flash (Mia EXL3) on 168_linearizability_kv
(2026-11-19):

- It fails 168 mostly by choosing a global memoised search, which times out,
  instead of the per-key search the problem needs (linearizability is local;
  the passing files cite it). In a live probe it reasoned to "decompose by
  key?" and rejected it by argument.
- With thinking on it stalls. In T, 314 of 360 unit-minutes went into turns
  capped with only reasoning (16K cap), and 4 of 6 units never wrote a file.
  In T2 (8K cap + act nudge), 247 of 360.
- The text act nudge was ignored every time (up to 11 nudges): with thinking
  on, the model always thinks first and hits the cap again.
- Each cap discards ~90% of the reasoning (~65K chars generated, ~6K of tail
  handed back), so the design is re-decided every turn.
- Passing runs built brute-force checkers. T_s1 168_kv_a kept editing a
  passing file for ~33 minutes (no task_done).
- The eval system prompt advertised web_search/fetch, which are refused under
  eval; reasoning text was not logged (only character counts).

Every switch below is opt-in; off, the request and the prompt are as in
harness9. Each is an agent switch in the cache key (`run_eval.agent_switches`)
and pinned into each unit's env, except the pack (it moves `pack1-`).

- **Forced-act turn (`OPENBEAST_AGENT_FORCE_ACT=1`, key `fact<N>`).** After N
  (`OPENBEAST_AGENT_FORCE_ACT_AFTER`, default 2) consecutive turns cut off by
  the cap with no content and no tool call, the NEXT request is sent with
  `chat_template_kwargs.enable_thinking=false` (the thinking-off arms' switch;
  the seed and the cap are kept) plus a short note: begin with "Decisions so
  far" and "Rejected options" (at most ~12 lines), then make the tool call in
  the same reply. Thinking resumes on the following turn; the forced turn
  does not count toward the next streak; a tool call resets it. With the act
  nudge also on, the forced turn replaces the nudge on that turn. Events:
  `force_act` (streak, N) and `force_act_result` (tool calls, content chars,
  finish reason). **The pass-back:** the forced reply writes the summary
  itself, as content. Content stays in the history and reasoning does not, so
  the decisions ride along on every later turn instead of being re-derived.
  A separate summary request was rejected: it costs another whole turn on the
  wall, can itself be capped, and leaves the act for a third request. The
  raw ~6K tail stays as the forced turn's input, since with thinking off it is
  all the model has of those turns.
- **Done when verified (`OPENBEAST_AGENT_DONE_VERIFIED=1`, key `dverif`),
  eval and daily agents.** One system-prompt line: once your own checks pass
  (a brute force on small inputs agrees, and a timing run at the stated input
  limits fits), call task_done; don't keep polishing. It is integrated with
  G9 (`OPENBEAST_AGENT_VERIFY_DONE`), not a second check: with G9 on, the
  line adds that task_done asks once for a re-run of a file written after its
  last run, which is what G9 already does (`tools._verify_untested`).
- **Planning lines (`OPENBEAST_AGENT_ALGO_LINES=1`, key `alines`, arm UP).**
  Three general lines that name no task (`runner.ALGO_LINES`): bound first
  (the worst-case state count from the stated limits); implement a doubtful
  simplification and check it against a brute force on many small random
  inputs before rejecting it; a >5x miss on the time limit means redesign.
- **Algorithmic procedure pack (`OPENBEAST_ALGO_PACK=1`, arm UPA).**
  `agents/packs/algo-v1.md`, ~400 tokens (1,838 bytes), language-independent,
  procedure only, no noun from any v5 unit id (a test checks the ids; "state"
  is the brief's own "minimal state that determines the future"). **Frozen:
  sha256 `6c5324007750c44091a03c98cfef813cac0f3e576f982beb8908c0732e2ac09d`**,
  pinned in `agents/lang/pack_context.py` (`ALGO_PACK_SHA256`), in
  `tests/test_eval_arms_u.py`, and checked at run start (an edited file stops
  the run). An edited procedure is a new file with a new pin. run_eval's
  `packs_flag` appends it, last, to every unit's pack file (zig pack + host
  pack + algo; other languages host pack + algo, or algo alone), and its
  bytes enter `pack1-<sha8>` after the others, so with it off the component
  is unchanged (T2's `pack1-7886ae62` on the Mac host). It needs packs on
  (it rides the packs channel); without them the run stops.
- **Web tools not advertised under eval (`OPENBEAST_AGENT_HIDE_WEB=1`, key
  `noweb`).** Under eval with `OPENBEAST_EVAL_WEB` not `record`, `fetch` and
  `web_search` leave the tool list and their four prompt lines leave the
  system prompt ("If stuck, use web_search or fetch" becomes "If stuck, test
  a smaller case or re-read the error"). A call made anyway still gets the
  gate's refusal. A switch, not a default, so T2 replayed on harness10 sends
  T2's prompt; every U arm sets it.
- **Reasoning text in the transcript (observability, always on).** Every
  turn with reasoning logs a `reasoning` event: `chars`, `capped`,
  `truncated`, `text`, size-capped at `OPENBEAST_AGENT_REASONING_LOG_CHARS`
  (default 100,000 chars per turn, so a 16K-token capped turn fits whole;
  over it, the head quarter and the tail are kept; 0 = off). Nothing reads it
  back into the conversation. `evals/audit/integrity_scan.py` reads only
  `iteration` and `tool_call` events, so a reasoning that names the repo is
  not a hit; a test runs the scan over a transcript with reasoning events.
- **Daily agents:** `AGENT_FORCE_ACT`, `AGENT_DONE_VERIFIED`,
  `AGENT_ALGO_LINES` in openbeast.conf are exported like the other agent
  switches (`scripts/lib/conf.sh`); no profile sets them.

Campaign (`v5-thinking-first`, all optional, `--only`, jobs 4 / slots 4,
time_x 2.3, ~6-7 h each for DeepSeek):
- **U** = T2 with the act nudge replaced by the forced-act turn (N=2), plus
  done-when-verified and no web advertised, at T's 16384 cap (declared
  `--reasoning-budget 16384`). 16384 because the investigation found T2's
  8192 doubled the caps without ending the stalls, and a forced act ends a
  stall whatever the cap.
- **U_8k** = U at 8192: T2 still scored best overall (65 vs 61), so the cap
  question is answered alongside (and T3, at 4096 on harness9, measures its
  direction).
- **UP** = U + the planning lines. Also the GLM control: run with
  `--model-name GLM-5.3-Flash` when the pair serves GLM.
- **UPA** = UP + the algorithmic pack.

Board label for a run of this tree: **"harness10"**. A correct solution
passes the same way. U-arm rows pair only with harness10 rows.

## v5.0, harness9: act nudge, partial stats on a wall kill, eval fetch guard; arm T2 (2026-11-18) — era roll

**Era `06c82c4335dd6a0c` → `036638ddd68698ce`, harness8 → harness9
(`cd729e38ca1e3030`).** `agents/runner.py`, `agents/tools.py` and run_eval's
`run_agent` changed. From the DeepSeek-V4.1-Flash (Mia EXL3) arm T audit
(61/71, `--jobs 4`): all 10 failures hit the 3,600 s wall, 17 units hit it
(7 passed on the code left at the kill), 17.0 of 26.6 unit-hours went to
wall-killed units, and 192_pike_regex spent 8 turns and 3,585 s with zero
tool calls, every turn capped at 16,384 tokens of pure reasoning.

- **Act nudge (opt-in, `OPENBEAST_AGENT_ACT_NUDGE=1`, key `anudge`).** From
  the second turn in a row without a tool call that ends in the cap, and from
  the first that ends neither capped nor as a final answer (`stop` with no
  content, no `finish_reason`), the runner adds a short nudge: the next reply
  must be a tool call (write the file, run a test, or `task_done`). It is
  joined to the capped-think recovery's message, so the model gets one user
  turn, and it is logged as `act_nudge`. It never fires after a tool call, or
  on `stop` with content. With the switch off, every request is as before.
- **Per-turn cap keyed.** `OPENBEAST_AGENT_MAX_TOKENS` (already the runner's
  override of the eval turn cap) is now an agent switch: `cap<N>` in the key,
  `agent_turn_cap` on rows, pinned into each unit's env. Arm T2 sets 8192
  (and declares `--reasoning-budget 8192`). The capped-think recovery is
  unchanged. A test pins it for DeepSeek's shape (`reasoning_content` in
  `model_extra`, content `None`).
- **Partial stats on a wall kill (reporting only).** run_agent hands the
  runner `OPENBEAST_AGENT_STATS_FILE`. The runner rewrites its counters there
  at every turn start and after every response, and run_agent reads them only
  when it killed the agent. The row then carries `tokens_*`, `iterations`,
  `reasoning_turns` and `compactions` with `stats_partial: true` (16 of T's 17
  killed rows had tokens 0 and iterations None). No verdict rule reads them:
  `api_errors` is not taken from the sidecar, and `campaign.py score` never
  counts a killed row (`agent_exit_code` -1) as a turn-cap ending.
- **Eval fetch guard.** Under eval, whatever `OPENBEAST_EVAL_WEB` says, `fetch`
  refuses a URL that names this project's repository on any host
  (`MaximilianKhan/openbeast` on github.com, raw.githubusercontent.com,
  api.github.com/repos, codeload, a CDN's /gh/ mirror; case-insensitive,
  percent-decoded). It also refuses one whose path goes into a checkout's
  `evals/{tasks,refs,suites,results,cache,reference}`. Every redirect hop is
  checked too. `web_search` refuses a query that names the repo. The web
  tools stay off under eval by default. Arm T's two CPython fetches were
  refused by that gate. `evals/audit/integrity_scan.py` reported them as
  "read" because its refusal pattern missed the gate's wording. Fixed here.
  The scan adds a contaminating kind, `selfrepo`, with the guard's patterns
  (a test pins them equal).

Board label for a run of this tree: **"harness9"**. A correct solution passes
the same way (the nudge and the cap change how a run spends its wall, so T2's
rows pair only with harness9 rows; T's are harness8 + review 1118). Campaign:
`T_j2` (#226: T at `--jobs 2 --slots 2`, era-neutral) and `T2` (T + the 8192
cap + the act nudge, at T_j2's jobs) in `v5-thinking-first`, both optional.

## v5.0, review 1118: sandbox hardening (#224, era roll) and four validator lints (#223) (2026-11-18)

**#224, era `595634987879411a` → `06c82c4335dd6a0c`, harness stays harness8.**
`agents/tools.py` and `agents/runner.py` changed, so the era rolls. A correct
solution is judged exactly as before; what changed is what an agent can reach:
the macOS eval sandbox denies outbound network except loopback (the public
repo, and with it `evals/refs`, was clonable from the eval shell), the
push-diagnostics compilers and `grep` run under the same read-deny as `bash`,
eval-mode writes to the repo, the eval venv/prefix, Python's own install and
reused host packs are refused, and the integrity scan also flags remote
fetches and absolute include/embed paths in written files. The run side:
`--served-model` is checked at start and around every unit (a switched server
stops the run, nothing is banked), thinking is always sent explicitly, a failed
row counts as infra-lost only when API errors ended it, and the admission probe
needs a 200. Board label: "harness8 + sandbox hardening (review 1118)". The
first run under it is DeepSeek-V4.1-Flash (Mia EXL3), arm T, 61/71.

**#223, validators of 126_hmac_scratch, 153_coroutine_scheduler,
117_iter_refinement and 49_markov_steady_state.** Their ban/lint checks read
raw source (126, 153) or stripped comments but not multi-line docstrings
(117, 49), so a correct solution that named a banned module in a comment or
docstring could FAIL (the 122_c class). They now lint code only (tokenize /
AST); 153 also refuses `from asyncio import ...`. This changes what passes, so
every board run measured before it carries a validator note on those four
units, **not adjusted** (`evals/boards/v5/exclusions.json`). No board run failed any of the four after the board's merge (GLM-B's first
file lost them to a validator interpreter that did not start; its repair file
passed all four), so no published score would rise under the fix; 153's
stricter `from asyncio import` refusal could only lower one.

## v5.0, INFERENCE_THINKING: the host's thinking default (2026-11-17) — era roll (runner.py)

`agents/runner.py` changed, so the era (its content hash) rolls; no eval
request changes. spark-1 now serves GLM-5.3-Flash with thinking on by
default (`INFERENCE_THINKING=on`): beast-gate sets
`chat_template_kwargs.enable_thinking` on a chat request that names no
thinking control, and the runner's non-eval agents follow the key. Eval
runs stay exactly as they were:

- every eval-mode request carries `X-OpenBeast-Gate-Defaults: off`, and the
  gate forwards such a request as sent (the header is not forwarded);
- an eval run ignores `INFERENCE_THINKING` (env and conf): with
  `OPENBEAST_AGENT_THINKING` unset the body still names no thinking mode, so
  every host gives its own server default, the condition the L0/A/B
  baselines (TensorFold: off) and the llama.cpp boards (Qwen templates: on)
  were measured under. `OPENBEAST_AGENT_THINKING=on|off` is sent as before.

Why a header and not an explicit `enable_thinking=false` on every eval
request: that would have kept the GLM baselines but switched thinking OFF
for every llama.cpp model whose template thinks by default, a silent change
to every Qwen row. The new non-eval conf key `AGENT_MAX_TOKENS` is read only
outside eval mode; the eval turn caps (8K / 16K) are unchanged.

## v5.0, G24-G27 harness integrity fixes (2026-11-17) — era roll, harness8

Four defects found by a transcript audit of GLM-5.3-Flash's v5 runs
(`docs/HARNESS_NOTES.md`, "Language-specific residue under thinking" and
G24-G27). Every one is in the agent's environment, so **rows from before
this change do not replay into the new era**.

- **G24, the trailing-dot path bug (a bug in every arm since the R1 path
  guard, 2026-09-10).** `run_agent` extracted the task's `/tmp/eval*` paths
  with `/tmp/eval[\w\-./]*`, which keeps a sentence-final period: "Create
  /tmp/eval_gemm/gemm.c." handed the agent `/tmp/eval_gemm/gemm.c.` in
  `OPENBEAST_TASK_PATHS`. The done-check (`tools.task_done`) then refused to
  finish until a file literally named `gemm.c.` existed, the path guard
  compared writes against it, and G22's environment block printed it. 27 of
  320 units (25 of the 71 v5 units) named a dotted path: 122 b-e, 163 a-f
  (`/tmp/eval_divmod.`), 167 a-f, 168 a-f, 178, 190, 04, 175. Measured by
  `evals/audit/integrity_scan.py --trailing-dot` (turns after the first
  `task_done` refused for a dotted path; turns whose calls named one):

  | Arm | Units hit | Turns after the refusal | Turns touching a dotted path | Hit units that failed |
  |---|---|---|---|---|
  | L0 | 2 | 17 | 11 | none |
  | L1 / L2 (one window, not separable) | 1 | 8 | 4 | none |
  | A | 1 | 10 | 2 | none |
  | B | 7 | 113 | 56 | 122_c, 178, 190 |
  | C | 11 | 85 | 35 | none |
  | E (30-38 of 71, still running) | 10 | 29 | 25 | 122_c, 167_a |

  GLM copied its solution to `gemm.c.`, compiled `gemm.rs.` (`invalid
  character '.' in crate name`), and in C 122_b went looking for the
  checker (G25). The validator itself always read the real path, so no
  verdict was flipped directly; the cost is turns and wall, and the units
  that failed after a refusal spent up to 43 turns there (B 122_c).
  The fix: `run_eval.task_paths()` strips trailing periods (no real path
  here ends in one), for `OPENBEAST_TASK_PATHS` and for `fixture_roots`
  (the fixture lock of `/tmp/eval_divmod.`); `tools._task_expected_paths`
  strips them too. `task_paths` and `_FIXTURE_PATH` join
  `cache.HARNESS_FUNCTIONS` / `HARNESS_CONSTANTS`.
- **G25, the eval seal reaches past the answer key.** In arm C 122_b the
  agent's bash read Claude Code workflow journals under `~/.claude/projects`,
  `evals/run_eval.py` and `agents/tools.py`, and a cached validation of an
  earlier run in `evals/cache/` ("FAIL: mismatch at case 0"). Under
  `OPENBEAST_EVAL=1` the agent may no longer read: every checkout's whole
  `evals/` (tasks, refs, suites, results, cache, vlib, the harness), its
  `tests/` and `agents/logs/` (this checkout, every `git worktree list`
  checkout, and the git dir), the same trees in any other clone by pattern,
  `~/.claude`, and Claude Code's scratch dirs `/tmp/claude-<uid>`. The task's
  `/tmp/eval*` dirs, `$TMPDIR`, the toolchains on PATH (even one unpacked in
  a scratch dir) and the system stay readable. File tools refuse by path
  with a message that says what is sealed; on macOS the bash tool runs under
  a sandbox-exec profile that denies the reads in the kernel and appends
  "[eval sandbox: 'Operation not permitted' means a sealed path — ...]".
  Linux: the file tools and the command-text check refuse, the kernel does
  not (as before; see the residual gaps in README).
- **G25, the integrity scan of model transcripts.**
  `evals/audit/integrity_scan.py LABEL=results.json[,more]@TREE ...` scans
  every transcript of an arm for tool calls that read or listed those
  locations, per unit, with the row's verdict, and marks a unit
  CONTAMINATED when a read succeeded. It never edits a results file. Over
  every GLM-5.3-Flash v5 arm (L0, L1, L2, A, B, C, E so far; 2026-11-17):
  **3 contaminated units, all in arm C, all PASS**: 122_b (read
  `~/.claude/projects/.../workflows/*/journal.jsonl`, `evals/cache/*122_gemm_blocked_b*`,
  listed `evals/`, read `evals/run_eval.py` and `agents/tools.py`), 122_d
  (`ls /private/tmp/claude-501/`, then a `grep -r` over `~/.claude` that
  timed out with partial output) and 122_e (a `grep -r` over `~/.claude`
  that timed out with no output, then `ls -d ~/.claude`). Their C verdicts
  are reported as contaminated; nothing else in any arm read a sealed path
  (the zig stdlib reads of the 11-16 runs were the toolchain's own tree,
  which then lived in a scratch dir).
- **G26, 122's source lint read comments.** The forbidden-API check
  rejected `// no BLAS, no dlopen` (arm E 122_c: a correct C++ solution
  scored FAIL; its final file, replayed, now passes). All five variants
  shared the lint. It now ignores comments, matches the loader/BLAS words
  with string and char contents blanked, and keeps strings for the
  language rules that need them (`import "syscall"`, `extern "C"`,
  `asm(".incbin")`); Go's `//go:` and `#cgo` directives stay visible. The
  five references now open with such a comment, and all five units were
  re-proven with `tests/prove_task.py` (PROVEN). Task hash of the five
  122 units moves.
- **G27, the agent's `python3` is the validators'.** The tool shell
  inherited the operator's PATH, where `python3` was anaconda 3.9.13 while
  validators run `OPENBEAST_EVAL_PY` (3.12). Under eval the bash tool now
  puts a private dir first on PATH whose `python`, `python3` and
  `python3.X` exec `OPENBEAST_EVAL_PY` (or `$OPENBEAST_EVAL_PREFIX/venv`),
  and nothing else from the venv (no `pip`); on macOS the venv and the shim
  are write-denied to the agent. G22's environment block probes through
  the same PATH. Outside eval nothing changes.
- **Era:** `HARNESS_VERSION` harness7 -> harness8 (`run_agent` calls
  `task_paths`); `agents/tools.py` and `agents/runner.py` changed, so the
  context hash moves for every row; the 122 task hash moves. `vlib.py`,
  `SUITE_VERSION` and `system-prompt.md` are unchanged.

## v5.0, a capped think keeps its tail + zig pack v3 (2026-11-17): era roll

Evidence: arm C's 71 transcripts (`docs/HARNESS_NOTES.md` "Thinking stall"
and "Language residue under thinking").

- **The era moves, because `agents/runner.py` changed.** At this change the
  era goes from `f1e0565c5a9cb086` to `575afd5d75e15c94` (`cache.current_era()`). Other
  changes to the era files that merge alongside this one move it again;
  read the stamp in the results header. `HARNESS_VERSION` (harness7) did not
  move, because nothing in `cache.HARNESS_FUNCTIONS` changed.
- **Runner: a thinking turn cut off by the cap is no longer thrown away.**
  This applies when a capped turn (`finish_reason == "length"` with our
  `max_tokens`) has no content and no tool call but does have reasoning:
  the nudge then carries the last 6,000 chars of that reasoning and says
  "you were cut off mid-reasoning; conclude and act now". In C all 78
  capped turns were exactly that. After a discarded cap on a code-writing
  turn, re-thinking from scratch capped again or hit the wall 33 times in
  47. Every other turn is unchanged: reasoning is never fed back, a capped
  answer with content keeps its 2,000-char head, and the old nudge stays.
  The `output_cap` event gains `completion_tokens`, `reasoning_chars` and
  `reasoning_tail_chars`.
- **The cap did not move** (16,384 with thinking, 8,192 without), and the
  client timeout still assumes 8 tok/s. The capped turns measured 15.1–25.8
  tok/s under 4 jobs. A higher cap was rejected: the length a capped think
  needed is censored, and each extra 8K costs ~7 min per stalled turn.
- **Zig pack v3** (`agents/packs/zig-0.16-v3.md` @`cfdab8fa`,
  `OPENBEAST_PACK_VARIANT=v3`): v2 plus `posix_write` (P0), `clz_shift`,
  `erf` and `init_skeleton`, with OLD/NEW fixtures verified on zig 0.16.0.
  It gets its own `pack1-<sha8>`. v1 (production, `MEASURED_SHA256`) and v2
  are byte-identical to before. `claims/zig-0.16.json` and the zig
  escalation index are regenerated (24 claims, 57 OLD fixtures).
- **Host pack:** a new probed fact, `sed_no_word_boundary` (BSD sed treats
  `\b` as a literal `b`). On the Mac harness host, packs-on runs therefore
  get a different host-pack sha (`9795705f` → `c2b4d955`), so every
  packs-on row there is in a new `pack1-` component. Packs-off runs do not
  change.

## v5.0, G22 environment block + G23 wall warnings (2026-11-16) — era roll, harness7

Two opt-in agent switches for the overnight arm E (both on). Evidence and
prior art: `docs/HARNESS_NOTES.md` G22/G23.

- **The harness fingerprint moves: harness6 -> harness7.** run_eval's
  `run_agent` (in `cache.HARNESS_FUNCTIONS`) now sets
  `OPENBEAST_AGENT_WALL_S` in the agent's env to the timeout it kills at
  (`int(wall * timeout_scale)`, computed once and used for both). Through
  env, not argv: the runner's argv is unchanged, and the wall the agent is
  told cannot drift from the kill timeout. The variable is set on every
  unit, but the runner reads it only with the switch on. The era moves for
  every row regardless, since `agents/runner.py` changed.
- **G22, `OPENBEAST_AGENT_ENV_BLOCK=1`.** Before the first model call the
  task message gets a probed environment block (<= 1,200 chars): OS/arch,
  python3/go/rustc/zig/cc/clang/gcc versions, which of git, make, cmake,
  timeout, gtimeout, perl, jq exist, the workdir, which expected task paths
  exist, and a depth <= 2, <= 30-entry listing of the task's `/tmp/eval_*`
  dirs. It is stored in the history (a fixed fact) and logged as an
  `env_block` transcript event. Never an env value or a path outside
  `/tmp/eval*`.
- **G23, `OPENBEAST_AGENT_WALL_WARN=1`.** The first turn starting past 50%
  and the first past 80% of the wall get "[wall clock — N s of your M s
  wall used; finish and verify the destination now]" in the transient block
  next to the turns notice (never stored); a `wall_notice` event is logged.
  M is the scaled wall, so it depends on `--jobs` as the kill does.
  `runner.py --wall-s` sets the wall outside run_eval.
- **Cache key.** Both join the `ag-<parts>` component (`envb`, `wwarn`;
  arm E is `ag-envb-wwarn`), omitted when unset. Stamped in
  `harness.agent_switches` and, when set, on every row (`agent_env_block`,
  `agent_wall_warn`); pinned into each unit's env like the others.
- Off (unset): requests are byte-identical to before.

## v5.0, zig pack variants: v1 back in production, v2 by switch (2026-11-16)

The fix for #188. That change put the v2 bytes in `agents/packs/zig-0.16.md`.
`MEASURED_SHA256` still named v1, so production served no zig pack at all.
The measured v1 bytes are restored there (@`5cdf4b64`; production serves
them again). v2 now lives at `agents/packs/zig-0.16-v2.md` (@`ed18f83e`).
An eval run picks one with `OPENBEAST_PACK_VARIANT=v1|v2`: the default is
v1, and an unknown name aborts. The choice is recorded as
`harness.pack_variant`, and the file's bytes are in `pack1-<sha8>`.

- **Era effect:** default packs-on runs measure v1 + the host pack again.
  That pack1 component differs from #188's v2 + host pack, and from the
  pre-#188 v1-only component.
- **To measure v2 + the host pack:** `BEAST_PACKS=1 OPENBEAST_PACK_VARIANT=v2`.
- `HARNESS_VERSION` and the base era are unchanged.

## v5.0, the harness host provisions its own toolchains (2026-11-16) — no era move

Same suite, manifest and `HARNESS_VERSION` (harness6). The harness
fingerprint did not move: nothing in `cache.HARNESS_FUNCTIONS` changed (the
new checks live in `run_eval()`'s start and its per-unit infra block, and in
`evals/eval_host.py`, none of which shapes what an agent sees). No era
component moved either: `system-prompt.md`, `agents/runner.py`,
`agents/tools.py`, `SUITE_VERSION` and `vlib.py` are untouched. The pins are
the versions the units were proven with, so a host that already had them
(the Mac's Homebrew go1.23.3) keeps its `env1-` component.

- **`evals/toolchains.lock` + `scripts/eval-host.sh check|setup|env|fetch`.**
  zig 0.16.0, rustc 1.100.0 (rustup 1.29.3 online, the standalone installer
  offline), go 1.23.3, and a python 3.12.16 eval venv from
  `agents/requirements.lock`, per platform (darwin-arm64, linux-aarch64,
  linux-x86_64), sha256-pinned (zig, rust and cpython reuse the ark
  catalog's pins; a test holds them equal). `setup` installs into
  `~/.local/opt/openbeast-eval`, never as root; offline from an ark drive or
  a `fetch` mirror with `--from`.
- **Live runs provision.** `run_eval.py`, `profile.py` and
  `benchmark_all.py` run the check (then setup) before the validator
  toolchain is pinned, put the prefix first on PATH and re-exec under the
  eval venv. `--no-provision` / `OPENBEAST_EVAL_NO_PROVISION=1` only report;
  `--cache-only` is untouched. `profile.py` preflight refuses a host the
  check still fails.
- **A broken interpreter never scores.** A live `run_eval` refuses to start
  when its python cannot import the stdlib and the runner's packages. An
  agent that dies at interpreter startup is a new infra reason,
  **`interpreter_error`**: not cached, not scored (`scoring.INFRA_REASONS`,
  `ablation.INVALID_REASONS`, an incomplete L0 for `profile --resume`), and
  a forced re-check of the interpreter aborts the run when it is broken.
  The 2026-11-16 incident: 22 units scored as FAILs (`docs/HARNESS_NOTES.md`).

## v5.0, zig pack v2 + host pack + Rust edition fix (2026-11-16) — base era unchanged

Evidence: a per-language failure taxonomy of GLM-5.3-Flash's v5 runs
(L0, L12, arms A and B; 153 failed units). Only zig fails for lack of
knowledge. Host friction (macOS/BSD userland and libc) cost about 95 turns.
Notes G19-G21 are in `docs/HARNESS_NOTES.md`; the design is in
`docs/LANG_AWARENESS_PLAN.md` §5.1.

**Era effects.** `HARNESS_VERSION` stays harness6: none of
`cache.HARNESS_FUNCTIONS` changed, and `packs_flag`/`ArmCtx` are not among
them. The base era does not move either, because `agents/runner.py`,
`agents/tools.py` and `system-prompt.md` are untouched. Packs-OFF rows are
therefore unaffected, except the five Rust units below. What moves:

- **The packs component** (`pack1-<sha8>`, packs-on arms only). The zig
  pack's bytes changed, and the component now also hashes the host pack.
  With a non-empty host pack, EVERY unit of a packs-on arm carries it, not
  only zig units. Old packs-on rows do not replay.
- **The escalation component** (`esc1-…`, escalate arms only).
  `agents/lang/escalate-index.json` and the zig claim set were regenerated
  for the 14 new OLD fixtures.
- **Five Rust units' task hashes** (36_black_scholes_e, 122_gemm_blocked_d,
  123_nbody_e, 158_karatsuba_bytes_e, 159_ntt_convolution_e). Their task
  text changed, so their old rows do not replay in any arm.

Changes:

- **Zig pack v2** (`agents/packs/zig-0.16.md` @`ed18f83e` in #188; moved to
  `zig-0.16-v2.md` by the entry above, 1,959/2,000
  tokens). Five new curated bullets: `Thread.spawn(config, f, args_tuple)`,
  `ResetEvent`/`Mutex` → `std.Io.Event`/`std.Io.Mutex`, Io file-method
  arity (`writeStreamingAll`, `openFile`, `stdin()`, `reader(io, &buf)`),
  `@import("std")` and `while (c) : (i += 1)`, and the lint errors. Each has
  OLD/NEW fixtures in `tests/fixtures/zig016/`, and OLD fixtures now pin
  the expected error. The generated digest went from 35 lines to 18.
  `MEASURED_SHA256` is unchanged: production serves no zig pack until an
  eval arm measures v2.
- **Host pack** (`agents/lang/host_pack.py`). It is probed from the tool
  host when `packs_flag` runs, and every packs-on unit gets it (zig units
  get it after the zig pack, in one `--context-file`). Results gain
  `harness.packs.host` and `harness.host_pack`. A GNU/glibc host probes no
  gaps and gets nothing.
- **`pack_context.pack_problem`** requires the generated-section header for
  zig only. A non-zig pack in `PACK_FILES` no longer aborts `--packs`.
- **Rust `--edition 2021`.** Five v5 Rust variants said "Idiomatic Rust
  2021+", but their validators compiled with bare `rustc -O`, which is
  edition 2015 (`try_into` without an import failed in three runs, and push
  diagnostics already compile with `--edition 2021`). Task text and
  validator now both say `rustc -O --edition 2021`, as 167_e and 168_e
  already did. All five re-proven with `tests/prove_task.py`.
  163_bigint_divmod_e also compiles with bare `rustc`, but its text claims no
  edition, so there is no contradiction and it is unchanged.

## v5.0, harness batch G9/G13/G15/G16 + agent-switch cache key (2026-11-16) — era roll

Same suite, manifest and `HARNESS_VERSION` (harness6): the harness
fingerprint (`cache.harness_fingerprint()`, run_eval's agent-shaping code) did
not move, because none of `cache.HARNESS_FUNCTIONS` changed. The **era does
move**: `agents/tools.py` and `agents/runner.py` are hashed into it and both
changed. G15 is a default-on change to what an edit_file result says, so no
pre-batch row replays into the new era. Re-measure any arm you compare
against rows from before this change. Evidence for each item is in
`docs/HARNESS_NOTES.md`.

- **Cache-key safety (fixes a trap).** `OPENBEAST_AGENT_THINKING`,
  `OPENBEAST_AGENT_SEED`, `OPENBEAST_AGENT_VERIFY_DONE` and
  `OPENBEAST_DIAG_EDIT_HINT` change what the agent does, but the cache key
  could not see them. An arm run without `--no-cache` replayed the default
  arm's rows and banked its own rows under the default arm's key. They are
  now one key component, `ag-<parts>` (e.g. `ag-think-on-seed2`, built by
  `run_eval.agent_switches()` with the runner's own parse rules). It is
  OMITTED when every switch is unset, so legacy keys stay reachable (the
  diag/rb convention). The values are stamped in the results header
  (`harness.agent_switches`, `harness.agent_component`) and on every row
  (`agent_thinking`, `agent_seed`, plus `agent_verify_done` and
  `diag_edit_hint` when set). They are also pinned into each unit's env, so
  the agent gets exactly what was keyed. The `--no-cache` advice in the
  runner-v2 entry below no longer applies.
- **`run_eval.py --seed N` / `--thinking on|off`** set
  `OPENBEAST_AGENT_SEED` / `OPENBEAST_AGENT_THINKING`, so both are in the key.
  A k-seed repeat (G12) is `--seed 1`, `--seed 2`, `--seed 3` runs.
- **G9, "test before done" (opt-in, `OPENBEAST_AGENT_VERIFY_DONE=1`).** This
  builds on the R1 missing-file refusal. When the expected files exist, the
  agent's first `task_done` is answered "Before finishing: re-run your test
  against <dest> itself" if a destination source file it wrote has not been
  named by a bash command since the last write_file/edit_file to any file.
  It fires once per run and never blocks: the second `task_done` is
  accepted. Off: the tool layer behaves exactly as before.
- **G13, "edit, don't rewrite" (opt-in, `OPENBEAST_DIAG_EDIT_HINT=1`).** A
  failing push-diagnostics block ends with "Fix the first error with
  edit_file at line N; don't rewrite the file", where N is the first error's
  line. It only applies while diagnostics are on.
- **G15, edit_file lint noise (default on, a fix).** With push diagnostics
  on, an edit_file result shows only shellcheck findings that are new
  relative to the file before the edit (keyed on source line, code and
  message, so they survive line shifts). When nothing is new it reads
  "diagnostics: no new findings (shell; N pre-existing not repeated)".
  Compiler errors are never filtered: a persisting build error is not noise.
  write_file output is unchanged.
- **G16, telemetry.** An escalation arm's header names the languages whose
  escalation index is served on the host (`harness.escalation_eligible_langs`)
  and the number of card-eligible units. The run prints "card-eligible units:
  n/N", and `evals/ablation.py` adds it to the exposure line, counted over
  measured rows.

## v5.0, wall-scaled turns (2026-11-16) — opt-in budget, era unchanged

`run_eval.py --turns-per-wall-s N` gives each unit max(its max_iter,
wall_s / N) turns, ignoring the manifest's `max_iter_ceiling`, so the unit's
own wall budget (600 / 900 / 1500 s by tier) is the binding limit and the
turn cap only stops a loop. N = 15 gives medium 40, hard 60, xl 100 (from
15-30). Why: the GLM-5.3-Flash v5 L0 audit found most failing units ended on
max_iterations after 150-400 s of a 1,200-1,800 s wall. The effective budget
is in the cache key (`.mi<N>`) and on every result row (`max_iter`), and the
results header records `turns_per_wall_s`. Default runs are unchanged.

## v5.0, runner v2 (2026-11-16) — agent-harness frictions (era roll)

Same suite, manifest and `HARNESS_VERSION`. `agents/runner.py` and
`agents/tools.py` are hashed into the era, so this change moves it: no row
measured before runner v2 replays into it. Re-measure the v5 baseline after
merging; a pre-v2 row and a v2 row are not one experiment.

Why: an audit of GLM-5.3-Flash's 45 failed v5 units (2026-11-16) found 43
genuine model failures, and harness frictions that cost the model turns in
many of them:

- **Platform line.** The runner's system prompt now names the tool host's
  OS and the bash tool's shell (on macOS: BSD userland, `sed -i ''`, no GNU
  `timeout`). GNU habits failed on the macOS eval host because nothing said
  it was BSD.
- **bash, not sh.** The bash tool runs `/bin/bash -c` when /bin/bash exists
  (else `/bin/sh -c`, as before), and drops an inherited `BASH_ENV`. Process
  substitution `<(...)` failed under sh. This also ships to Mac/Linux clients.
- **Turn budget.** The prompt states "you have N tool turns", and a short
  notice rides with the plan block in the last 3 turns. Most failing units
  ended on max_iterations at 150-400 s of a 1200-1800 s wall budget, still
  exploring.
- **Unchanged update_plan.** Re-sending the current plan gets a nudge (it
  still costs its turn). One unit spent 60% of its turns re-sending one plan.
  The plan block and the instructions no longer say "keep it current".
- **Repeat guard.** The 3rd consecutive identical tool call with an identical
  result gets "You have seen this exact output N times; act on it". The
  audit saw the same `cat` 15x and the same bash command 19x.
- **Timeouts.** A bash timeout always says it timed out and the limit. On
  macOS, killpg on a group of zombies raised EPERM, so a killed hung
  pipeline read as "[Errno 1] Operation not permitted".
- **Eval turn cap.** Eval requests now carry `max_tokens` (they sent none):
  8192 with thinking off/unset, 16384 with `OPENBEAST_AGENT_THINKING=on`,
  the same for every model; `OPENBEAST_AGENT_MAX_TOKENS` still overrides
  (0 = uncapped). The client's read timeout is sized to the cap at 8 tok/s
  (floor 600 s), and eval runs never retry a timed-out turn: a first sizing
  (16K at 20 tok/s) let the client time out first under 4-way load, where a
  long-context turn decoded at ~12 tok/s. On 168 (c, f) the model's first code-writing turn ran past
  the client's 600 s timeout in 5 of 5 attempts, was retried identically, and
  the unit spent its 1,800 s wall without writing a file; a capped turn ends
  as "length" and the existing truncation recovery returns its head with a
  nudge.
- **Seed switch (opt-in).** `OPENBEAST_AGENT_SEED=<int>` sends `seed` on
  every request. The Spark TensorFold server uses a FIXED seed when none is
  sent (probed 2026-11-16: identical text for three temperature-0.6 requests;
  seeds 1/2/3 differ; seed 1 twice is identical), so each Spark run is one
  reproducible draw and tiny prompt differences flip units (G12). Repeat an
  arm under k seeds to separate an effect from that chaos. Like the thinking
  toggle, **the cache key does not see it**: run with `--no-cache` and a
  distinct `--model-name`.
- **Thinking toggle (opt-in, off by default).** `OPENBEAST_AGENT_THINKING=on|off`
  sends `chat_template_kwargs.enable_thinking` true/false on every request;
  unset sends nothing (requests unchanged). It is the switch for a "thinking
  on" ablation arm (`OPENBEAST_AGENT_THINKING=on python evals/run_eval.py
  ...`; run_eval passes the env through). **The cache key does not see this
  variable**, so run such an arm with `--no-cache`: otherwise it replays
  thinking-off rows and banks thinking-on rows under the baseline's key.
  `reasoning_content` is never fed back into the history.

## v5.0, harness6 (2026-11-16) — review-1116 fixes (era roll)

Same suite and manifest (71 units); the harness and `vlib.py` changed, so the cache
era moved (`HARNESS_VERSION` harness5 -> harness6, `vlib.py` rehashed). No
harness5 row replays into harness6: rerun the v5 units measured before it.
One roll covers every review-1116 eval fix below. What changed in how
units are judged:

Harness and vlib:

- A macOS solution can no longer signal processes outside its sandbox
  (it could SIGKILL sibling units, validators, the harness).
- The macOS deny-list sandbox fails closed (verdict `sandbox`) instead of
  running the solution unsandboxed when `sandbox-exec` fails.
- The Linux Landlock confine refuses network sockets with seccomp (UDP,
  and TCP below kernel 6.7, were open).
- `run_solution` keeps the solution's rusage on a normal exit (fixed-CPU
  gates passed over-budget solutions ~1 run in 12); on macOS the solution
  monitor and the leftover sweep read libproc in-process instead of
  forking `ps -A` every 50 ms.
- The validator completion line is no longer a module global an
  in-process solution can read; in-process units remain forgeable by
  frame introspection (documented).
- The leftover sweep kills only provably-owned process groups (reused
  pids no longer kill sibling units), kills before the timeout drain on
  macOS, and a validator's pgids file cannot name an older process.
- One harness per `/tmp/eval_*` root (`run_eval.fixture_lock`): every
  unit holds an flock per fixture root across harness processes; a sweep
  waits (and says so at once), a prover refuses at once with the holder's
  pid.
- The validator's `TMPDIR` is private (`<private>/tmp`).
- `--jobs` is clamped to beast-gate's per-device in-flight cap; the
  remote health check retries; probes never send the ambient key to an
  untrusted `--base-url`; the stratified L2 records the true pi.

Families 163-179:

- 163/167/168: the compiler no longer reads the sealed reference
  (`vlib.build_private`: include lint, private build dir, macOS read-deny);
  a hanging compiled solution fails on its judged warm-up instead of as an
  infra `validator_timeout`; 163's prompt states its 6 s CPU budget.
- 173: the reference absorbs its own undelivered `cancel()` on CPython
  <= 3.12; 24 cancel-tail scenarios pin it (624 scenarios in all).
- 175: the oracle is incremental (~0.2 s instead of ~1.8 s per validation;
  same expected results); the argvcheat arm proves on Linux.
- 176: `shopd/` is copied without following links, under a cap.
- 179: the validator never runs git in the agent's repository (a promisor
  remote's `uploadpack` ran unsandboxed); the fixture history is one
  `git fast-import` (same SHAs, ~0.5 s instead of ~4 s on a Mac).

Families 183-196, the U3 upgrades and the kept-v4 units:

- **Toolchain pins.** The harness resolves and fingerprints every compiler
  and inspection tool before any agent runs and refuses a validation when
  one moved or changed (not banked). U3 compiled units build a private copy
  with the pinned compiler (`vlib.start_build`); every validator gets
  `GOENV=off`, `GOTOOLCHAIN=local` and the pinned rustup toolchain. New
  prover arms: `toolchain:path`, `toolchain:goenv`.
- **Confinement.** 183, 188, 40, 36_a and 123_a run the solution with
  `confine=` (stdlib + a private dir) on macOS and Linux; 40 lints strictly
  and blocks numpy/torch with an audit hook.
- **Kept-v4 gates** of 146, 147, 153, 158 and 159 scale with
  `OPENBEAST_TIMEOUT_SCALE` (the v4 number at scale 1); the prover lint now
  sees a measured time compared with a bare number.
- **122's oracle** builds its columns in the integer domain (bit-identical,
  2x faster on aarch64).

## v5.0 (2026-11-15) — the measured suite (CURRENT; era break)

`evals/SUITE_VERSION` = v5 rolls every cache era; the v4 board is frozen
and nothing is compared across versions. The suite is the manifest
`evals/suites/v5.json`: **71 scored units over 34 base tasks**, all 12
categories and all six languages, no probe units. It is the designed v5
(111 units over 39 bases + 7 probes) minus what failed review: 14 new
families and the U3 upgrades (36, 40, 122, 123) passed three adversarial
rounds and ship; 12 items are the v5.1 backlog (`evals/README.md`), and
24 units of v4 tasks, kept exactly as defined in v4 and proven, keep
their categories covered. A full three-arm profile is 3.73 reference
hours, inside the 4-hour budget even sequentially. Harness: `harness5`.

- Hidden seeded tests inside the validator for new and upgraded units; no
  shipped unit has an expected file or `check.py` in the agent's dir; a
  mismatch prints only the case index.
- Zig API tutoring banned from prompts (L0 is unassisted).
- Oracle seal: `evals/tasks|refs|suites` unreadable under `OPENBEAST_EVAL=1`;
  `fetch`/`web_search` off under eval (or recorded).
- Every shipped unit proven without a model (`tests/prove_task.py`; family
  formats delegate to the family's own prover).
- Validator isolation: stdin-fed script, private cwd and env, solution
  confinement (network, process-info, exec, read-deny / allowlist), a
  **private build cache per validation** (zig, Go, cargo; ccache off),
  deleted afterwards, and a **completion line** (a python validator passes
  only when it ran to its end).
- **Every process the agent started is killed before validation**, detached
  daemons included (env tag, macOS sandbox tag, tree polling with the runner
  as a Linux subreaper, groups and sessions, a cgroup where delegated); a
  unit with survivors is not validated.
- Arms L0/L1/L2 in one process (`run_eval.py --arms`), diagnostics keyed per
  unit, per-unit wall caps, longest-first scheduling, `--slots`.
- `evals/profile.py --budget-h 4` (calibrate k on L0, fixed L2 cut order,
  `budget_skip` never scored) and `evals/ablation.py` (McNemar, MDE /
  within_churn, strata, Horvitz-Thompson for stratified L2, `--pool`).
- Scoring: LANG over multi-language bases only; imputation retired for v5
  and every arm; a full v5 row = the manifest's scored units (read from it).
- The v4 fast pin is renamed `v4-fast` (was `v5-fast`) and stays v4-only.

## v5-fast overlay (2026-09-08) — pinned fast subset, imputation-scored

NOT a new suite version — v4 stays current and frozen; v5-fast is a pinned
SELECTION of it (`evals/suites/v5-fast.json`): the 86 units that
discriminate between reference models + 20 all-pass tripwires. Runs via
`--suite v5-fast`, scores by imputing the 185 saturated units, which
reproduces full-291 capability exactly for in-family models (identity
verified per reference run by `make_fast_suite.py`; raw-subset ranking
measured at τ = +0.810 and rejected). Fast runs never enter the
leaderboard and share cache keys with full runs. ~0.85 h with `--jobs 4`
vs ~5.6 h full.

## v4 (2026-07-08) — the hardened suite (CURRENT)

137 base tasks / 291 effective units across all 12 topic categories.

A full adversarial re-review of v3.5 (see `docs/archive/EVAL_REVIEW_2026-07-07.md`)
found 117 of 159 tasks flagged — inflators (empty submissions / naive
algorithms / dead perf gates passing), deflators (correct code failing on
broken fixtures or comment-tripping lints), and unverifiable properties.
v4 is the rebuild (plan: `docs/archive/EVAL_V4_PLAN.md`):

- **Pruned 25** overlapping / unverifiable tasks (159 → 134).
- **Added 3 DSP tasks** (160 FIR, 161 Goertzel, 162 biquad IIR) so Signal
  Processing is a real category (1 → 4 tasks). Net: 137 tasks.
- **Every surviving flagged task hardened + verified BOTH ways**
  (reference passes; the documented cheat is empirically rejected) under
  9 systemic rules: pre_validate on all fixture tasks, comment/string-
  proof anchored lints, no readable answers, perf/memory gates measured
  to ≥10x, printf fixtures, self-contained variant texts, promise↔assert
  parity, durable 6-language refs, ≥5x timing margins.

v4 numbers are NOT comparable to v3.5. The v3.5 leaderboard is retained as
the "before" for the launch narrative and will be archived on a later
formal-display pass.

## v3.5 (2026-05-07) — legacy

159 base tasks / 323 units. Full report and leaderboard in
`docs/RESULTS.md`. Retained for the evolution story; see the review above
for why it was rebuilt.
