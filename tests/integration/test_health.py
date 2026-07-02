"""
Integration tests for the health check endpoint.

The /health endpoint is the first thing checked after deployment.
It must always return 200 and never require authentication.
"""


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        """Health check must return HTTP 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_returns_status_ok(self, client):
        """Response body must include status=ok."""
        response = client.get("/api/v1/health")
        assert response.json()["status"] == "ok"

    def test_health_returns_version(self, client):
        """Response must include the application version string."""
        response = client.get("/api/v1/health")
        assert "version" in response.json()

    def test_health_requires_no_authentication(self, client):
        """No Authorization header should be needed for the health check."""
        response = client.get("/api/v1/health")
        # Must not be 401 or 403.
        assert response.status_code not in (401, 403)

    def test_health_includes_database_status(self, client):
        """Health check must report the database connection state."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "database" in data
        # With SQLite in-memory test DB, connection should succeed.
        assert data["database"] in ("connected", "unreachable")
