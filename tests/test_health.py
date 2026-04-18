from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoints():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("status") in {"ok", "degraded"}

    for path in ["/ready", "/live"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
