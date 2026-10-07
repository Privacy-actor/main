import io
import json
import zipfile
from pathlib import PurePosixPath, PureWindowsPath

import pytest
from fastapi.testclient import TestClient

from app.main import app, _safe_export_name


client = TestClient(app)


@pytest.mark.parametrize("endpoint", ["/api/v1/extract", "/api/v1/jobs"])
@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_corrupt_documents_return_actionable_client_error(endpoint, extension):
    response = client.post(endpoint, files={"files": (f"broken.{extension}", b"not a valid document")})
    assert response.status_code == 400
    assert extension.upper() in response.json()["detail"]
    assert "broken." + extension in response.json()["detail"]


def test_password_protected_pdf_returns_client_error():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("synthetic-test-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    response = client.post("/api/v1/extract", files={"files": ("locked.pdf", buffer.getvalue())})
    assert response.status_code == 400
    assert "密码" in response.json()["detail"]


@pytest.mark.parametrize("config", ["[]", "null", "true", "42", '"mask"'])
def test_batch_rejects_non_object_configuration(config):
    response = client.post(
        "/api/v1/jobs", files={"files": ("sample.txt", b"Call 13800138000")},
        data={"config_json": config},
    )
    assert response.status_code == 422
    assert "JSON 对象" in response.json()["detail"]


@pytest.mark.parametrize("filename", [
    "/tmp/report.txt", "../../report.txt", r"C:\Users\report.txt", "C:report.txt",
    r"\\server\share\report.txt", "//server/share/report.txt", "folder/../report.txt",
    "folder/.. /report.txt", "///", "../", 'folder/a:b?.txt',
])
def test_export_paths_are_relative_on_both_posix_and_windows(filename):
    name = _safe_export_name(filename)
    assert not PurePosixPath(name).is_absolute()
    assert not PureWindowsPath(name).drive
    assert not PureWindowsPath(name).root
    assert ".." not in PurePosixPath(name).parts
    assert "\\" not in name and ":" not in name
    assert name.endswith(".redacted.txt")


def test_zip_preserves_relative_folders_and_handles_sanitized_name_collisions():
    response = client.post(
        "/api/v1/jobs",
        files=[("files", ("/reports/sample.txt", b"first")), ("files", ("reports/sample.txt", b"second"))],
        data={"config_json": '{"use_llm":false}'},
    )
    assert response.status_code == 200
    download = client.get(f"/api/v1/jobs/{response.json()['id']}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert "reports/sample.redacted.txt" in names
        assert "reports/sample.redacted-2.txt" in names
        assert len(names) == len(set(names))
        assert archive.read("reports/sample.redacted.txt") == b"first"
        assert archive.read("reports/sample.redacted-2.txt") == b"second"


def test_batch_review_counts_and_manifest_follow_latest_task():
    created = client.post(
        "/api/v1/jobs", files={"files": ("sample.txt", b"Alpha 13800138000")},
        data={"config_json": '{"use_llm":false}'},
    )
    assert created.status_code == 200
    job_url = f"/api/v1/jobs/{created.json()['id']}"
    original = client.get(job_url).json()["payload"]["results"][0]
    task_id = original["task_id"]
    task = client.get(f"/api/v1/tasks/{task_id}").json()
    phone = next(span for span in task["spans"] if span["entity_type"] == "PHONE")
    added = client.post("/api/v1/reviews", json={
        "task_id": task_id, "span_id": "manual_pending", "operation": "add",
        "span": {"id": "manual_pending", "start": 0, "end": 5, "text": "Alpha",
                 "entity_type": "CUSTOM", "sources": ["MANUAL"], "status": "pending", "conflict": True},
    })
    assert added.status_code == 200
    pending = client.get(job_url).json()["payload"]["results"][0]
    assert pending["entity_count"] == original["entity_count"] + 1
    assert pending["pending_count"] == 1
    assert pending["status"] == "needs_review"
    for span_id, operation in [("manual_pending", "accept"), (phone["id"], "reject")]:
        reviewed = client.post("/api/v1/reviews", json={"task_id": task_id, "span_id": span_id, "operation": operation})
        assert reviewed.status_code == 200
    latest = client.get(job_url).json()["payload"]["results"][0]
    assert latest["entity_count"] == 1
    assert latest["pending_count"] == 0
    assert latest["status"] == "completed"
    download = client.get(job_url + "/download")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["payload"]["results"][0] == latest
