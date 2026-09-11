from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class SlaStatusResponse(BaseModel):
    project_id: str
    milestone: str
    deadline: date
    sla_status: str
    days_remaining: int
    days_overdue: int
    severity: str
    escalation_level: str
    notification_required: bool
    ai_risk_score: int | None = Field(default=None, ge=0, le=100)
    notification_status: str
    notification_recipient: str | None = None
    notification_timestamp: datetime | None = None
    msg91_request_id: str | None = None
    notification_error: str | None = None


class SlaAuditEvent(BaseModel):
    project_id: str
    sla_event: str
    severity: str
    recipient: str
    notification_status: str
    timestamp: datetime
    msg91_request_id: str | None = None
