"""Decision ledger, rolling stats and Prometheus text (plan §5.12).

  .run/instinct/decisions-YYYYMMDD.jsonl   one row per call (I5) — shadow,
                                           fallback and eval-refused included
  .run/instinct/feedback-YYYYMMDD.jsonl    append-only outcomes (join: NEXT),
                                           pruned on the same retention_days
Files are 0600 in a 0700 directory, rotated daily, pruned after
retention_days. By default a row carries only input_sha256; an excerpt
(first 160 + last 160 chars) or the full text is written only when the
decision's privacy.log_inputs (or the service default) asks for it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

LATENCY_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
MASS_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0)


def input_sha256(inputs: Any, items: Any = None) -> str:
    blob = json.dumps({"inputs": inputs, "items": items}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def excerpt(inputs: dict) -> dict:
    out = {}
    for k, v in inputs.items():
        if isinstance(v, str) and len(v) > 320:
            out[k] = v[:160] + " … " + v[-160:]
        else:
            out[k] = v
    return out


def _append(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Ledger:
    def __init__(self, directory: Path, *, retention_days: int = 30,
                 clock=time.time):
        self.dir = Path(directory)
        self.retention_days = retention_days
        self.clock = clock
        self._lock = threading.Lock()
        self._last_prune_day = ""
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        # rolling in-memory stats
        self.counts: dict[tuple, int] = defaultdict(int)          # (decision, mode, action)
        self.fallbacks: dict[tuple, int] = defaultdict(int)       # (decision, reason)
        self.lat_hist: dict[tuple, list[int]] = {}                # (decision, engine)
        self.lat_sum: dict[tuple, float] = defaultdict(float)
        self.mass_hist: dict[str, list[int]] = {}
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

    def _day(self, ts: float | None = None) -> str:
        return datetime.fromtimestamp(ts if ts is not None else self.clock(),
                                      tz=timezone.utc).strftime("%Y%m%d")

    def path_for(self, ts: float | None = None) -> Path:
        return self.dir / f"decisions-{self._day(ts)}.jsonl"

    def prune(self) -> list[str]:
        cutoff = datetime.fromtimestamp(self.clock(), tz=timezone.utc) - timedelta(
            days=self.retention_days)
        removed = []
        for p in [*self.dir.glob("decisions-*.jsonl"), *self.dir.glob("feedback-*.jsonl")]:
            stamp = p.name.split("-", 1)[1][:-len(".jsonl")]
            try:
                day = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if day < cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
                try:
                    p.unlink()
                    removed.append(p.name)
                except OSError:
                    pass
        return removed

    def write(self, row: dict) -> None:
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._lock:
            day = self._day()
            if day != self._last_prune_day:
                self._last_prune_day = day
                self.prune()
            _append(self.path_for(), line)
            if row.get("kind", "decide") in ("decide", "route"):
                self._observe(row)

    def feedback(self, row: dict) -> None:
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self._lock:
            day = self._day()
            if day != self._last_prune_day:
                self._last_prune_day = day
                self.prune()
            _append(self.dir / f"feedback-{day}.jsonl", line)

    def _observe(self, row: dict) -> None:
        d = row.get("decision", "?")
        self.counts[(d, row.get("mode"), row.get("action"))] += 1
        if row.get("fallback_reason"):
            self.fallbacks[(d, row["fallback_reason"])] += 1
        eng = (row.get("engine") or {}).get("id") or "none"
        ms = float((row.get("latency_ms") or {}).get("total") or 0.0)
        h = self.lat_hist.setdefault((d, eng), [0] * (len(LATENCY_BUCKETS) + 1))
        for i, b in enumerate(LATENCY_BUCKETS):
            if ms <= b:
                h[i] += 1
        h[-1] += 1
        self.lat_sum[(d, eng)] += ms
        lm = row.get("label_mass")
        if isinstance(lm, (int, float)):
            mh = self.mass_hist.setdefault(d, [0] * (len(MASS_BUCKETS) + 1))
            for i, b in enumerate(MASS_BUCKETS):
                if lm <= b:
                    mh[i] += 1
            mh[-1] += 1
        self.recent[d].append({"action": row.get("action"), "mode": row.get("mode"),
                               "agree": row.get("agree"), "ms": ms, "label_mass": lm,
                               "fallback_reason": row.get("fallback_reason")})

    def stats(self, decision: str | None = None) -> dict:
        out: dict[str, Any] = {}
        decisions = sorted({k[0] for k in self.counts} | set(self.recent))
        for d in decisions:
            if decision and d != decision:
                continue
            rec = list(self.recent.get(d, []))
            mix: dict[str, int] = defaultdict(int)
            for (dd, _mode, action), n in self.counts.items():
                if dd == d:
                    mix[action] += n
            lat = sorted(r["ms"] for r in rec)
            masses = sorted(r["label_mass"] for r in rec
                            if isinstance(r["label_mass"], (int, float)))
            agree = [r["agree"] for r in rec if isinstance(r["agree"], bool)]
            out[d] = {
                "mix": dict(mix),
                "fallback_reasons": {r: n for (dd, r), n in self.fallbacks.items() if dd == d},
                "agreement_with_baseline": (sum(agree) / len(agree)) if agree else None,
                "latency_ms": {q: _pct(lat, q) for q in ("p50", "p95", "p99")},
                "label_mass": {q: _pct(masses, q) for q in ("p05", "p50", "p95")},
                "window": len(rec),
            }
        return out

    def prometheus(self, *, engines_up: dict[str, bool], calibrated: dict[str, bool],
                   effective_enforce: dict[str, bool]) -> str:
        lines = [
            "# HELP instinct_decisions_total Decisions served, by mode and action.",
            "# TYPE instinct_decisions_total counter"]
        for (d, mode, action), n in sorted(self.counts.items(), key=str):
            lines.append(f'instinct_decisions_total{{decision="{_esc(d)}",mode="{_esc(mode)}",'
                         f'action="{_esc(action)}"}} {n}')
        lines += ["# HELP instinct_fallback_total Fallbacks by reason.",
                  "# TYPE instinct_fallback_total counter"]
        for (d, reason), n in sorted(self.fallbacks.items()):
            lines.append(f'instinct_fallback_total{{decision="{_esc(d)}",'
                         f'reason="{_esc(reason)}"}} {n}')
        lines += ["# HELP instinct_latency_ms Decision latency.",
                  "# TYPE instinct_latency_ms histogram"]
        for (d, eng), h in sorted(self.lat_hist.items()):
            lab = f'decision="{_esc(d)}",engine="{_esc(eng)}"'
            for i, b in enumerate(LATENCY_BUCKETS):
                lines.append(f'instinct_latency_ms_bucket{{{lab},le="{b}"}} {h[i]}')
            lines.append(f'instinct_latency_ms_bucket{{{lab},le="+Inf"}} {h[-1]}')
            lines.append(f"instinct_latency_ms_sum{{{lab}}} {self.lat_sum[(d, eng)]:.3f}")
            lines.append(f"instinct_latency_ms_count{{{lab}}} {h[-1]}")
        lines += ["# HELP instinct_label_mass Label mass of engine answers.",
                  "# TYPE instinct_label_mass histogram"]
        for d, h in sorted(self.mass_hist.items()):
            lab = f'decision="{_esc(d)}"'
            for i, b in enumerate(MASS_BUCKETS):
                lines.append(f'instinct_label_mass_bucket{{{lab},le="{b}"}} {h[i]}')
            lines.append(f'instinct_label_mass_bucket{{{lab},le="+Inf"}} {h[-1]}')
            lines.append(f"instinct_label_mass_count{{{lab}}} {h[-1]}")
        lines += ["# TYPE instinct_engine_up gauge"]
        for e, up in sorted(engines_up.items()):
            lines.append(f'instinct_engine_up{{engine="{_esc(e)}"}} {int(bool(up))}')
        lines += ["# TYPE instinct_calibrated gauge"]
        for d, c in sorted(calibrated.items()):
            lines.append(f'instinct_calibrated{{decision="{_esc(d)}"}} {int(bool(c))}')
        lines += ["# TYPE instinct_effective_enforce gauge"]
        for d, c in sorted(effective_enforce.items()):
            lines.append(f'instinct_effective_enforce{{decision="{_esc(d)}"}} {int(bool(c))}')
        return "\n".join(lines) + "\n"


def _pct(xs: list[float], q: str) -> float | None:
    if not xs:
        return None
    frac = {"p05": 0.05, "p50": 0.5, "p95": 0.95, "p99": 0.99}[q]
    i = min(len(xs) - 1, max(0, int(round(frac * (len(xs) - 1)))))
    return xs[i]


def read_rows(directory: Path, since_ts: float = 0.0,
              decision: str | None = None) -> Iterable[dict]:
    for p in sorted(Path(directory).glob("decisions-*.jsonl")):
        try:
            with open(p) as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("ts", 0) < since_ts:
                        continue
                    if decision and row.get("decision") != decision:
                        continue
                    yield row
        except OSError:
            continue
