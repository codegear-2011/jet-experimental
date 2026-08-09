"""
jet.app

The Jet application object. This is the center of every Jet project.

    app = Jet()
    app.page("/", "index.html")
    app.route("/login", login)
"""

import os

from . import docs as _docs
from . import errors
from .request import Request
from .response import Response
from .router import Router
from .template import Template, TemplateError

STATIC_DIR = "static"
TEMPLATES_DIR = "templates"
UPLOADS_DIR = "uploads"


class Jet:
    """
    The Jet application.

    Configuration describes the application (config.py).
    Application code describes application behavior (app.py).
    """

    def __init__(self, config=None, name="Jet App", docs=True, debug=True):
        self.config = config or {}
        self.name = name
        self.version = "0.1"
        self.debug = debug
        self.router = Router()
        self.docs_enabled = docs

        self.static_dir = self.config.get("STATIC_DIR", STATIC_DIR)
        self.templates_dir = self.config.get("TEMPLATES_DIR", TEMPLATES_DIR)
        self.uploads_dir = self.config.get("UPLOADS_DIR", UPLOADS_DIR)

        if self.docs_enabled:
            _docs.mount(self)

    # -- routing ------------------------------------------------------

    def route(self, path, handler):
        """Register a function-based route.

            app.route("/login", login)
        """
        self.router.add(path, handler)

    def page(self, path, template_name):
        """Shortcut for template rendering.

            app.page("/", "index.html")

        Equivalent to:

            app.route("/", Template("index.html"))
        """
        template = Template(template_name, self.templates_dir)
        self.router.add(path, template)

    # -- request handling ------------------------------------------------

    def handle(self, request: Request) -> Response:
        """Resolve and run a request, always returning a Response."""

        if request.path.startswith(f"/{self.static_dir}/"):
            return self._serve_static(request.path)

        handler, params = self.router.resolve(request.path)

        if handler is None:
            return Response.html(errors.not_found(request.path), status=404)

        request.params = params or {}

        try:
            if isinstance(handler, Template):
                body = handler.render(request=request, **request.params)
                return Response.html(body)

            result = handler(request) if _wants_request(handler) else handler()
            return Response.make(result)

        except TemplateError as exc:
            return Response.html(errors.template_error(exc, debug=self.debug), status=500)
        except Exception as exc:  # noqa: BLE001 - top level safety net
            return Response.html(errors.server_error(exc, debug=self.debug), status=500)

    def _serve_static(self, path):
        relative = path[len(f"/{self.static_dir}/"):]
        full_path = os.path.join(self.static_dir, relative)

        full_path = os.path.normpath(full_path)
        if not full_path.startswith(self.static_dir):
            return Response.html(errors.not_found(path), status=404)

        if not os.path.isfile(full_path):
            return Response.html(errors.not_found(path), status=404)

        return Response.file(full_path)

    def __repr__(self):
        return f"<Jet name={self.name!r} routes={len(self.router.routes)}>"


def _wants_request(handler):
    """Handlers may take zero or one (request) argument."""
    try:
        code = handler.__code__
        return code.co_argcount >= 1
    except AttributeError:
        return True
