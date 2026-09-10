from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.sla import SlaAuditEvent, SlaStatusResponse
from app.services.sla_service import SlaService

router = APIRouter(prefix="/projects", tags=["sla"])


def _service(request: Request) -> SlaService:
    service: SlaService | None = getattr(request.app.state, "sla_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="SLA service is unavailable")
    return service


def _evaluate(service: SlaService, project_id: str, *, allow_retry: bool = False) -> SlaStatusResponse:
    try:
        return SlaStatusResponse(**service.evaluate(project_id, allow_retry=allow_retry))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/sla", response_model=SlaStatusResponse)
def get_sla(project_id: str, request: Request) -> SlaStatusResponse:
    return _evaluate(_service(request), project_id)


@router.post("/{project_id}/sla/evaluate", response_model=SlaStatusResponse, status_code=status.HTTP_200_OK)
def evaluate_sla(project_id: str, request: Request) -> SlaStatusResponse:
    return _evaluate(_service(request), project_id)


@router.post("/{project_id}/sla/test-alert", response_model=SlaStatusResponse, status_code=status.HTTP_200_OK)
def test_alert(project_id: str, request: Request) -> SlaStatusResponse:
    return _evaluate(_service(request), project_id, allow_retry=True)


@router.get("/{project_id}/sla/audit", response_model=list[SlaAuditEvent])
def get_sla_audit(project_id: str, request: Request) -> list[SlaAuditEvent]:
    service = _service(request)
    if project_id not in service.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    return [SlaAuditEvent(**event) for event in service.notification_service.audit(project_id)]
