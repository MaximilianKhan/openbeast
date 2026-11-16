"""Job kinds and the families they belong to.

A kind is ``<family>.<variant>`` by convention, and the family selects the
retry policy (``retry.<family>`` in the settings). Kinds whose behaviour
does not match their name are listed in ``FAMILY_OVERRIDES``.
"""

from __future__ import annotations

from typing import Dict, List

FAMILY_OVERRIDES: Dict[str, str] = {
    # 2.5: HTTP ingestion moved onto the fetcher pool and its retry policy.
    "ingest.http": "fetch",
    "ingest.https": "fetch",
    # Reports are rendered and uploaded like exports.
    "report.pdf": "export",
    "report.csv": "export",
}

KNOWN_KINDS: List[str] = [
    "export.s3",
    "export.gcs",
    "export.sftp",
    "ingest.http",
    "ingest.https",
    "ingest.sftp",
    "ingest.kafka",
    "report.pdf",
    "report.csv",
    "maintenance.vacuum",
    "maintenance.purge",
]


def family(kind: str) -> str:
    if kind in FAMILY_OVERRIDES:
        return FAMILY_OVERRIDES[kind]
    return kind.split(".", 1)[0]


def is_control_plane(kind: str) -> bool:
    return family(kind) == "maintenance"
