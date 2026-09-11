from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool
    auth_key: str
    template_id: str
    sender_id: str
    recipient: str
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "NotificationSettings":
        return cls(
            enabled=os.getenv("MSG91_ENABLED", "false").strip().lower() == "true",
            auth_key=os.getenv("MSG91_AUTH_KEY", "").strip(),
            template_id=os.getenv("MSG91_TEMPLATE_ID", "").strip(),
            sender_id=os.getenv("MSG91_SENDER_ID", "").strip(),
            recipient=os.getenv("MINISTRY_ALERT_PHONE", "").strip(),
        )


@dataclass(frozen=True)
class NotificationResult:
    status: str
    recipient: str
    timestamp: datetime
    request_id: str | None = None
    error: str | None = None


def mask_recipient(recipient: str) -> str:
    if len(recipient) <= 4:
        return "*" * len(recipient)
    return f"{'*' * (len(recipient) - 4)}{recipient[-4:]}"


class Msg91Client:
    endpoint = "https://control.msg91.com/api/v5/flow"

    def __init__(self, settings: NotificationSettings) -> None:
        self.settings = settings

    def send(self, variables: dict[str, str]) -> str:
        if not all((self.settings.auth_key, self.settings.template_id, self.settings.sender_id, self.settings.recipient)):
            raise RuntimeError("MSG91 configuration is incomplete")
        body = {
            "template_id": self.settings.template_id,
            "short_url": "0",
            "recipients": [{"mobiles": self.settings.recipient, **variables}],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "authkey": self.settings.auth_key,
                "accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                response_body: Any = json.loads(response.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MSG91 request failed: {exc}") from exc
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"MSG91 returned HTTP {response.status}")
        if isinstance(response_body, dict) and response_body.get("type") == "error":
            raise RuntimeError("MSG91 rejected the notification request")
        return str(response_body.get("request_id") or response_body.get("message") or "accepted")


class NotificationAuditStore:
    """Process-local audit store for the demo; failed events remain retryable."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = Lock()

    def latest_for(self, event_key: str) -> dict[str, Any] | None:
        with self._lock:
            for event in reversed(self._events):
                if event["event_key"] == event_key:
                    return dict(event)
        return None

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._events.append(dict(event))
        return dict(event)

    def list_for(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if event["project_id"] == project_id]


class NotificationService:
    def __init__(
        self,
        settings: NotificationSettings | None = None,
        client: Msg91Client | None = None,
        audit_store: NotificationAuditStore | None = None,
    ) -> None:
        self.settings = settings or NotificationSettings.from_env()
        self.client = client or Msg91Client(self.settings)
        self.audit_store = audit_store or NotificationAuditStore()

    def notify(
        self,
        *,
        project_id: str,
        event_key: str,
        severity: str,
        variables: dict[str, str],
        allow_retry: bool = False,
    ) -> NotificationResult:
        previous = self.audit_store.latest_for(event_key)
        if previous and previous["notification_status"] in {"sent", "dry_run"} and not allow_retry:
            return NotificationResult(
                status="already_sent",
                recipient=previous["recipient"],
                timestamp=previous["timestamp"],
                request_id=previous.get("msg91_request_id"),
            )

        timestamp = datetime.now(timezone.utc)
        recipient = mask_recipient(self.settings.recipient) if self.settings.recipient else "not-configured"
        if not self.settings.enabled:
            result = NotificationResult("dry_run", recipient, timestamp)
        else:
            try:
                request_id = self.client.send(variables)
            except RuntimeError as exc:
                result = NotificationResult("failed", recipient, timestamp, error=str(exc))
            else:
                result = NotificationResult("sent", recipient, timestamp, request_id=request_id)

        self.audit_store.add({
            "project_id": project_id,
            "event_key": event_key,
            "sla_event": "sla_escalation",
            "severity": severity,
            "recipient": result.recipient,
            "notification_status": result.status,
            "timestamp": result.timestamp,
            "msg91_request_id": result.request_id,
            "error": result.error,
        })
        return result

    def audit(self, project_id: str) -> list[dict[str, Any]]:
        return self.audit_store.list_for(project_id)
