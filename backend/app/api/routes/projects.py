from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.project import ProjectRecord
from app.services.project_service import ProjectService

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectRecord], status_code=status.HTTP_200_OK)
def list_projects(
    request: Request,
    search: str | None = Query(default=None, min_length=1),
    sector: str | None = Query(default=None, min_length=1),
    state: str | None = Query(default=None, min_length=1),
) -> list[ProjectRecord]:
    service: ProjectService | None = getattr(request.app.state, "project_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Project data is unavailable")
    return service.list_projects(search=search, sector=sector, state=state)


@router.get("/projects/{project_id}", response_model=ProjectRecord, status_code=status.HTTP_200_OK)
def get_project(request: Request, project_id: str) -> ProjectRecord:
    service: ProjectService | None = getattr(request.app.state, "project_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Project data is unavailable")
    project = service.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
