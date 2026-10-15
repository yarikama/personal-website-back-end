from fastapi.testclient import TestClient

from main import get_application


def test_preflight_is_cached_for_two_hours():
    client = TestClient(get_application())

    response = client.options(
        "/api/v1/admin/complete",
        headers={
            "Origin": "https://www.yarikama.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://www.yarikama.com"
    assert response.headers["access-control-max-age"] == "7200"


def test_preflight_from_an_unknown_origin_is_refused():
    client = TestClient(get_application())

    response = client.options(
        "/api/v1/admin/complete",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers
