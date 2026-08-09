"""
Jet Web Framework
Created by Code Gear, Copyright 2026-2029

A lightweight, Python-first web framework.

    from jet import *

    app = Jet()
    app.page("/", "index.html")
    app.route("/login", login)
    serve(app)
"""

__version__ = "0.1"
__author__ = "Code Gear"

from .app import Jet
from .request import Request
from .response import Response
from .template import Template, TemplateEngine
from .server import serve

__all__ = [
    "Jet",
    "Request",
    "Response",
    "Template",
    "TemplateEngine",
    "serve",
]
