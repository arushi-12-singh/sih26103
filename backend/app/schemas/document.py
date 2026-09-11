from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    document_id: str = Field(description="Unique identifier for the document")
    project_id: str = Field(description="Associated project ID")
    filename: str = Field(description="Original uploaded file name")
    file_type: str = Field(description="MIME type or extension of the file")
    file_size: int = Field(description="File size in bytes")
    uploaded_at: str = Field(description="ISO 8601 upload timestamp")
    uploaded_by: str = Field(default="Ananya Sharma", description="Uploader user name or persona")
    status: str = Field(default="uploaded", description="Current status of the document")
    stored_filename: str = Field(description="Internal storage key/filename on disk")
    category: Optional[str] = Field(default="Other / Supporting Document", description="Optional classification category")
    description: Optional[str] = Field(default=None, description="Optional document notes or description")


class DocumentUploadResponse(BaseModel):
    document_id: str
    project_id: str
    filename: str
    file_type: str
    file_size: int
    uploaded_at: str
    uploaded_by: str
    status: str = "uploaded"
    message: Optional[str] = Field(default="Document uploaded successfully")


class DocumentUpdateRequest(BaseModel):
    category: Optional[str] = None
    description: Optional[str] = None


class DocumentListResponse(BaseModel):
    project_id: str
    total_count: int
    total_size_bytes: int
    documents: list[DocumentMetadata]
