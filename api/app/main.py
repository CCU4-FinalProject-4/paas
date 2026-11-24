import time
import uuid
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from fastapi import Cookie, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import config, db
from .metrics import IN_PROGRESS, LATENCY, REQUESTS

VISITS_COUNTER_ID = "counter#visits"
SESSION_COOKIE = "session_id"

app = FastAPI(title="paas-api", version=config.APP_VERSION)


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


def reply(**data):
    return {**data, "served_by": config.HOSTNAME}


def now_iso():
    return datetime.now(UTC).isoformat()


@app.middleware("http")
async def observe(request: Request, call_next):
    IN_PROGRESS.inc()
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Served-By"] = config.HOSTNAME
        return response
    finally:
        IN_PROGRESS.dec()
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        REQUESTS.labels(request.method, path, str(status)).inc()


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(reply(detail=exc.detail), status_code=exc.status_code)


@app.exception_handler(ClientError)
async def dynamodb_error(request: Request, exc: ClientError):
    # Throttling is temporary capacity pressure, not a bug: tell the client to back off.
    if db.error_code(exc) in db.THROTTLING_ERRORS:
        return JSONResponse(
            reply(detail="database is busy, retry later"),
            status_code=503,
            headers={"Retry-After": "1"},
        )
    return JSONResponse(reply(detail="database error"), status_code=500)


@app.get("/")
def root():
    return reply(service="paas-api", version=config.APP_VERSION)


@app.get("/health")
def health():
    try:
        db.call("describe_table", TableName=config.ITEMS_TABLE)
        db.call("describe_table", TableName=config.SESSIONS_TABLE)
    except Exception as e:
        return JSONResponse(
            reply(status="unhealthy", error=type(e).__name__, version=config.APP_VERSION),
            status_code=503,
        )
    return reply(status="ok", version=config.APP_VERSION)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/work")
def work(ms: int = Query(default=100, ge=0, le=10_000)):
    deadline = time.perf_counter() + ms / 1000
    n = 0
    while time.perf_counter() < deadline:
        n += 1
    return reply(ms=ms, iterations=n)


@app.post("/visits")
def add_visit():
    res = db.call(
        "update_item",
        TableName=config.ITEMS_TABLE,
        Key=db.to_dynamo({"id": VISITS_COUNTER_ID}),
        UpdateExpression="ADD #count :one",
        ExpressionAttributeNames={"#count": "count"},
        ExpressionAttributeValues=db.to_dynamo({":one": 1}),
        ReturnValues="UPDATED_NEW",
    )
    return reply(visits=db.from_dynamo(res["Attributes"])["count"])


@app.get("/visits")
def get_visits():
    res = db.call(
        "get_item",
        TableName=config.ITEMS_TABLE,
        Key=db.to_dynamo({"id": VISITS_COUNTER_ID}),
        ConsistentRead=True,
    )
    return reply(visits=db.from_dynamo(res.get("Item", {})).get("count", 0))


@app.get("/session")
def session(response: Response, session_id: str | None = Cookie(default=None)):
    now = int(time.time())
    if session_id:
        try:
            res = db.call(
                "update_item",
                TableName=config.SESSIONS_TABLE,
                Key=db.to_dynamo({"session_id": session_id}),
                UpdateExpression="ADD #views :one SET last_served_by = :host",
                # TTL deletion is lazy, so an expired session can still exist for a while.
                ConditionExpression="attribute_exists(session_id) AND expires_at > :now",
                ExpressionAttributeNames={"#views": "views"},
                ExpressionAttributeValues=db.to_dynamo(
                    {":one": 1, ":host": config.HOSTNAME, ":now": now}
                ),
                ReturnValues="ALL_NEW",
            )
            return reply(session=db.from_dynamo(res["Attributes"]), new=False)
        except ClientError as e:
            if db.error_code(e) != "ConditionalCheckFailedException":
                raise

    item = {
        "session_id": uuid.uuid4().hex,
        "views": 1,
        "created_by": config.HOSTNAME,
        "last_served_by": config.HOSTNAME,
        "expires_at": now + config.SESSION_TTL_SECONDS,
    }
    db.call("put_item", TableName=config.SESSIONS_TABLE, Item=db.to_dynamo(item))
    response.set_cookie(
        SESSION_COOKIE,
        item["session_id"],
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return reply(session=item, new=True)


@app.get("/items")
def list_items():
    # shortcut: single scan page of up to 100 items, paginate if the table grows.
    res = db.call(
        "scan",
        TableName=config.ITEMS_TABLE,
        Limit=100,
        FilterExpression="id <> :counter",
        ExpressionAttributeValues=db.to_dynamo({":counter": VISITS_COUNTER_ID}),
    )
    items = sorted((db.from_dynamo(i) for i in res["Items"]), key=lambda i: i["created_at"])
    return reply(items=items, count=len(items))


@app.post("/items", status_code=201)
def create_item(body: ItemIn):
    ts = now_iso()
    item = {
        "id": uuid.uuid4().hex,
        "name": body.name,
        "description": body.description,
        "version": 1,
        "created_at": ts,
        "updated_at": ts,
        "created_by": config.HOSTNAME,
    }
    db.call(
        "put_item",
        TableName=config.ITEMS_TABLE,
        Item=db.to_dynamo(item),
        ConditionExpression="attribute_not_exists(id)",
    )
    return reply(item=item)


@app.get("/items/{item_id}")
def get_item(item_id: str):
    if item_id == VISITS_COUNTER_ID:
        raise HTTPException(404, "item not found")
    res = db.call("get_item", TableName=config.ITEMS_TABLE, Key=db.to_dynamo({"id": item_id}))
    if "Item" not in res:
        raise HTTPException(404, "item not found")
    return reply(item=db.from_dynamo(res["Item"]))


@app.put("/items/{item_id}")
def update_item(item_id: str, body: ItemIn):
    if item_id == VISITS_COUNTER_ID:
        raise HTTPException(404, "item not found")
    try:
        res = db.call(
            "update_item",
            TableName=config.ITEMS_TABLE,
            Key=db.to_dynamo({"id": item_id}),
            UpdateExpression="SET #name = :name, #desc = :desc, updated_at = :ts ADD #version :one",
            ConditionExpression="attribute_exists(id)",
            ExpressionAttributeNames={
                "#name": "name",
                "#desc": "description",
                "#version": "version",
            },
            ExpressionAttributeValues=db.to_dynamo(
                {":name": body.name, ":desc": body.description, ":ts": now_iso(), ":one": 1}
            ),
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if db.error_code(e) == "ConditionalCheckFailedException":
            raise HTTPException(404, "item not found") from e
        raise
    return reply(item=db.from_dynamo(res["Attributes"]))


@app.delete("/items/{item_id}")
def delete_item(item_id: str):
    if item_id == VISITS_COUNTER_ID:
        raise HTTPException(404, "item not found")
    try:
        db.call(
            "delete_item",
            TableName=config.ITEMS_TABLE,
            Key=db.to_dynamo({"id": item_id}),
            ConditionExpression="attribute_exists(id)",
        )
    except ClientError as e:
        if db.error_code(e) == "ConditionalCheckFailedException":
            raise HTTPException(404, "item not found") from e
        raise
    return reply(deleted=item_id)
