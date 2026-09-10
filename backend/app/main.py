from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.prediction import router as prediction_router
from app.api.routes.similarity import router as similarity_router
from app.services.prediction_service import build_prediction_service
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
    yield


app = FastAPI(title="PAIMANA Project Intelligence API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(prediction_router, prefix="/api/v1")
app.include_router(similarity_router, prefix="/api/v1")
app.include_router(intelligence_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health() -> dict[str, str | None]:
    model_available = getattr(app.state, "prediction_service", None) is not None
    return {
        "status": "ok" if model_available else "degraded",
        "service": "project-intelligence",
        "model_status": "loaded" if model_available else "unavailable",
        "model_error": getattr(app.state, "model_error", None),
        "similarity_status": "loaded" if getattr(app.state, "similarity_service", None) is not None else "unavailable",
        "similarity_error": getattr(app.state, "similarity_error", None),
    }
