def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_version_ok(client):
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"service", "version", "environment"}


def test_correlation_id_header(client):
    response = client.get("/health")
    assert "X-Correlation-ID" in response.headers


def test_correlation_id_propagated(client):
    response = client.get("/health", headers={"X-Correlation-ID": "test-correlation-123"})
    assert response.headers["X-Correlation-ID"] == "test-correlation-123"
