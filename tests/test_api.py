"""Smoke test for the Investigator Workspace API. Case changes go to a temporary file, never data/cases.json."""
import pytest
from fastapi.testclient import TestClient

from app import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CASES_PATH", str(tmp_path / "cases.json"))
    monkeypatch.setattr(server, "CASES", {})
    return TestClient(server.app)


def test_home_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "Fraudbusters" in r.text


def test_alert_list_and_detail(client):
    alerts = client.get("/api/alerts").json()
    assert len(alerts) > 0
    first = alerts[0] if isinstance(alerts, list) else alerts["alerts"][0]
    aid = first["alert_id"] if "alert_id" in first else first["id"]
    d = client.get(f"/api/alerts/{aid}").json()
    assert d["nodes"] and d["transactions"]


def test_case_update_is_saved_with_audit_log(client):
    r = client.post("/api/cases/AL0001", json={"status": "Escalated", "note": "test note", "by": "Ini"})
    assert r.status_code == 200
    c = server.CASES["AL0001"]
    assert c["status"] == "Escalated"
    assert c["log"], "every change must be written to the audit log"


def test_str_pdf_downloads(client):
    r = client.get("/api/alerts/AL0001/str.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
