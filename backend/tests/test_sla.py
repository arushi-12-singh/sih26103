from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.notification_service import NotificationService, NotificationSettings
from app.services.sla_service import PROJECTS_PATH, SlaService


class FakeMsg91:
    def __init__(self, result: str = "req-123", error: bool = False) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def send(self, variables: dict[str, str]) -> str:
        self.calls += 1
        if self.error:
            raise RuntimeError("provider unavailable")
        return self.result


def service(tmp_path: Path, deadline: str, *, enabled: bool = False, client: FakeMsg91 | None = None) -> SlaService:
    config = tmp_path / "sla.json"
    config.write_text(json.dumps({
        "rules": {"amber_days_remaining": 30, "critical_risk_percentage": 80},
        "projects": {"PAI-00001": {"milestone": "Demo milestone", "deadline": deadline}},
    }), encoding="utf-8")
    settings = NotificationSettings(enabled, "key", "template", "PAIMANA", "+919999999999")
    notifications = NotificationService(settings=settings, client=client or FakeMsg91())
    predictor = SimpleNamespace(
        predict=lambda payload: SimpleNamespace(
            project_risk=SimpleNamespace(risk_percentage=92),
        ),
    )
    return SlaService(
        projects_path=PROJECTS_PATH,
        config_path=config,
        notification_service=notifications,
        prediction_service=predictor,
        today=date(2026, 9, 10),
    )


def test_healthy_sla_does_not_notify(tmp_path: Path) -> None:
    result = service(tmp_path, "2026-12-31").evaluate("PAI-00001")
    assert result["severity"] == "GREEN"
    assert result["notification_required"] is False
    assert result["notification_status"] == "not_required"


def test_approaching_sla_is_warning(tmp_path: Path) -> None:
    result = service(tmp_path, "2026-09-25").evaluate("PAI-00001")
    assert result["severity"] == "AMBER"
    assert result["escalation_level"] == "WARNING"


def test_breach_is_red_and_dry_run_is_recorded(tmp_path: Path) -> None:
    result = service(tmp_path, "2026-09-01").evaluate("PAI-00001")
    assert result["severity"] == "CRITICAL"
    assert result["notification_status"] == "dry_run"
    assert len(result["recipient"]) > 0


def test_duplicate_breach_is_suppressed(tmp_path: Path) -> None:
    msg91 = FakeMsg91()
    svc = service(tmp_path, "2026-09-01", enabled=True, client=msg91)
    first = svc.evaluate("PAI-00001")
    second = svc.evaluate("PAI-00001")
    assert first["notification_status"] == "sent"
    assert second["notification_status"] == "already_sent"
    assert msg91.calls == 1


def test_msg91_success_and_failure_are_audited(tmp_path: Path) -> None:
    success_client = FakeMsg91()
    success = service(tmp_path, "2026-09-01", enabled=True, client=success_client)
    assert success.evaluate("PAI-00001")["msg91_request_id"] == "req-123"

    failed_client = FakeMsg91(error=True)
    failed = service(tmp_path, "2026-09-01", enabled=True, client=failed_client)
    failure = failed.evaluate("PAI-00001")
    assert failure["notification_status"] == "failed"
    assert failed.notification_service.audit("PAI-00001")[0]["notification_status"] == "failed"


def test_invalid_and_missing_sla_projects(tmp_path: Path) -> None:
    svc = service(tmp_path, "2026-12-31")
    with pytest.raises(KeyError):
        svc.evaluate("UNKNOWN")
    config = tmp_path / "empty.json"
    config.write_text('{"rules": {}, "projects": {}}', encoding="utf-8")
    missing = SlaService(projects_path=PROJECTS_PATH, config_path=config, today=date(2026, 9, 10))
    with pytest.raises(ValueError):
        missing.evaluate("PAI-00001")
