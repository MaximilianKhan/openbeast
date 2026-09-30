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


def _read_map(path: Path) -> dict[str, dict]:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) \
        else {}


class Demotions:
    """Operator demotions (.run/instinct/demoted.json, written by
    `scripts/instinct.sh demote`) plus auto-demotions, which PERSIST to
    auto-demoted.json beside it (B-instinct-06): a restart must not re-arm a
    flapping engine. An auto-demotion records the decision's engine hashes;
    it ends only when one of them changes (a new thing) or on `undemote`."""

    def __init__(self, path: Path, auto_path: Path | None = None):
        self.path = Path(path)
        self.auto_path = Path(auto_path) if auto_path else self.path.with_name(
            "auto-demoted.json")
        self.operator: dict[str, dict] = {}
        self.auto: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        """(Re)read both files. The service is the only writer of the auto
        file, so re-reading it on SIGHUP picks up an `undemote` and nothing
        else."""
        self.operator = _read_map(self.path)
        self.auto = {k: v for k, v in _read_map(self.auto_path).items()
                     if isinstance(v.get("reason"), str)}

    def add_auto(self, decision: str, rec: dict) -> None:
        self.auto[decision] = rec
        self.save_auto()

    def drop_auto(self, decision: str) -> None:
        if self.auto.pop(decision, None) is not None:
            self.save_auto()

    def save_auto(self) -> None:
        try:
            self.auto_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.auto_path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self.auto, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.auto_path)
        except OSError:
            pass   # a full disk must not take decisions down; memory still holds it

    def reason(self, decision: str) -> str | None:
        if decision in self.operator:
            return "operator:" + str(self.operator[decision].get("reason", "demoted"))
        if decision in self.auto:
            return "auto:" + self.auto[decision]["reason"]
        return None

    def clear_auto(self) -> None:
        self.auto.clear()
        self.save_auto()


class AutoDemoter:
    """Rolling-window auto-demotion (plan §5.7):
    fallback-or-timeout rate > 5% over the last 100 calls, or a label_mass p50
    more than 0.2 below the calibration reference over the last 200 calls."""

    def __init__(self, demotions: Demotions, *, window: int = 100, rate: float = 0.05,
                 mass_window: int = 200, mass_drop: float = 0.2, hashes_for=None):
        self.demotions = demotions
        # decision -> {engine: decision_hash}: what the demotion judged
        self.hashes_for = hashes_for
        self.window, self.rate = window, rate
        self.mass_window, self.mass_drop = mass_window, mass_drop
        self.outcomes: dict[str, deque] = {}
        self.masses: dict[tuple[str, str], deque] = {}

    def observe(self, decision: str, *, failed: bool, label_mass: float | None = None,
                mass_ref_p50: float | None = None, engine: str = "") -> str | None:
        o = self.outcomes.setdefault(decision, deque(maxlen=self.window))
        o.append(bool(failed))
        if len(o) == self.window and sum(o) / len(o) > self.rate:
            return self._demote(decision, f"fallback_rate {sum(o)}/{len(o)}")
        if label_mass is not None:
            return self.observe_mass(decision, engine, label_mass, mass_ref_p50)
        return None

    def observe_mass(self, decision: str, engine: str, label_mass: float,
                     mass_ref_p50: float | None) -> str | None:
        """label_mass windows are per (decision, engine): each engine is
        compared with its OWN calibration reference."""
        m = self.masses.setdefault((decision, engine), deque(maxlen=self.mass_window))
        m.append(label_mass)
        if (mass_ref_p50 is not None and len(m) == self.mass_window
                and statistics.median(m) < mass_ref_p50 - self.mass_drop):
            return self._demote(decision, f"label_mass p50 {statistics.median(m):.3f} "
                                          f"< ref {mass_ref_p50:.3f} - {self.mass_drop}"
                                          + (f" on {engine}" if engine else ""))
        return None

    def reset(self, decision: str) -> None:
        self.outcomes.pop(decision, None)
        for k in [k for k in self.masses if k[0] == decision]:
            self.masses.pop(k, None)

    def _demote(self, decision: str, reason: str) -> str:
        if decision not in self.demotions.auto:
            rec = {"reason": reason, "at": time.time()}
            if self.hashes_for is not None:
                rec["hashes"] = self.hashes_for(decision)
            self.demotions.add_auto(decision, rec)
        return reason
