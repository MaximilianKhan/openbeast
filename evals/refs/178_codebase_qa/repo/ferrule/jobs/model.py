"""The Job record."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ferrule.util.ids import new_job_id


@dataclass
class Job:
    kind: str
    tenant: str = "default"
    payload: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_job_id)
    owner: Optional[str] = None
    queue: str = "default"
    state: str = "NEW"
    attempts: int = 0
    tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    not_before_ms: int = 0
    history: List[Tuple[str, str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        doc = asdict(self)
        doc["history"] = [list(h) for h in self.history]
        return doc

    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "Job":
        doc = dict(doc)
        doc["history"] = [tuple(h) for h in doc.get("history", [])]
        return cls(**doc)

    @property
    def terminal(self) -> bool:
        return self.state in ("SUCCEEDED", "DEAD", "CANCELLED")
