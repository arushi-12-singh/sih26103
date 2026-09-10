from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
import re
from typing import Set

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

PROJECT_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{2,100}$")

KNOWN_STATIC_PROJECTS: set[str] = {
    "EFC-04",
    "PROJ-UP-01",
    "PROJ-UK-02",
    "PROJ-MH-03",
    "PROJ-RJ-04",
    "PROJ-KA-05",
    "PROJ-OR-06",
    "API-TEST-01",
    "TEST-01",
    "TEST-02",
    "TEST-03",
    "TEST-PROJ-01",
    "TEST-PROJ-02",
}


@lru_cache(maxsize=1)
def get_known_project_ids() -> Set[str]:
    """Load all valid project IDs from data files and static presets."""
    project_ids = set(KNOWN_STATIC_PROJECTS)

    # 1. Load from projects.csv
    projects_csv = DATA_DIR / "projects.csv"
    if projects_csv.exists():
        try:
            with open(projects_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pid = row.get("project_id")
                    if pid:
                        project_ids.add(pid.strip())
        except Exception:
            pass

    # 2. Load from historical_projects.csv
    hist_csv = DATA_DIR / "historical_projects.csv"
    if hist_csv.exists():
        try:
            with open(hist_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pid = row.get("project_id")
                    if pid:
                        project_ids.add(pid.strip())
        except Exception:
            pass

    return project_ids


def is_valid_project_format(project_id: str) -> bool:
    """Validate project ID structure to prevent directory traversal and invalid characters."""
    if not project_id or not isinstance(project_id, str):
        return False
    trimmed = project_id.strip()
    return bool(PROJECT_ID_REGEX.match(trimmed))


def validate_project_exists(project_id: str, extra_valid_ids: set[str] | None = None) -> bool:
    """Check if the given project_id is a valid, existing project."""
    if not is_valid_project_format(project_id):
        return False
    clean_id = project_id.strip()
    if extra_valid_ids and clean_id in extra_valid_ids:
        return True
    return clean_id in get_known_project_ids()
