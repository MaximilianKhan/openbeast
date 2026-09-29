#!/usr/bin/env python3
"""Live row validity stamp — the check e32_capability{2,3}.sh CLAIMED to do.

Those scripts' headers promised "any CUDA error in the serve log or >10%
zero-token failures marks the row INVALID". The CUDA half was written as

    crashes=$(grep -c "CUDA error" "$log" 2>/dev/null || echo 0)

which appends a second "0" whenever grep matches nothing (grep -c prints 0 AND
exits 1), so $crashes became "0\\n0" and the comparison died with
"[: 0\\n0: integer expected" — the guard never fired in either direction. The
zero-token half was never implemented at all. Both rows of the 2026-09-14 IQ3
pair therefore finished with no validity verdict, and the question "did this
10-hour arm survive?" was answered a day later, by hand, from a log.

This module is THE classifier: e32_cap_verdict.py (both the openbeast-research
copy and research/lowrank/.../32-t117-gsq-head-to-head/) and tier3_verdict.py
import it instead of keeping their own, so there is nothing left to drift.

A failed row is classified from its OWN fields (2026-09-29 review,
research-stats-1). The old rule called "agent_exit_code None and elapsed 0" a
benign cache hit — but a replayed row keeps its original exit code and
elapsed time and carries from_cache=True, and cacheable_result() never banks a
zero-token fail, so that shape is only ever the harness's own death record
(setup_failed, server_unhealthy, skipped_cache_miss). The "4 cached" on the
09-14 UD-IQ3 row were four OpenBLAS pthread setup deaths, relabelled benign.

  cached  — from_cache is True (a replay; its verdict was stamped when banked)
  infra   — the harness never ran the agent: reason in INFRA_REASONS, or no
            exit code and zero elapsed on a live row. Never the model.
  timeout — exit -1, the harness wall-timeout sentinel. An honest fail.
  killed  — any other negative exit: a signal death (-9 OOM, -15 SIGTERM).
  dead    — a live FAILED row with a normal exit (>= 0) and zero completion
            tokens: the agent never got a single token back. That is what a
            unit run against a dead or SIGKILLed llama-server looks like —
            "Connection error." on every iteration, then exit 0 — and every
            finished zero-token agent log in agents/logs (114 of 114 on
            2026-09-29) holds API errors. Never the model. (A timeout is -1,
            so it is not caught here.)
  other   — the agent ran, produced tokens, and exited normally.

Two more contaminations are not visible in the exit code at all:
  EAGAIN  — validation died on fork/pthread exhaustion (the uid-wide
            RLIMIT_NPROC cap; review tools-mcp-security-1), exit 0, tokens>0.
  API     — llama-server died mid-unit: the agent log shows "Connection
            error." (or another API error) on the remaining iterations and the
            runner still exits 0 with the tokens it had (review eval-harness-1).
            Rows are matched to agents/logs/*.jsonl by (tokens_completion,
            tokens_prompt) plus a time window.

A row is INVALID on any infra / dead / EAGAIN / API-error failure (each one is a
discordant pair the model did not earn — rerun those units and patch them in
with patchup_replace.py), on >10% killed zero-token fails, or on CUDA errors
in the serve log. It is INCOMPLETE when n differs from the pinned suite size.

usage: row_validity.py <results.json> [--serve-log <path>] [--agent-logs <dir>|none]
                       [--expected-n N]
exit 0 = valid, 1 = INVALID or INCOMPLETE, 2 = could not tell
"""
import collections
import datetime
import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFRA_REASONS = {"setup_failed", "server_unhealthy", "skipped_cache_miss"}
EAGAIN_RE = re.compile(r"Resource temporarily unavailable|SystemResources|"
                       r"thread constructor failed|pthread_create failed|"
                       r"fork: retry|can't start new thread")
KILLED_MAX_FRAC = 0.10
MATCH_WINDOW_S = 600          # agent-log finish -> cached_at, for replayed rows


def kind(x):
    if x.get("from_cache"):
        return "cached"
    if x.get("reason") in INFRA_REASONS:
        return "infra"
    rc = x.get("agent_exit_code")
    if rc is None and (x.get("elapsed_seconds") or 0) == 0:
        return "infra"
    if rc == -1:
        return "timeout"
    if rc is not None and rc < 0:
        return "killed"
    if rc is not None and not x.get("passed") and not (x.get("tokens_completion") or 0):
        return "dead"
    return "other"


def eagain(x):
    """The EAGAIN signature in a FAILED row's validation output, else None."""
    if x.get("passed"):
        return None
    m = EAGAIN_RE.search(str(x.get("validation_output") or ""))
    return m.group(0) if m else None


def _ts(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def load_log_index(log_dir):
    """{(tokens_completion, tokens_prompt): [(log name, [error texts], finish dt)]}
    over every agent log that reached done/max_iterations."""
    if not log_dir or not os.path.isdir(log_dir):
        # None, not an empty index: callers print "API axis unchecked" on
        # None, and an empty index would read as "checked, found nothing"
        # (a worktree has no agents/logs of its own).
        print(f"row_validity: agent-log dir {log_dir!r} not found — API-error axis unchecked",
              file=sys.stderr)
        return None
    idx = collections.defaultdict(list)
    for f in glob.glob(os.path.join(log_dir, "*.jsonl")):
        errs, fin = [], None
        with open(f, errors="ignore") as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("type") == "error":
                    errs.append(str(e.get("error"))[:70])
                elif e.get("type") in ("done", "max_iterations"):
                    fin = e
        if fin is not None:
            idx[(fin.get("tokens_completion"), fin.get("tokens_prompt"))].append(
                (os.path.basename(f), errs, _ts(fin.get("timestamp"))))
    return idx


def api_errors(x, idx, lo=None, hi=None):
    """(log name, [errors]) when the agent log behind row x holds API errors.

    A replayed row is pinned by cached_at (the log finished 0..600 s before
    it was banked); a live row by the results file's run window [lo, hi].
    Zero-token rows are too generic to match by tokens alone and are only
    matched when a timestamp pins them."""
    key = (x.get("tokens_completion"), x.get("tokens_prompt"))
    cands = idx.get(key) or []
    ca = _ts(x.get("cached_at"))
    if ca is not None:
        cands = [c for c in cands if c[2] is not None
                 and 0 <= (ca - c[2]).total_seconds() <= MATCH_WINDOW_S]
    elif not key[0]:
        # (0, 0) is shared by every timed-out or dead-on-arrival run; a
        # multi-hour run window cannot pick the right one out.
        return None
    elif lo is not None or hi is not None:
        cands = [c for c in cands if c[2] is not None
                 and (lo is None or c[2] >= lo) and (hi is None or c[2] <= hi)]
    hits = [(n, e) for n, e, _ in cands if e]
    return hits[0] if hits else None


def expected_n(d):
    """Pinned size of the suite the row claims to be, when it names one."""
    name = d.get("suite_selection") or (d.get("fast_suite") or {}).get("suite")
    if not name:
        return None
    p = os.path.join(REPO, "evals", "suites", f"{name}.json")
    if not os.path.exists(p):
        return None
    return len(json.load(open(p)).get("units") or [])


def _ids(ids, k=8):
    return ", ".join(ids[:k]) + (f" (+{len(ids) - k} more)" if len(ids) > k else "")


def audit(d, idx=None, want_n=None):
    """Validity of one results dict. Returns a dict; `valid` and `complete`
    are the two verdict bits, `reasons` says why not."""
    tasks = d.get("tasks") or []
    run_lo = _ts(d.get("timestamp"))
    run_lo = run_lo - datetime.timedelta(seconds=60) if run_lo else None
    run_hi = run_lo + datetime.timedelta(days=2) if run_lo else None
    fails = [x for x in tasks if not x.get("passed")]
    ztok = [x for x in fails if (x.get("tokens_completion") or 0) == 0]
    killed = [x["id"] for x in ztok if kind(x) == "killed"]
    infra = [x["id"] for x in fails if kind(x) == "infra"]
    dead = [x["id"] for x in fails if kind(x) == "dead"]
    eag = [x["id"] for x in fails if eagain(x)]
    api = [x["id"] for x in fails if idx is not None
           and api_errors(x, idx, run_lo, run_hi)]
    benign = collections.Counter(kind(x) for x in ztok
                                 if kind(x) in ("cached", "timeout", "other"))
    if want_n is None:
        want_n = expected_n(d)
    reasons = []
    if fails and len(killed) / len(fails) > KILLED_MAX_FRAC:
        reasons.append(f"killed zero-token fails {len(killed)}/{len(fails)} = "
                       f"{len(killed) / len(fails):.0%} > {KILLED_MAX_FRAC:.0%}")
    if infra:
        reasons.append(f"{len(infra)} harness/setup death(s), never the model: {_ids(infra)}")
    if dead:
        reasons.append(f"{len(dead)} live zero-token fail(s) with a normal exit — a dead "
                       f"llama-server, never the model: {_ids(dead)}")
    if eag:
        reasons.append(f"{len(eag)} fork/thread EAGAIN validation death(s): {_ids(eag)}")
    if api:
        reasons.append(f"{len(api)} fail(s) with API/connection errors in the agent log: {_ids(api)}")
    complete = want_n is None or len(tasks) == want_n
    return {"n": len(tasks), "want_n": want_n, "complete": complete,
            "passed": sum(1 for x in tasks if x.get("passed")), "fails": len(fails),
            "killed": killed, "infra": infra, "dead": dead, "eagain": eag, "api": api,
            "benign": benign, "reasons": reasons, "valid": not reasons,
            "contaminated": sorted(set(infra) | set(dead) | set(eag) | set(api))}


def contaminated_ids(d, idx):
    """Unit ids of every row (pass or fail) that is not a clean sample:
    infra and dead-server deaths, EAGAIN fails, and any row whose agent log
    holds API errors."""
    lo = _ts(d.get("timestamp"))
    lo = lo - datetime.timedelta(seconds=60) if lo else None
    hi = lo + datetime.timedelta(days=2) if lo else None
    out = {}
    for x in d.get("tasks") or []:
        why = []
        if not x.get("passed") and kind(x) in ("infra", "dead"):
            why.append(kind(x))
        if eagain(x):
            why.append(f"EAGAIN({eagain(x)})")
        hit = api_errors(x, idx, lo, hi) if idx is not None else None
        if hit:
            why.append(f"API x{len(hit[1])} ({hit[0]})")
        if why:
            out[x["id"]] = why
    return out


def main(argv):
    if len(argv) < 1 or argv[0].startswith("--"):
        print("VALIDITY: no results file given"); return 2
    path = argv[0]
    opt = {}
    i = 1
    while i < len(argv):
        if argv[i] in ("--serve-log", "--agent-logs", "--expected-n") and i + 1 < len(argv):
            opt[argv[i]] = argv[i + 1]; i += 2
        else:
            i += 1
    serve = opt.get("--serve-log")
    logs = opt.get("--agent-logs", os.path.join(REPO, "agents", "logs"))
    if not os.path.exists(path):
        print(f"VALIDITY: results file missing ({path})"); return 2
    d = json.load(open(path))
    if not d.get("tasks"):
        print(f"VALIDITY: no tasks in {os.path.basename(path)}"); return 2
    idx = None if logs == "none" else load_log_index(logs)
    a = audit(d, idx, int(opt["--expected-n"]) if "--expected-n" in opt else None)

    cuda = None
    if serve and os.path.exists(serve):
        with open(serve, errors="ignore") as fh:
            cuda = sum(1 for ln in fh if "CUDA error" in ln or "GGML_ASSERT" in ln)
    reasons = list(a["reasons"])
    if cuda:
        reasons.append(f"{cuda} CUDA error/GGML_ASSERT lines in serve log")
    if not a["complete"]:
        reasons.insert(0, f"n={a['n']} but the pinned suite has {a['want_n']} units")

    benign = ", ".join(f"{n} {k}" for k, n in sorted(a["benign"].items()))
    print(f"VALIDITY {os.path.basename(path)}: n={a['n']}"
          + (f"/{a['want_n']}" if a["want_n"] else "")
          + f" passed={a['passed']} fails={a['fails']} killed={len(a['killed'])}"
          f" infra={len(a['infra'])} dead={len(a['dead'])} eagain={len(a['eagain'])}"
          f" api={len(a['api']) if idx is not None else 'n/a'}"
          + (f" (benign zero-token: {benign})" if benign else "")
          + f" cuda={cuda if cuda is not None else 'n/a'}")
    if reasons:
        label = "ROW INCOMPLETE" if not a["complete"] else "ROW INVALID"
        print(f"VALIDITY: {label} — " + "; ".join(reasons))
        if a["contaminated"]:
            print("VALIDITY: rerun + patchup_replace.py these units: " + ",".join(a["contaminated"]))
        return 1
    notes = []
    if cuda is None:
        notes.append("serve log absent: CUDA axis unchecked")
    if idx is None:
        notes.append("agent logs not read: API-error axis unchecked")
    print("VALIDITY: row clean" + (f"  ({'; '.join(notes)})" if notes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
