from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoints():
    for path in ["/health", "/ready", "/live"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
