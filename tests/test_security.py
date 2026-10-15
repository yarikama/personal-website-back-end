import json

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from api.routes import csp
from main import get_application

client = TestClient(get_application())


def reports(directive: str) -> float:
    return REGISTRY.get_sample_value("csp_reports_total", {"directive": directive}) or 0


def test_every_response_carries_the_security_headers():
    for response in (client.get("/health"), client.get("/api/v1/nope")):
        assert response.headers["strict-transport-security"].startswith("max-age=")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_api_responses_may_load_nothing():
    api = client.get("/api/v1/nope")
    assert api.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    # The interactive docs load Swagger UI from a CDN.
    assert "content-security-policy" not in client.get("/docs").headers


def test_a_report_uri_violation_is_counted():
    before = reports("script-src-elem")
    body = {
        "csp-report": {
            "document-uri": "https://www.yarikama.com/notes/x",
            "violated-directive": "script-src-elem",
            "effective-directive": "script-src-elem",
            "blocked-uri": "inline",
            "disposition": "report",
        }
    }
    response = client.post(
        "/api/v1/csp-report",
        content=json.dumps(body),
        headers={"Content-Type": "application/csp-report"},
    )
    assert response.status_code == 204
    assert reports("script-src-elem") == before + 1


def test_reporting_api_batches_count_only_csp_violations():
    before = reports("img-src")
    body = [
        {"type": "csp-violation", "body": {"effectiveDirective": "img-src"}},
        {"type": "csp-violation", "body": {"effectiveDirective": "img-src"}},
        {"type": "deprecation", "body": {"id": "x"}},
    ]
    response = client.post(
        "/api/v1/csp-report",
        content=json.dumps(body),
        headers={"Content-Type": "application/reports+json"},
    )
    assert response.status_code == 204
    assert reports("img-src") == before + 2


def test_oversized_or_malformed_reports_are_refused():
    big = client.post(
        "/api/v1/csp-report", content=b"[" + b" " * (csp.MAX_BODY_BYTES + 1) + b"]"
    )
    assert big.status_code == 413
    assert client.post("/api/v1/csp-report", content=b"not json").status_code == 400
