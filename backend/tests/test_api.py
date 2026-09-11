"""
Integration tests for FastAPI endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import REFERENCE_DOCS_DIR


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_generate_endpoint_requires_file(client):
    response = client.post("/api/generate")
    # Missing required form-data file
    assert response.status_code == 422


def test_generate_endpoint_with_sample_pdf(client):
    case_pdf = REFERENCE_DOCS_DIR / "03_Case_Information.pdf"
    if not case_pdf.exists():
        pytest.skip("Reference PDF not found")

    with open(case_pdf, "rb") as f:
        response = client.post(
            "/api/generate",
            files={"case_info": ("03_Case_Information.pdf", f, "application/pdf")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "entities" in data
    assert "evaluation" in data
    assert data["evaluation"]["overall_score"] >= 80.0
    assert "generated_03_Case_Information.docx" in data["files"]["affidavit"]


def test_download_endpoints(client):
    # Test docx download
    response_docx = client.get("/api/download/affidavit")
    assert response_docx.status_code in (200, 404)

    # Test report download
    response_report = client.get("/api/download/report?format=json")
    assert response_report.status_code in (200, 404)
