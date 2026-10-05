import json

from starlette.testclient import TestClient

from app.core.middleware import RequestLoggingMiddleware


def test_json_post_body_is_replayed_and_sensitive_values_are_redacted(caplog):
    async def endpoint(scope, receive, send):
        request = await receive()
        payload = request["body"]
        while request.get("more_body", False):
            request = await receive()
            payload += request["body"]

        response_body = json.dumps({"received": json.loads(payload)}).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": response_body})

    client = TestClient(RequestLoggingMiddleware(endpoint))
    response = client.post(
        "/login",
        headers={"X-Request-ID": "test-request-id"},
        json={"username": "admin", "password": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "received": {"username": "admin", "password": "secret"},
    }
    assert response.headers["X-Request-ID"] == "test-request-id"
    logged_bodies = [
        record.request_body
        for record in caplog.records
        if hasattr(record, "request_body")
    ]
    assert logged_bodies[0]["password"] == "***"
    assert "secret" not in repr(logged_bodies)
