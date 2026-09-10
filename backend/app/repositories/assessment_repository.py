"""Storage for GIS assessment history (Feature 6, API 5).

Same shape as app/repositories/gis_boundary_repository.py: an abstract contract plus a
JSON-file backend, so the service layer never knows where records live.

`JSONFileAssessmentRepository` appends to a single JSON document, written atomically.
Assessments are an append-mostly audit log rather than mutable state, which is why the
contract has `add`, `get`, and `for_project` but no `update`: correcting an assessment
means recording a new one, not editing the record of what was concluded at the time.

A PostgreSQL backend is not implemented here. The table is defined in
migrations/0002_create_gis_assessments.sql and mirrors this contract exactly, so adding
one is a self-contained change -- but writing a second SQL backend that no test in this
environment can exercise would be shipping unverified code, so it is deliberately left
until a database is actually available.
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import gis_config as gis
from app.schemas.assessment import AssessmentRecord

ASSESSMENT_STORE_PATH = gis.BACKEND_ROOT / "data" / "gis" / "assessments.json"


class AssessmentNotFoundError(LookupError):
    """Raised when an assessment id, or a project's history, does not exist."""


class AssessmentRepository(ABC):
    """The storage contract for assessment history."""

    backend: str = "abstract"

    @abstractmethod
    def add(self, record: AssessmentRecord) -> AssessmentRecord:
        """Persist one assessment."""

    @abstractmethod
    def get(self, assessment_id: str) -> AssessmentRecord:
        """Fetch one assessment by id. Raises AssessmentNotFoundError."""

    @abstractmethod
    def for_project(self, project_id: str, *, limit: int | None = None, offset: int = 0) -> list[AssessmentRecord]:
        """Fetch a project's assessments, newest first."""

    @abstractmethod
    def count(self, project_id: str | None = None) -> int:
        """Count all assessments, or one project's."""

    @abstractmethod
    def clear(self) -> int:
        """Remove every assessment; returns how many were removed."""


class JSONFileAssessmentRepository(AssessmentRepository):
    """Append-mostly JSON file store, held in memory and written atomically."""

    backend = "json-file"

    def __init__(self, path: Path | str = ASSESSMENT_STORE_PATH, *, autoload: bool = True) -> None:
        self.path = Path(path)
        self._records: dict[str, AssessmentRecord] = {}
        # project_id -> insertion-ordered ids, so a project's history needs no scan.
        self._by_project: dict[str, list[str]] = {}
        if autoload:
            self.load()

    # --- persistence ---------------------------------------------------------------

    def load(self) -> int:
        """(Re)read from disk. A missing file is an empty log, not an error."""
        self._records.clear()
        self._by_project.clear()
        if not self.path.exists():
            return 0
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in payload.get("assessments", []):
            self._index(AssessmentRecord.model_validate(entry))
        return len(self._records)

    def save(self) -> None:
        """Atomic write: a crash mid-save cannot truncate the audit log."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "type": "GISAssessmentLog",
            "count": len(self._records),
            "assessments": [record.model_dump(mode="json") for record in self._records.values()],
        }
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
        )
        try:
            with handle as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    # --- contract ------------------------------------------------------------------

    def add(self, record: AssessmentRecord) -> AssessmentRecord:
        self._index(record)
        self.save()
        return record

    def get(self, assessment_id: str) -> AssessmentRecord:
        try:
            return self._records[assessment_id]
        except KeyError as exc:
            raise AssessmentNotFoundError(f"No assessment with id {assessment_id!r}.") from exc

    def for_project(self, project_id: str, *, limit: int | None = None, offset: int = 0) -> list[AssessmentRecord]:
        ids = self._by_project.get(project_id, [])
        # Newest first, with id as a tiebreaker so records written in the same instant
        # still come back in a stable, reproducible order.
        records = sorted(
            (self._records[i] for i in ids), key=lambda r: (r.created_at, r.id), reverse=True
        )
        window = records[offset:]
        return window[:limit] if limit is not None else window

    def count(self, project_id: str | None = None) -> int:
        if project_id is None:
            return len(self._records)
        return len(self._by_project.get(project_id, []))

    def clear(self) -> int:
        removed = len(self._records)
        self._records.clear()
        self._by_project.clear()
        self.save()
        return removed

    # --- internals -----------------------------------------------------------------

    def _index(self, record: AssessmentRecord) -> None:
        if record.id not in self._records:
            self._by_project.setdefault(record.project_id, []).append(record.id)
        self._records[record.id] = record


def build_assessment_repository(*, path: Path | str | None = None) -> AssessmentRepository:
    """Construct the configured backend (currently always the JSON file store)."""
    return JSONFileAssessmentRepository(path or ASSESSMENT_STORE_PATH)
