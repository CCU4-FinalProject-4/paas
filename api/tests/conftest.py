import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app import config, db
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(config, "AWS_ENDPOINT_URL", None)
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=config.AWS_REGION)
        for table, key in [(config.ITEMS_TABLE, "id"), (config.SESSIONS_TABLE, "session_id")]:
            ddb.create_table(
                TableName=table,
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                BillingMode="PROVISIONED",
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            )
        db.client.cache_clear()
        yield TestClient(app)
        db.client.cache_clear()
