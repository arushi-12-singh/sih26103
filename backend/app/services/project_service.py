from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.schemas.project import ProjectRecord

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "projects.csv"

_BOOLEAN_FIELDS = (
    "land_acquisition_pending",
    "clearance_pending",
    "funding_issue",
    "contractor_issue",
)
_INTEGER_FIELDS = (
    "planned_duration_months",
    "project_age_months",
    "milestones_total",
    "milestones_delayed",
)
_FLOAT_FIELDS = (
    "original_cost",
    "revised_cost",
    "physical_progress",
    "financial_progress",
    "previous_schedule_deviation",
)


class ProjectService:
    """Load and serve validated project records from the project dataset."""

    def __init__(self, data_path: Path = DATA_PATH) -> None:
        if not data_path.exists():
            raise FileNotFoundError(f"Project data not found: {data_path}")

        data = pd.read_csv(data_path)
        records = [self._normalize_record(record) for record in data.to_dict(orient="records")]
        self.projects = [ProjectRecord.model_validate(record) for record in records]
        self._by_id = {project.project_id: project for project in self.projects}

    def list_projects(
        self,
        search: str | None = None,
        sector: str | None = None,
        state: str | None = None,
    ) -> list[ProjectRecord]:
        projects = self.projects
        if search:
            query = search.casefold()
            projects = [
                project
                for project in projects
                if query in project.project_id.casefold()
                or query in project.sector.casefold()
                or query in project.state.casefold()
            ]
        if sector:
            projects = [project for project in projects if project.sector.casefold() == sector.casefold()]
        if state:
            projects = [project for project in projects if project.state.casefold() == state.casefold()]
        return projects

    def get_project(self, project_id: str) -> ProjectRecord | None:
        return self._by_id.get(project_id)

    @staticmethod
    def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        normalized.pop("is_delayed", None)
        for field in _BOOLEAN_FIELDS:
            normalized[field] = bool(int(normalized[field]))
        for field in _INTEGER_FIELDS:
            normalized[field] = int(normalized[field])
        for field in _FLOAT_FIELDS:
            normalized[field] = float(normalized[field])
        normalized["project_id"] = str(normalized["project_id"])
        return normalized


def build_project_service() -> ProjectService:
    return ProjectService()
