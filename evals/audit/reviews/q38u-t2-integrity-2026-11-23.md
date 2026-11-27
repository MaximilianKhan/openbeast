# Q38U-T2 integrity review notes (2026-11-23, sealed)

The run: Qwen3.8 27B Uncensored MTP Q5_K_M, campaign `v5-thinking-first`
arm `Q_T2` (T2's switches at `--jobs 1 --slots 1`), RTX 5090, llama.cpp,
harness9 tree `f38a29c7` (era 036638dd), driven from the rig's eval-host
container (`openbeast-eval-host:8d1e28442569`, Linux). Checkout on the
rig: `~/ob-q38/.claude/worktrees/h9`. Board row `Q38U-T2`. These notes are
kept out of the public docs (board.json and the reports are readable by a
measured agent).

| Label | File (`evals/boards/v5/`) | Header time (UTC, naive) | End (campaign.log, UTC) | Rows |
|---|---|---|---|---|
| `Q_T2` | `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json` | 2026-11-23T11:57:50 | 2026-11-23T23:11:11 | 71 |
| `R1` | `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-231112.json` | 2026-11-23T23:11:12 | 2026-11-24T01:26:32 | 7 |

## The scan (run 2026-11-23 ~17:30 PST, on the rig)

From a scratch export of origin/main at `d87211f1`
(`~/ob-scan-d87211f1`; `git archive`), against the rig's original result
files (original mtimes) and the arm tree's transcripts:

```bash
H=~/ob-q38/.claude/worktrees/h9; R=$H/evals/results
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir $H/agents/logs \
    --until Q_T2=2026-11-23T23:11:11 --until R1=2026-11-24T01:26:32 --json /tmp/qt2-scan.json \
    "Q_T2=$R/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json@$H" \
    "R1=$R/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-231112.json@$H"
```

`--tz UTC`: the container writes naive UTC timestamps. `--until`: the END
lines of the campaign log. From the committed copies the same command
works with `$R` replaced by `evals/boards/v5` (the `--until` values carry
the ends, so the reset mtimes do not matter).

Result:

- `Q_T2`: 71 transcripts, 71 units, 0 unattributed, **2 contaminated**.
- `R1`: 10 transcripts, 7 scored units, 0 unattributed, **1 contaminated**.
  The 3 extra transcripts (`37_softmax_stable`, `40_attention`,
  `49_markov_steady_state`, 23:09-23:10 UTC) are the main pass's last
  units, inside the window's start slack; they are unscored in `R1`,
  attributed to `Q_T2` too, and had no hits.

## The three contaminated units

As with Q38U-L0: on Linux the agent's shell has no kernel network deny
(agents/tools.py `_eval_read_deny_prefix` is macOS only; evals/README.md
residual gaps). In each case the agent's `fetch` or `web_search` was
refused first, then the agent used `curl` from `bash`, and the reply
arrived.

- **124_rkf45 (Q_T2, passed).** `curl` of two Wikipedia articles on the
  Fehlberg method (it14, it15, it17); the output shows the article text
  and, at it17, the Butcher tableau coefficients. Excluded: the pass is
  withheld.
- **126_hmac_scratch (Q_T2, passed).** `curl` of RFC 4231 and RFC 2202
  from rfc-editor.org (it5, it9, it10); the output shows test-vector text.
  Public test vectors, not an answer key, but an off-host read is
  contamination under the scan's rule (the same unit and the same kind of
  read as in Q38U-L0). Excluded: the pass is withheld.
- **122_gemm_blocked_d (R1, failed).** `curl` of a rustc 1.100.0 standard
  library source file (`core_arch/src/x86/avx.rs`) from
  raw.githubusercontent.com (it44); the grep output shows the file
  arrived. A later `web_search` (it56) was refused. Excluded; no score
  effect (a fail). The same unit also failed at the Linux `dlsym` binary
  check: see below.

Score: 54 raw, 52 scored.

Attempts only (refused web tools, no shell network access): Q_T2's
`163_bigint_divmod_d` (`web_search`), `183_heston_little_trap` (`fetch`),
`36_black_scholes_b` (`web_search`, a pass). Not contamination.

## 122_gemm_blocked_d: the dlsym check (docs/MODEL_CAMPAIGN.md procedure)

R1's row reads `the multiply must be your own code: the binary imports
dlsym`. No replay (the N = 2048 case is a multi-threaded CPU job, and the
rig's GPU was booked for another benchmark). Rebuild and check instead:

- Final source recovered from the transcript
  (`agent-20261123-231113-84abeed3.jsonl`): one `write_file` and 15
  `edit_file` calls on `/tmp/eval_gemm/gemm.rs`, each `old_string`
  matching exactly once in sequence; no `bash` command wrote the file
  (only `rustc`, the agent's own `ref.py`, and runs). The last edit was
  followed by a compile and a run, so the recovered source is the one the
  validator built. sha256 of the recovered file:
  `285e627eedfb2b4a6c17a2162593a8f09ad7994af93c1c17e888f78edfc0d767`.
- Rebuilt in `openbeast-eval-host:8d1e28442569` (`--network none`) with
  `/opt/openbeast-eval/rust-1.100.0/bin/rustc` (`rustc 1.100.0
  (0ef1bc386 2026-11-09)`): `rustc -O --edition 2021 gemm.rs -o gemm`,
  build OK.
- `readelf -W --dyn-syms --syms`: undefined symbols against `_BAD_IMPORT`
  minus `dlsym`: none. Defined symbols against `_BAD_DEFINED`: none.
  `dlsym@GLIBC_2.34` is the only loader import. `NEEDED`: libgcc_s.so.1,
  libc.so.6, ld-linux-x86-64.so.2, all within `_LINUX_LIB`. The patterns
  were re-read from `evals/tasks/122_gemm_blocked.json` at origin/main and
  match the harness9 copy.

So `exclusions.json` carries the `validator_notes` entry (run `Q38U-T2`):
"verdict not measured: the validator stopped at the binary check on std's
dlsym (Linux host); rebuilt binary imports no other loader or BLAS
symbol". Nothing shows the solution correct. The unit stays not passed;
it is also excluded above.

## Follow-up

- ~~Add this unit's source to the pending replay of the calibration's
  three Rust `122_gemm_blocked_d` solutions under the proposed rule.~~
  Done 2026-11-23: the same source (sha256 `285e627e…`, recovered again
  independently from the transcript) passes the 6 hidden cases under the
  proposed rule, three runs of three; the unpatched validator reproduces
  the `imports dlsym` stop. The `validator_notes` entry now records the
  replay verdict. No score change: the unit stays excluded for the
  shell's off-host read above, which a replay does not undo
  (calibration report §5).
- The Linux agent shell's network deny (HARNESS_NOTES next-era (d)) now
  accounts for 6 exclusions over two rig rows.
