from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.documents import router as documents_router
from app.api.routes.gis import router as gis_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.prediction import router as prediction_router
from app.api.routes.priority import router as priority_router
from app.api.routes.projects import router as projects_router
from app.api.routes.similarity import router as similarity_router
from app.services.document_service import build_document_service
from app.services.gis_service import build_gis_service
from app.services.prediction_service import build_prediction_service
from app.services.priority_service import build_priority_service
from app.services.project_service import build_project_service
from app.services.similarity_service import build_similarity_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        app.state.prediction_service = build_prediction_service()
        app.state.model_error = None
    except (FileNotFoundError, ValueError, OSError) as exc:
        app.state.prediction_service = None
        app.state.model_error = str(exc)
    try:
        app.state.similarity_service = build_similarity_service()
        app.state.similarity_error = None
    except (FileNotFoundError, ValueError, OSError) as exc:
        app.state.similarity_service = None
        app.state.similarity_error = str(exc)
    try:
        app.state.gis_service = build_gis_service()
        app.state.gis_error = None
    except (FileNotFoundError, ValueError, OSError) as exc:
        app.state.gis_service = None
        app.state.gis_error = str(exc)
    app.state.priority_service = build_priority_service()
    if getattr(app.state, "document_service", None) is None:
        app.state.document_service = build_document_service()
    try:
        app.state.project_service = build_project_service()
        app.state.project_error = None
    except (FileNotFoundError, ValueError, OSError) as exc:
        app.state.project_service = None
        app.state.project_error = str(exc)
    yield


app = FastAPI(title="PAIMANA Project Intelligence API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(prediction_router, prefix="/api/v1")
app.include_router(similarity_router, prefix="/api/v1")
app.include_router(intelligence_router, prefix="/api/v1")
app.include_router(priority_router, prefix="/api/v1")
app.include_router(gis_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health() -> dict[str, str | None]:
    model_available = getattr(app.state, "prediction_service", None) is not None
    gis_available = getattr(app.state, "gis_service", None) is not None
    return {
        "status": "ok" if model_available and gis_available else "degraded",
        "service": "project-intelligence",
        "model_status": "loaded" if model_available else "unavailable",
        "model_error": getattr(app.state, "model_error", None),
        "similarity_status": "loaded" if getattr(app.state, "similarity_service", None) is not None else "unavailable",
        "similarity_error": getattr(app.state, "similarity_error", None),
        "gis_status": "loaded" if gis_available else "unavailable",
        "gis_error": getattr(app.state, "gis_error", None),
    }
