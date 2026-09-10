from __future__ import annotations

import os
from pathlib import Path

# Maximum document size in Megabytes (configurable via environment variable)
MAX_DOCUMENT_SIZE_MB: int = int(os.getenv("MAX_DOCUMENT_SIZE_MB", "20"))
MAX_DOCUMENT_SIZE_BYTES: int = MAX_DOCUMENT_SIZE_MB * 1024 * 1024

# Base storage directory for project uploads
DEFAULT_UPLOAD_DIR: Path = Path(
    os.getenv("UPLOAD_DIR", Path(__file__).resolve().parents[2] / "uploads" / "projects")
)

# Supported file extensions
ALLOWED_EXTENSIONS: set[str] = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".jpg",
    ".jpeg",
    ".png",
}

# Standard MIME type mappings
MIME_TYPE_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
