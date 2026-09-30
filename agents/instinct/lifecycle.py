"""Lifecycle: effective mode, gate matching, demotion (plan §5.7).

effective_mode = min over (off < shadow < canary < enforce) of:
  spec.policy.mode;
  enforce iff a gate record for this EXACT decision_hash says passed (and, by
  default, is committed to git) — else shadow;
  shadow if there is no calibration record for the hash;
  shadow if the engine is unhealthy or its last probe is stale (> 2x interval);
  shadow if the engine has no probabilities (I6);
  shadow if auto-demoted or operator-demoted;
  the caller's ceiling.
Promotion is never a toggle: it takes a committed gate record plus a spec edit.
Demotion is cheap and always allowed, because it only moves toward today's
behaviour.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .core import MODE_ORDER


@dataclass
class LifecycleInputs:
    spec_mode: str
    gate_ok: bool
    calib_ok: bool
    engine_healthy: bool
    probe_fresh: bool
    probabilistic: bool
    demoted: str | None
    ceiling: str


def effective_mode(li: LifecycleInputs) -> tuple[str, str | None]:
    """(mode, reason). `reason` names the FIRST cap that held the mode below the
    spec's target (or the target itself when the target is below enforce)."""
    caps: list[tuple[str, str]] = [(li.spec_mode, "lifecycle_off" if li.spec_mode == "off"
                                    else "lifecycle_shadow")]
    if not li.probabilistic:
        caps.append(("shadow", "uncalibrated"))
    if not li.calib_ok:
        caps.append(("shadow", "uncalibrated"))
    if not li.gate_ok:
        caps.append(("shadow", "no_gate"))
    if not li.engine_healthy:
        caps.append(("shadow", "conformance_failed"))
    elif not li.probe_fresh:
        caps.append(("shadow", "conformance_failed"))
    if li.demoted:
        caps.append(("shadow", "demoted"))
    caps.append((li.ceiling, "caller_ceiling"))
    low = min(MODE_ORDER[m] for m, _ in caps)
    mode = next(m for m, _ in caps if MODE_ORDER[m] == low)
    if mode in ("enforce", "canary"):
        return mode, None
    return mode, next(r for m, r in caps if MODE_ORDER[m] == low)


def git_committed(path: Path, repo: Path) -> bool:
    """True iff `path` is tracked AND unmodified in `repo`'s working tree."""
    try:
        rel = os.path.relpath(path, repo)
        r1 = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", rel],
                            capture_output=True, timeout=5)
        if r1.returncode != 0:
            return False
        r2 = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--", rel],
                            capture_output=True, timeout=5)
        return r2.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def gate_record_ok(rec: dict | None, dhash: str, calib_rec: dict | None) -> bool:
    if not rec or rec.get("decision_hash") != dhash or rec.get("passed") is not True:
        return False
    if not calib_rec:
        return False
    # The gate must have been computed against THIS calibration.
    return rec.get("calib_sha256") == calib_rec.get("_sha256")


class Demotions:
    """Operator demotions (.run/instinct/demoted.json, written by
    `scripts/instinct.sh demote`) plus in-memory auto-demotions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.operator: dict[str, dict] = {}
        self.auto: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            self.operator = {k: v for k, v in data.items() if isinstance(v, dict)} \
                if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.operator = {}

    def reason(self, decision: str) -> str | None:
        if decision in self.operator:
            return "operator:" + str(self.operator[decision].get("reason", "demoted"))
        if decision in self.auto:
            return "auto:" + self.auto[decision]["reason"]
        return None

    def clear_auto(self) -> None:
        self.auto.clear()


class AutoDemoter:
    """Rolling-window auto-demotion (plan §5.7):
    fallback-or-timeout rate > 5% over the last 100 calls, or a label_mass p50
    more than 0.2 below the calibration reference over the last 200 calls."""

    def __init__(self, demotions: Demotions, *, window: int = 100, rate: float = 0.05,
                 mass_window: int = 200, mass_drop: float = 0.2):
        self.demotions = demotions
        self.window, self.rate = window, rate
        self.mass_window, self.mass_drop = mass_window, mass_drop
        self.outcomes: dict[str, deque] = {}
        self.masses: dict[str, deque] = {}

    def observe(self, decision: str, *, failed: bool, label_mass: float | None,
                mass_ref_p50: float | None) -> str | None:
        o = self.outcomes.setdefault(decision, deque(maxlen=self.window))
        o.append(bool(failed))
        if len(o) == self.window and sum(o) / len(o) > self.rate:
            return self._demote(decision, f"fallback_rate {sum(o)}/{len(o)}")
        if label_mass is not None:
            m = self.masses.setdefault(decision, deque(maxlen=self.mass_window))
            m.append(label_mass)
            if (mass_ref_p50 is not None and len(m) == self.mass_window
                    and statistics.median(m) < mass_ref_p50 - self.mass_drop):
                return self._demote(decision, f"label_mass p50 {statistics.median(m):.3f} "
                                              f"< ref {mass_ref_p50:.3f} - {self.mass_drop}")
        return None

    def _demote(self, decision: str, reason: str) -> str:
        if decision not in self.demotions.auto:
            self.demotions.auto[decision] = {"reason": reason, "at": time.time()}
        return reason
