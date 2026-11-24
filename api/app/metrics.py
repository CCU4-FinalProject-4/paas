from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"])
IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests being served")
DYNAMODB_LATENCY = Histogram(
    "dynamodb_request_seconds", "DynamoDB call latency", ["operation", "outcome"]
)
