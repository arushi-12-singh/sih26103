from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from app.schemas.document import (
    DocumentListResponse,
    DocumentMetadata,
    DocumentUpdateRequest,
    DocumentUploadResponse,
)
from app.services.document_service import DocumentService, build_document_service

router = APIRouter(prefix="/projects", tags=["Project Documents"])


def get_document_service(request: Request) -> DocumentService:
    service: DocumentService | None = getattr(request.app.state, "document_service", None)
    if service is None:
        service = build_document_service()
        request.app.state.document_service = service
    return service


@router.post(
    "/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a project document",
    description=(
        "Uploads a document associated with a specific project. "
        "Validates file type (PDF, DOC, DOCX, XLS, XLSX, CSV, JPG, JPEG, PNG), "
        "enforces size limits, and sanitizes filenames."
    ),
)
async def upload_document(
    project_id: str,
    request: Request,
    file: UploadFile = File(..., description="The document file to upload"),
    category: str = Form(default="Other / Supporting Document", description="Optional classification category"),
    description: Optional[str] = Form(default=None, description="Optional notes or description"),
    uploader: Optional[str] = Form(default="Ananya Sharma", description="Uploader persona / username"),
) -> DocumentUploadResponse:
    service = get_document_service(request)
    return await service.save_document(
        project_id=project_id,
        file=file,
        category=category,
        description=description,
        uploader=uploader or "Ananya Sharma",
    )


@router.get(
    "/{project_id}/documents",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all documents for a project",
    description="Returns metadata for all documents cataloged for the given project ID.",
)
def list_documents(project_id: str, request: Request) -> DocumentListResponse:
    service = get_document_service(request)
    return service.list_documents(project_id=project_id)


@router.get(
    "/{project_id}/documents/{document_id}/download",
    summary="Download or view a project document",
    description="Streams the specified project document file.",
)
def download_document(
    project_id: str,
    document_id: str,
    request: Request,
) -> FileResponse:
    service = get_document_service(request)
    file_path, meta = service.get_document_file(project_id, document_id)

    # Encode filename for Content-Disposition header
    encoded_name = quote(meta.filename)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
    }

    return FileResponse(
        path=str(file_path),
        media_type=meta.file_type,
        filename=meta.filename,
        headers=headers,
    )


@router.delete(
    "/{project_id}/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a project document",
    description="Deletes the document file and removes its metadata from the project repository.",
)
def delete_document(
    project_id: str,
    document_id: str,
    request: Request,
) -> dict[str, str]:
    service = get_document_service(request)
    return service.delete_document(project_id=project_id, document_id=document_id)


@router.patch(
    "/{project_id}/documents/{document_id}",
    response_model=DocumentMetadata,
    status_code=status.HTTP_200_OK,
    summary="Update project document metadata",
    description="Updates the category and description of a cataloged project document.",
)
def update_document(
    project_id: str,
    document_id: str,
    payload: DocumentUpdateRequest,
    request: Request,
) -> DocumentMetadata:
    service = get_document_service(request)
    return service.update_document(
        project_id=project_id,
        document_id=document_id,
        category=payload.category,
        description=payload.description,
    )
