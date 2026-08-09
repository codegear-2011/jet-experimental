"""
jet.request

Represents an incoming HTTP request.
"""

import json as _json
from urllib.parse import parse_qsl, urlparse


class Request:
    """
    A simple, readable representation of an HTTP request.

    Attributes:
        method   -- "GET", "POST", ...
        path     -- "/login"
        headers  -- dict of request headers
        query    -- dict of parsed query-string params
        params   -- dict of route parameters (e.g. /user/<id>)
        body     -- raw request body (bytes)
        cookies  -- dict of parsed cookies
    """

    def __init__(self, method, path, headers=None, body=b"", params=None):
        parsed = urlparse(path)

        self.method = method.upper()
        self.path = parsed.path
        self.headers = headers or {}
        self.query = dict(parse_qsl(parsed.query))
        self.params = params or {}
        self.body = body or b""
        self.cookies = self._parse_cookies(self.headers.get("Cookie", ""))

    # -- helpers ----------------------------------------------------

    @staticmethod
    def _parse_cookies(raw):
        cookies = {}
        if not raw:
            return cookies
        for part in raw.split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                cookies[key] = value
        return cookies

    def text(self):
        """Return the request body decoded as text."""
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        """Parse and return the request body as JSON."""
        if not self.body:
            return None
        return _json.loads(self.text())

    def form(self):
        """Parse the body as application/x-www-form-urlencoded data."""
        return dict(parse_qsl(self.text()))

    def header(self, name, default=None):
        """Case-insensitive header lookup."""
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return default

    def __repr__(self):
        return f"<Request {self.method} {self.path}>"
