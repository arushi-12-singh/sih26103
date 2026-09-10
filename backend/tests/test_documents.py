from __future__ import annotations

import io
from pathlib import Path
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.document_service import DocumentService


@pytest.fixture(autouse=True)
def fresh_document_service():
    """Ensure every test runs in its own clean temporary uploads directory."""
    temp_dir = Path(tempfile.mkdtemp())
    test_doc_service = DocumentService(
        base_dir=temp_dir,
        extra_valid_projects={"TEST-PROJ-01", "TEST-PROJ-02", "TEST-PROJ-03", "EFC-04"},
    )
    app.state.document_service = test_doc_service

    yield test_doc_service

    # Cleanup temp directory after test
    shutil.rmtree(temp_dir, ignore_errors=True)
    app.state.document_service = None


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_valid_pdf_upload(client: TestClient):
    project_id = "TEST-PROJ-01"
    content = b"%PDF-1.5 Detailed Project Report"
    files = {"file": ("dpr_report.pdf", io.BytesIO(content), "application/pdf")}
    data = {"category": "Detailed Project Report (DPR)", "uploader": "Ananya Sharma"}

    response = client.post(f"/api/v1/projects/{project_id}/documents", files=files, data=data)
    assert response.status_code == 201
    res = response.json()
    assert res["project_id"] == project_id
    assert res["filename"] == "dpr_report.pdf"
    assert res["file_type"] == "application/pdf"
    assert res["file_size"] == len(content)
    assert res["status"] == "uploaded"
    assert "document_id" in res


def test_valid_docx_upload(client: TestClient):
    project_id = "TEST-PROJ-01"
    content = b"PK\x03\x04\x14\x00\x06\x00DOCX Mock Content"
    files = {"file": ("contract_agreement.docx", io.BytesIO(content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}

    response = client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert response.status_code == 201
    res = response.json()
    assert res["filename"] == "contract_agreement.docx"
    assert res["file_size"] == len(content)


def test_valid_xlsx_upload(client: TestClient):
    project_id = "TEST-PROJ-01"
    content = b"PK\x03\x04\x14\x00\x06\x00XLSX Cost Breakdown"
    files = {"file": ("expenditure_q3.xlsx", io.BytesIO(content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    response = client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert response.status_code == 201
    res = response.json()
    assert res["filename"] == "expenditure_q3.xlsx"


def test_valid_image_uploads(client: TestClient):
    project_id = "TEST-PROJ-01"
    
    # 1. PNG
    png_content = b"\x89PNG\r\n\x1a\nMock PNG Data"
    files = {"file": ("site_aerial_survey.png", io.BytesIO(png_content), "image/png")}
    res_png = client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert res_png.status_code == 201
    assert res_png.json()["filename"] == "site_aerial_survey.png"

    # 2. JPG
    jpg_content = b"\xff\xd8\xff\xe0Mock JPEG Data"
    files_jpg = {"file": ("geotech_sample.jpg", io.BytesIO(jpg_content), "image/jpeg")}
    res_jpg = client.post(f"/api/v1/projects/{project_id}/documents", files=files_jpg)
    assert res_jpg.status_code == 201
    assert res_jpg.json()["filename"] == "geotech_sample.jpg"


def test_multiple_document_uploads_and_listing(client: TestClient):
    project_id = "TEST-PROJ-01"
    
    # Upload 3 distinct files
    doc1 = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("doc1.pdf", io.BytesIO(b"PDF 1"), "application/pdf")},
    ).json()
    doc2 = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("doc2.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
    ).json()
    doc3 = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("doc3.png", io.BytesIO(b"PNG Data"), "image/png")},
    ).json()

    list_res = client.get(f"/api/v1/projects/{project_id}/documents")
    assert list_res.status_code == 200
    list_json = list_res.json()
    assert list_json["total_count"] == 3
    doc_ids = [d["document_id"] for d in list_json["documents"]]
    assert doc1["document_id"] in doc_ids
    assert doc2["document_id"] in doc_ids
    assert doc3["document_id"] in doc_ids


def test_duplicate_filename_handling(client: TestClient):
    project_id = "TEST-PROJ-01"
    
    # Upload first version
    res1 = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("environmental_clearance.pdf", io.BytesIO(b"Version 1"), "application/pdf")},
    )
    assert res1.status_code == 201
    id1 = res1.json()["document_id"]

    # Upload second version with identical original filename
    res2 = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("environmental_clearance.pdf", io.BytesIO(b"Version 2 updated"), "application/pdf")},
    )
    assert res2.status_code == 201
    id2 = res2.json()["document_id"]

    assert id1 != id2

    # Both documents must be listed
    list_res = client.get(f"/api/v1/projects/{project_id}/documents")
    assert list_res.status_code == 200
    assert list_res.json()["total_count"] == 2


def test_invalid_file_extension_rejected(client: TestClient):
    project_id = "TEST-PROJ-01"
    
    # .exe
    res_exe = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("malware.exe", io.BytesIO(b"MZ executable"), "application/x-msdownload")},
    )
    assert res_exe.status_code == 400
    assert "not supported" in res_exe.json()["detail"]

    # .sh
    res_sh = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("deploy.sh", io.BytesIO(b"#!/bin/bash"), "application/x-sh")},
    )
    assert res_sh.status_code == 400

    # .py
    res_py = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("script.py", io.BytesIO(b"import os"), "text/x-python")},
    )
    assert res_py.status_code == 400


def test_oversized_file_rejected(client: TestClient, fresh_document_service: DocumentService):
    project_id = "TEST-PROJ-01"
    
    # Configure 100KB limit on the test document service
    fresh_document_service.max_size_bytes = 100 * 1024

    oversized_content = b"0" * (150 * 1024)  # 150KB
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("large_scan.pdf", io.BytesIO(oversized_content), "application/pdf")},
    )
    assert response.status_code == 413
    assert "exceeds maximum" in response.json()["detail"].lower()


def test_invalid_or_nonexistent_project(client: TestClient):
    content = b"%PDF-1.4 Mock DPR"
    files = {"file": ("dpr.pdf", io.BytesIO(content), "application/pdf")}

    # 1. Non-existent project
    res_unknown = client.post("/api/v1/projects/NON-EXISTENT-PROJECT-9999/documents", files=files)
    assert res_unknown.status_code == 404
    assert "does not exist" in res_unknown.json()["detail"]

    # 2. Path traversal in project ID
    res_bad = client.post("/api/v1/projects/..%2F..%2Fevil/documents", files=files)
    assert res_bad.status_code in (400, 404)


def test_filename_sanitization_and_path_traversal(client: TestClient):
    project_id = "TEST-PROJ-01"
    content = b"%PDF-1.4 Traversing File Content"
    
    # Path traversal in filename header
    files = {"file": ("../../../../etc/passwd.pdf", io.BytesIO(content), "application/pdf")}
    res = client.post(f"/api/v1/projects/{project_id}/documents", files=files)
    assert res.status_code == 201
    stored_name = res.json()["filename"]
    assert ".." not in stored_name
    assert "/" not in stored_name
    assert "\\" not in stored_name
    assert stored_name.endswith(".pdf")


def test_document_deletion(client: TestClient):
    project_id = "TEST-PROJ-01"
    files = {"file": ("clearance_to_delete.pdf", io.BytesIO(b"Delete me"), "application/pdf")}
    
    # 1. Upload
    up_res = client.post(f"/api/v1/projects/{project_id}/documents", files=files).json()
    doc_id = up_res["document_id"]

    # 2. Delete
    del_res = client.delete(f"/api/v1/projects/{project_id}/documents/{doc_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

    # 3. Verify gone from listing
    list_res = client.get(f"/api/v1/projects/{project_id}/documents").json()
    assert list_res["total_count"] == 0

    # 4. Deleting again returns 404
    del_again = client.delete(f"/api/v1/projects/{project_id}/documents/{doc_id}")
    assert del_again.status_code == 404


def test_cross_project_isolation(client: TestClient):
    # Upload to project 1
    p1_doc = client.post(
        "/api/v1/projects/TEST-PROJ-01/documents",
        files={"file": ("project1_secret.pdf", io.BytesIO(b"P1 Secret"), "application/pdf")},
    ).json()
    doc_id = p1_doc["document_id"]

    # Attempt to access / download P1's doc from project 2
    cross_res = client.get(f"/api/v1/projects/TEST-PROJ-02/documents/{doc_id}/download")
    assert cross_res.status_code == 404

    # Attempt to delete P1's doc via project 2 endpoint
    cross_del = client.delete(f"/api/v1/projects/TEST-PROJ-02/documents/{doc_id}")
    assert cross_del.status_code == 404
