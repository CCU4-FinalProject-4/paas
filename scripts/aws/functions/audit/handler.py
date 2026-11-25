"""Audit trail for paas-items, triggered by DynamoDB Streams.

The API used to have no audit trail; instead of adding a synchronous write to every request,
the change is captured by the table stream and processed here, off the request path.
"""

import json

from boto3.dynamodb.types import TypeDeserializer

_deserialize = TypeDeserializer().deserialize
EVENT_NAMES = {"INSERT": "item.created", "MODIFY": "item.updated", "REMOVE": "item.deleted"}


def _plain(image: dict | None) -> dict:
    return {k: _deserialize(v) for k, v in (image or {}).items()}


def _changed(old: dict, new: dict) -> dict:
    keys = old.keys() | new.keys()
    return {k: {"from": old.get(k), "to": new.get(k)} for k in keys if old.get(k) != new.get(k)}


def handler(event, context):
    for record in event["Records"]:
        d = record["dynamodb"]
        old, new = _plain(d.get("OldImage")), _plain(d.get("NewImage"))
        entry = {
            "event": EVENT_NAMES[record["eventName"]],
            "item_id": (new or old).get("id"),
            "version": (new or old).get("version"),
            "changes": _changed(old, new) if record["eventName"] == "MODIFY" else None,
            "approx_time": d.get("ApproximateCreationDateTime"),
            "event_id": record["eventID"],
        }
        # One JSON line per change: CloudWatch Logs is the audit sink (free tier: 5 GB/month).
        print(json.dumps(entry, default=str))
    return {"processed": len(event["Records"])}
