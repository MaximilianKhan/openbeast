"""Bulk import of jobs from JSON Lines.

Each line is ``{"kind": ..., "payload": {...}, "tenant": ..., "tags": [...]}``.
Lines are submitted through ``JobService.submit`` so every plugin sees
them; a line that a hook rejects is reported and skipped, not fatal.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import IO, List, Tuple

from ferrule.errors import FerruleError
from ferrule.jobs.service import Context

log = logging.getLogger(__name__)


@dataclass
class ImportReport:
    submitted: List[str] = field(default_factory=list)
    rejected: List[Tuple[int, str]] = field(default_factory=list)
    malformed: List[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.malformed


def import_jsonl(service, fh: IO[str], actor: str = "importer", role: str = "operator",
                 default_tenant: str = "default", dry_run: bool = False) -> ImportReport:
    report = ImportReport()
    for lineno, line in enumerate(fh, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            doc = json.loads(line)
            kind = doc["kind"]
        except (ValueError, KeyError, TypeError):
            report.malformed.append(lineno)
            continue
        ctx = Context(actor=actor, role=role, tenant=doc.get("tenant", default_tenant), dry_run=dry_run)
        try:
            job = service.submit(kind, doc.get("payload") or {}, ctx, tags=doc.get("tags"),
                                 queue=doc.get("queue", "default"))
        except FerruleError as exc:
            report.rejected.append((lineno, f"{exc.code}: {exc}"))
            continue
        report.submitted.append(job.id)
    log.info("import: %d submitted, %d rejected, %d malformed",
             len(report.submitted), len(report.rejected), len(report.malformed))
    return report
