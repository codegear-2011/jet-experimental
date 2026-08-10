"""
jet.response

Represents an outgoing HTTP response.

Handlers may return:
    - a Response object
    - a plain string (treated as HTML)
    - a dict / list (treated as JSON)

Set the environment variable JET_FORCE_PYTHON=1 to force Python's
`json` module even when `jet_core` is installed.
"""

import json as _json
import mimetypes
import os

from ._engine import HAS_RUST_CORE, core as _core


class Response:
    """
    A simple, explicit HTTP response.

        Response.html("<h1>Hi</h1>")
        Response.json({"ok": True})
        Response.redirect("/login")
        Response.file("static/logo.png")
    """

    def __init__(self, body=b"", status=200, headers=None, content_type="text/html; charset=utf-8"):
        self.status = status
        self.headers = headers or {}
        self.headers.setdefault("Content-Type", content_type)

        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body

    # -- constructors -------------------------------------------------

    @classmethod
    def html(cls, content, status=200, headers=None):
        return cls(content, status=status, headers=headers, content_type="text/html; charset=utf-8")

    @classmethod
    def json(cls, data, status=200, headers=None):
        if HAS_RUST_CORE:
            content = _core.json_dumps(data)  # already bytes
        else:
            content = _json.dumps(data, indent=2)
        return cls(content, status=status, headers=headers, content_type="application/json")

    @classmethod
    def text(cls, content, status=200, headers=None):
        return cls(content, status=status, headers=headers, content_type="text/plain; charset=utf-8")

    @classmethod
    def redirect(cls, location, status=302):
        response = cls("", status=status, content_type="text/html; charset=utf-8")
        response.headers["Location"] = location
        return response

    @classmethod
    def file(cls, path, status=200, download_name=None):
        if not os.path.exists(path):
            return Response.html(f"<h1>404</h1><p>File not found: {path}</p>", status=404)

        content_type, _ = mimetypes.guess_type(path)
        content_type = content_type or "application/octet-stream"

        with open(path, "rb") as f:
            data = f.read()

        response = cls(data, status=status, content_type=content_type)
        if download_name:
            response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
        return response

    # -- cookies --------------------------------------------------------

    def set_cookie(self, name, value, path="/", max_age=None):
        cookie = f"{name}={value}; Path={path}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        # Multiple Set-Cookie headers are allowed; store as a list.
        self.headers.setdefault("__set_cookie__", [])
        self.headers["__set_cookie__"].append(cookie)

    # -- normalization ----------------------------------------------------

    @staticmethod
    def make(value):
        """Coerce a handler's return value into a Response object."""
        if isinstance(value, Response):
            return value
        if isinstance(value, (dict, list)):
            return Response.json(value)
        if isinstance(value, tuple) and len(value) == 2:
            body, status = value
            return Response.make(body).with_status(status)
        return Response.html(str(value))

    def with_status(self, status):
        self.status = status
        return self

    def __repr__(self):
        return f"<Response {self.status}>"
