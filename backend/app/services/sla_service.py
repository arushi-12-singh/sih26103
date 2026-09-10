from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.schemas.project import ProjectRiskRequest
from app.services.notification_service import NotificationService
from app.services.prediction_service import SavedPredictionService

PROJECTS_PATH = Path(__file__).resolve().parents[2] / "data" / "projects.csv"
SLA_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "sla_config.json"


@dataclass(frozen=True)
class SlaRules:
    amber_days_remaining: int = 30
    critical_risk_percentage: int = 80


@dataclass(frozen=True)
class SlaConfiguration:
    project_id: str
    milestone: str
    deadline: date


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


class SlaService:
    def __init__(
        self,
        *,
        projects_path: Path = PROJECTS_PATH,
        config_path: Path = SLA_CONFIG_PATH,
        notification_service: NotificationService | None = None,
        prediction_service: SavedPredictionService | None = None,
        today: date | None = None,
    ) -> None:
        self.today = today
        self.notification_service = notification_service or NotificationService()
        self.prediction_service = prediction_service
        with projects_path.open(newline="", encoding="utf-8") as handle:
            self.projects = {row["project_id"]: row for row in csv.DictReader(handle)}
        with config_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        self.rules = SlaRules(**raw.get("rules", {}))
        self.configurations = {
            project_id: SlaConfiguration(
                project_id=project_id,
                milestone=entry["milestone"],
                deadline=date.fromisoformat(entry["deadline"]),
            )
            for project_id, entry in raw.get("projects", {}).items()
        }

    def evaluate(self, project_id: str, *, trigger_notification: bool = True, allow_retry: bool = False) -> dict[str, Any]:
        if project_id not in self.projects and project_id not in self.configurations:
            raise KeyError("Project not found")
        configuration = self.configurations.get(project_id)
        if configuration is None:
            raise ValueError("SLA data is not configured for this project")

        reference_date = self.today or date.today()
        delta = (configuration.deadline - reference_date).days
        days_remaining = max(delta, 0)
        days_overdue = max(-delta, 0)
        risk_score = self._risk_score(self.projects.get(project_id, {}))
        if delta <= 0:
            severity = "CRITICAL" if risk_score is not None and risk_score >= self.rules.critical_risk_percentage else "RED"
            sla_status = "BREACHED"
            escalation_level = "IMMEDIATE" if severity == "CRITICAL" else "ESCALATED"
        elif delta <= self.rules.amber_days_remaining:
            severity, sla_status, escalation_level = "AMBER", "APPROACHING", "WARNING"
        else:
            severity, sla_status, escalation_level = "GREEN", "HEALTHY", "NONE"
        notification_required = severity in {"RED", "CRITICAL"}
        notification_status = "not_required"
        notification_record: dict[str, Any] | None = None
        if notification_required and trigger_notification:
            notification_result = self.notification_service.notify(
                project_id=project_id,
                event_key=f"{project_id}:{configuration.deadline.isoformat()}:{severity}",
                severity=severity,
                variables={
                    "project_id": project_id,
                    "severity": severity,
                    "sla_status": sla_status,
                    "days_overdue": str(days_overdue),
                    "risk_score": str(risk_score if risk_score is not None else "unavailable"),
                    "sender": self.notification_service.settings.sender_id,
                },
                allow_retry=allow_retry,
            )
            notification_status = notification_result.status
            notification_record = {
                "recipient": notification_result.recipient,
                "timestamp": notification_result.timestamp,
                "msg91_request_id": notification_result.request_id,
                "notification_error": notification_result.error,
            }
        return {
            "project_id": project_id,
            "milestone": configuration.milestone,
            "deadline": configuration.deadline,
            "sla_status": sla_status,
            "days_remaining": days_remaining,
            "days_overdue": days_overdue,
            "severity": severity,
            "escalation_level": escalation_level,
            "notification_required": notification_required,
            "ai_risk_score": risk_score,
            "notification_status": notification_status,
            **(notification_record or {}),
        }

    def _risk_score(self, row: dict[str, str]) -> int | None:
        if not row:
            return None
        service = self.prediction_service
        if service is None:
            return None
        payload = ProjectRiskRequest(
            project_id=row["project_id"],
            sector=row["sector"],
            state=row["state"],
            original_cost=float(row["original_cost"]),
            revised_cost=float(row["revised_cost"]),
            planned_duration_months=int(row["planned_duration_months"]),
            project_age_months=int(row["project_age_months"]),
            physical_progress=float(row["physical_progress"]),
            financial_progress=float(row["financial_progress"]),
            milestones_total=int(row["milestones_total"]),
            milestones_delayed=int(row["milestones_delayed"]),
            land_acquisition_pending=_as_bool(row["land_acquisition_pending"]),
            clearance_pending=_as_bool(row["clearance_pending"]),
            funding_issue=_as_bool(row["funding_issue"]),
            contractor_issue=_as_bool(row["contractor_issue"]),
            previous_schedule_deviation=float(row["previous_schedule_deviation"]),
        )
        return service.predict(payload).project_risk.risk_percentage
