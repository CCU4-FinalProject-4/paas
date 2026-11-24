import os
import socket

AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL") or None
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ITEMS_TABLE = os.getenv("ITEMS_TABLE", "paas-items")
SESSIONS_TABLE = os.getenv("SESSIONS_TABLE", "paas-sessions")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
APP_VERSION = os.getenv("APP_VERSION", "dev")
HOSTNAME = socket.gethostname()
