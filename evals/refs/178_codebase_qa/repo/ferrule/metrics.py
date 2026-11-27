"""Process metrics in the Prometheus text format.

Counters and histograms are fed from the event bus and the scheduler; the
``/metrics`` endpoint is served by the deployment's sidecar, which calls
``render()``.
"""

from __future__ import annotations

import bisect
import threading
from collections import defaultdict
from typing import DefaultDict, Dict, List, Tuple

LabelKey = Tuple[Tuple[str, str], ...]

DEFAULT_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)


def _labels(labels: Dict[str, str]) -> LabelKey:
    return tuple(sorted(labels.items()))


def _fmt_labels(key: LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in key)
    return "{" + inner + "}"


class Counter:
    def __init__(self, name: str, help: str) -> None:
        self.name = name
        self.help = help
        self._values: DefaultDict[LabelKey, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, n: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._values[_labels(labels)] += n

    def value(self, **labels: str) -> float:
        return self._values.get(_labels(labels), 0.0)

    def render(self) -> List[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        for key, v in sorted(self._values.items()):
            out.append(f"{self.name}{_fmt_labels(key)} {v:g}")
        return out


class Histogram:
    def __init__(self, name: str, help: str, buckets=DEFAULT_BUCKETS_MS) -> None:
        self.name = name
        self.help = help
        self.buckets = tuple(sorted(buckets))
        self._counts: DefaultDict[LabelKey, List[int]] = defaultdict(lambda: [0] * (len(self.buckets) + 1))
        self._sums: DefaultDict[LabelKey, float] = defaultdict(float)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            self._counts[key][bisect.bisect_left(self.buckets, value)] += 1
            self._sums[key] += value

    def render(self) -> List[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key in sorted(self._counts):
            running = 0
            for bound, n in zip(self.buckets + (float("inf"),), self._counts[key]):
                running += n
                le = "+Inf" if bound == float("inf") else f"{bound:g}"
                lab = _fmt_labels(key + (("le", le),))
                out.append(f"{self.name}_bucket{lab} {running}")
            out.append(f"{self.name}_sum{_fmt_labels(key)} {self._sums[key]:g}")
            out.append(f"{self.name}_count{_fmt_labels(key)} {running}")
        return out


JOBS_SUBMITTED = Counter("ferrule_jobs_submitted_total", "Jobs accepted by JobService.submit")
JOBS_FINISHED = Counter("ferrule_jobs_finished_total", "Jobs that reached a terminal state")
RETRY_DELAY = Histogram("ferrule_retry_delay_ms", "Retry delays scheduled, in milliseconds")
REGISTRY = [JOBS_SUBMITTED, JOBS_FINISHED, RETRY_DELAY]


def on_event(topic: str, data) -> None:
    if topic == "job.submitted":
        JOBS_SUBMITTED.inc(kind=str(data.get("kind")))
    elif topic == "job.finished":
        JOBS_FINISHED.inc(state=str(data.get("state")))


def attach(events) -> None:
    events.subscribe("job.*", on_event)


def render() -> str:
    lines: List[str] = []
    for metric in REGISTRY:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"
