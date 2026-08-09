"""
jet.router

Function-based routing. No decorators, no magic.

    router.add("/login", login)
    router.add("/user/<id>", show_user)

If the compiled `jet_core` Rust extension is available, path matching
is delegated to it for speed. Otherwise Jet transparently falls back
to a pure-Python implementation -- the public API is identical either
way, and callers never need to know which backend is active.
"""

import re

try:
    import jet_core as _core
    HAS_RUST_CORE = True
except ImportError:
    _core = None
    HAS_RUST_CORE = False


class RouteError(Exception):
    """Raised when a route cannot be matched or is misconfigured."""


class Route:
    """A single registered path -> handler pair (backend-agnostic)."""

    def __init__(self, path, handler):
        self.path = path
        self.handler = handler
        self.pattern, self.param_names = self._compile(path)

    @staticmethod
    def _compile(path):
        param_names = []

        def replace(match):
            name = match.group(1)
            param_names.append(name)
            return r"(?P<%s>[^/]+)" % name

        pattern = re.sub(r"<(\w+)>", replace, path)
        pattern = f"^{pattern}$"
        return re.compile(pattern), param_names

    def match(self, path):
        match = self.pattern.match(path)
        if not match:
            return None
        return match.groupdict()


class Router:
    """
    Holds every registered route and resolves incoming paths.

    Uses `jet_core.Router` (Rust) for the matching hot path when the
    compiled extension is installed; otherwise matches with Python's
    `re` module. Either way, `router.routes` always holds plain
    `Route` objects for introspection (e.g. by `jet.docs`).
    """

    def __init__(self):
        self.routes = []
        self._core_router = _core.Router() if HAS_RUST_CORE else None

    def add(self, path, handler):
        self.routes.append(Route(path, handler))
        if self._core_router is not None:
            self._core_router.add(path)

    def resolve(self, path):
        """Return (handler, params) for a path, or (None, None)."""
        if self._core_router is not None:
            result = self._core_router.resolve(path)
            if result is None:
                return None, None
            index, params = result
            return self.routes[index].handler, dict(params)

        for route in self.routes:
            params = route.match(path)
            if params is not None:
                return route.handler, params
        return None, None

    def list_routes(self):
        return [route.path for route in self.routes]
