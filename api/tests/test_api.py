import time

from botocore.exceptions import ClientError

from app import config, db


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["X-Served-By"] == config.HOSTNAME
    assert r.json()["served_by"] == config.HOSTNAME


def test_health_fails_without_tables(client):
    db.client().delete_table(TableName=config.SESSIONS_TABLE)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "unhealthy"


def test_items_crud(client):
    r = client.post("/items", json={"name": "first", "description": "hello"})
    assert r.status_code == 201
    item = r.json()["item"]
    assert item["version"] == 1

    assert client.get(f"/items/{item['id']}").json()["item"]["name"] == "first"

    r = client.put(f"/items/{item['id']}", json={"name": "renamed"})
    assert r.json()["item"]["name"] == "renamed"
    assert r.json()["item"]["version"] == 2

    listed = client.get("/items").json()
    assert [i["id"] for i in listed["items"]] == [item["id"]]

    assert client.delete(f"/items/{item['id']}").status_code == 200
    assert client.get(f"/items/{item['id']}").status_code == 404


def test_missing_item_is_404(client):
    assert client.put("/items/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/items/nope").status_code == 404


def test_invalid_item_is_422(client):
    assert client.post("/items", json={"name": ""}).status_code == 422


def test_visits_counter_is_hidden_from_items(client):
    for expected in (1, 2, 3):
        assert client.post("/visits").json()["visits"] == expected
    assert client.get("/visits").json()["visits"] == 3
    assert client.get("/items").json()["count"] == 0
    assert client.get("/items/counter%23visits").status_code == 404


def test_session_persists_between_requests(client):
    first = client.get("/session").json()
    assert first["new"] is True
    second = client.get("/session").json()
    assert second["new"] is False
    assert second["session"]["session_id"] == first["session"]["session_id"]
    assert second["session"]["views"] == 2


def test_expired_session_is_replaced(client):
    old = client.get("/session").json()["session"]["session_id"]
    db.client().update_item(
        TableName=config.SESSIONS_TABLE,
        Key={"session_id": {"S": old}},
        UpdateExpression="SET expires_at = :past",
        ExpressionAttributeValues={":past": {"N": str(int(time.time()) - 1)}},
    )
    r = client.get("/session").json()
    assert r["new"] is True
    assert r["session"]["session_id"] != old


def test_throttling_returns_503_with_retry_after(client, monkeypatch):
    def throttled(operation, **kwargs):
        error = {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}}
        raise ClientError(error, operation)

    monkeypatch.setattr(db, "call", throttled)
    r = client.post("/visits")
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "1"


def test_work_burns_cpu(client):
    r = client.get("/work?ms=50")
    assert r.status_code == 200
    assert r.json()["iterations"] > 0
    assert client.get("/work?ms=999999").status_code == 422


def test_metrics_exposed(client):
    client.get("/health")
    body = client.get("/metrics").text
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "http_requests_in_progress",
        "dynamodb_request_seconds",
    ):
        assert name in body
    assert 'path="/health"' in body
