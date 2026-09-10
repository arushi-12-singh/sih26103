from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Optional, Set
import uuid

from fastapi import HTTPException, UploadFile, status

from app.config.document_config import (
    ALLOWED_EXTENSIONS,
    DEFAULT_UPLOAD_DIR,
    MAX_DOCUMENT_SIZE_BYTES,
    MAX_DOCUMENT_SIZE_MB,
    MIME_TYPE_MAP,
)
from app.schemas.document import (
    DocumentListResponse,
    DocumentMetadata,
    DocumentUploadResponse,
)
from app.services.project_validator import is_valid_project_format, validate_project_exists


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and remove dangerous characters."""
    # Strip any directory components
    clean = os.path.basename(filename.replace("\\", "/"))
    # Remove leading dots or null bytes
    clean = clean.replace("\x00", "").lstrip(".")
    # Replace non-alphanumeric (except . - _) with underscores
    clean = re.sub(r"[^\w\s\.-]", "_", clean)
    clean = re.sub(r"\s+", "_", clean).strip("._")
    return clean or "document"


class DocumentService:
    def __init__(
        self,
        base_dir: Path = DEFAULT_UPLOAD_DIR,
        extra_valid_projects: set[str] | None = None,
        max_size_bytes: int | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.extra_valid_projects = extra_valid_projects or set()
        self.max_size_bytes = max_size_bytes or MAX_DOCUMENT_SIZE_BYTES

    def _validate_project(self, project_id: str) -> str:
        """Validate project ID format and existence."""
        if not is_valid_project_format(project_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid project ID format: '{project_id}'",
            )
        clean_pid = project_id.strip()
        if not validate_project_exists(clean_pid, self.extra_valid_projects):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{clean_pid}' does not exist",
            )
        return clean_pid

    def _get_project_dir(self, project_id: str) -> Path:
        safe_pid = self._validate_project(project_id)
        project_dir = self.base_dir / safe_pid
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    def _get_manifest_path(self, project_dir: Path) -> Path:
        return project_dir / "manifest.json"

    def _read_manifest(self, project_dir: Path) -> list[dict]:
        manifest_path = self._get_manifest_path(project_dir)
        if not manifest_path.exists():
            return []
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_manifest(self, project_dir: Path, manifest: list[dict]) -> None:
        manifest_path = self._get_manifest_path(project_dir)
        temp_path = manifest_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        temp_path.replace(manifest_path)

    async def save_document(
        self,
        project_id: str,
        file: UploadFile,
        category: str = "Other / Supporting Document",
        description: Optional[str] = None,
        uploader: str = "Ananya Sharma",
    ) -> DocumentUploadResponse:
        # 1. Project validation
        clean_project_id = self._validate_project(project_id)

        # 2. Filename & extension validation
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must have a filename",
            )

        raw_filename = file.filename
        # Check path traversal in provided filename
        if ".." in raw_filename or "/" in raw_filename or "\\" in raw_filename:
            raw_filename = os.path.basename(raw_filename.replace("\\", "/"))

        ext = Path(raw_filename).suffix.lower()
        if not ext or ext not in ALLOWED_EXTENSIONS:
            allowed_list = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File extension '{ext}' is not supported. Allowed extensions: {allowed_list}",
            )

        project_dir = self._get_project_dir(clean_project_id)
        clean_name = sanitize_filename(raw_filename)
        document_id = f"DOC-{uuid.uuid4().hex[:10].upper()}"
        stored_filename = f"{document_id}_{clean_name}"
        target_path = project_dir / stored_filename

        # 3. Stream upload and check size limit
        file_size = 0
        chunk_size = 1024 * 1024  # 1MB chunk size
        is_oversized = False

        try:
            with open(target_path, "wb") as f_out:
                while chunk := await file.read(chunk_size):
                    file_size += len(chunk)
                    if file_size > self.max_size_bytes:
                        is_oversized = True
                        break
                    f_out.write(chunk)
        except Exception as exc:
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write uploaded file: {exc}",
            ) from exc
        finally:
            await file.close()

        if is_oversized:
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds maximum allowed limit of {self.max_size_bytes // (1024 * 1024) or 1} MB",
            )

        # 4. Determine MIME type
        mime_type = file.content_type or MIME_TYPE_MAP.get(ext, "application/octet-stream")
        uploaded_at = datetime.now(timezone.utc).isoformat()

        meta = DocumentMetadata(
            document_id=document_id,
            project_id=clean_project_id,
            filename=clean_name,
            file_type=mime_type,
            file_size=file_size,
            uploaded_at=uploaded_at,
            uploaded_by=uploader or "Ananya Sharma",
            status="uploaded",
            stored_filename=stored_filename,
            category=category,
            description=description.strip() if description else None,
        )

        manifest = self._read_manifest(project_dir)
        manifest.insert(0, meta.model_dump())
        self._write_manifest(project_dir, manifest)

        return DocumentUploadResponse(
            document_id=meta.document_id,
            project_id=meta.project_id,
            filename=meta.filename,
            file_type=meta.file_type,
            file_size=meta.file_size,
            uploaded_at=meta.uploaded_at,
            uploaded_by=meta.uploaded_by,
            status=meta.status,
            message="Document uploaded successfully",
        )

    def list_documents(self, project_id: str) -> DocumentListResponse:
        clean_project_id = self._validate_project(project_id)
        project_dir = self._get_project_dir(clean_project_id)
        raw_manifest = self._read_manifest(project_dir)

        valid_docs: list[DocumentMetadata] = []
        total_size = 0

        for item in raw_manifest:
            try:
                doc = DocumentMetadata(**item)
                # Verify file still exists on disk
                if (project_dir / doc.stored_filename).exists():
                    valid_docs.append(doc)
                    total_size += doc.file_size
            except Exception:
                continue

        return DocumentListResponse(
            project_id=clean_project_id,
            total_count=len(valid_docs),
            total_size_bytes=total_size,
            documents=valid_docs,
        )

    def get_document_file(self, project_id: str, document_id: str) -> tuple[Path, DocumentMetadata]:
        clean_project_id = self._validate_project(project_id)
        project_dir = self._get_project_dir(clean_project_id)
        manifest = self._read_manifest(project_dir)

        for item in manifest:
            if item.get("document_id") == document_id:
                meta = DocumentMetadata(**item)
                file_path = project_dir / meta.stored_filename
                if not file_path.exists():
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Document file not found on disk",
                    )
                return file_path, meta

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found for project '{clean_project_id}'",
        )

    def delete_document(self, project_id: str, document_id: str) -> dict[str, str]:
        clean_project_id = self._validate_project(project_id)
        project_dir = self._get_project_dir(clean_project_id)
        manifest = self._read_manifest(project_dir)

        target_meta = None
        new_manifest = []

        for item in manifest:
            if item.get("document_id") == document_id:
                target_meta = item
            else:
                new_manifest.append(item)

        if not target_meta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{document_id}' not found for project '{clean_project_id}'",
            )

        file_path = project_dir / target_meta.get("stored_filename", "")
        if file_path.exists():
            file_path.unlink(missing_ok=True)

        self._write_manifest(project_dir, new_manifest)
        return {"status": "success", "message": f"Document '{document_id}' deleted successfully"}


def build_document_service() -> DocumentService:
    return DocumentService()
