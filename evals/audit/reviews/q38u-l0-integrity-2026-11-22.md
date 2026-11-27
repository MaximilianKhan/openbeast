# Q38U-L0 integrity review notes (2026-11-22, sealed)

The run: Qwen3.8 27B Uncensored MTP Q5_K_M, the L0 profile arm, RTX 5090,
llama.cpp, harness10 (era 9bcd5aa6), driven from the rig's eval-host
container (Linux). Result file
`evals/boards/v5/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L0-20261122-083815.json`.
These notes are kept out of the public board note (board.json is rendered
into README.md and docs/RESULTS.md, which a measured agent can read).

## The scan

`evals/audit/integrity_scan.py Q38U_L0=<result> --logs-dir <the rig's agents/logs>`,
run on the Mac against copies of the rig's files, under `TZ=UTC`: the
container writes naive UTC timestamps (in the result header and the log
names), and the results window is the header timestamp to the file's
mtime. A copy that resets the mtime (scp without `-p`), or a scan in the
Mac's local zone, cuts the window and attributes only part of the run (the
first attempt matched 18 of 71 units). With `scp -p` and `TZ=UTC`: 72
transcripts, 71 units, 3 contaminated. Rechecked 2026-11-22 after the scan
stopped adding forward slack past the window's end (`--tz UTC`): 71
transcripts, 71 units, 3 contaminated, the same three units; the 72nd was
the next run's first unit.

## The three units

On Linux the agent's shell has no kernel network deny (agents/tools.py
`_eval_read_deny_prefix` is macOS only; evals/README.md residual gaps).
The `fetch` and `web_search` tools were refused in every case, and the
agent then tried `curl` from `bash`:

- **126_hmac_scratch (passed).** `curl` of RFC 2202 and RFC 4231 from
  ietf.org succeeded (11,942 and 17,725 bytes saved under /tmp). These are
  public test-vector documents, not an answer key, but an off-host read is
  contamination under the scan's rule. Excluded: the pass is withheld
  (40 raw, 39 scored).
- **163_bigint_divmod_e (failed).** `curl` of a university course page; the
  pipeline's exit code 1 came from the `grep` at its end, so whether the
  page arrived cannot be told from the transcript. Treated as a read.
  Excluded; no score effect.
- **192_pike_regex (failed).** `curl` of a source file on
  raw.githubusercontent.com (not this project's repository); the file
  written had 0 lines, consistent with a 404 body, so the host was reached.
  Excluded; no score effect.

Two other units (124_rkf45, 183_heston_little_trap) only tried the refused
web tools: attempts, not contamination.

## Follow-up

- The Linux agent shell needs the macOS sandbox's network rule (loopback
  only) before a Linux host's rows are compared with the Mac's: proposal (d)
  in docs/HARNESS_NOTES.md "Next-era proposals from the 2026-11-22 runs".
- Scan improvement: read the results window in the header's own zone (or
  record a zone in the header) so a copied file scans the same on any host.
