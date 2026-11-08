LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "planner-tests",
    }
}

import json


class FakeStreamResponse:
    """Stands in for a streamed requests.Response (context manager + iter_content)."""

    def __init__(self, status_code=200, json_data=None, body=None):
        self.status_code = status_code
        self._body = body if body is not None else json.dumps(
            json_data if json_data is not None else {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=65536):
        yield self._body