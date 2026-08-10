"""
jet.docs

Built-in, zero-config API documentation.

If enabled, Jet automatically mounts:
    /docs          -- interactive Scalar UI, powered by /openapi.json
    /doc           -- interactive Swagger UI, powered by /openapi.json
    /openapi.json  -- a minimal OpenAPI-style description
"""

from .response import Response


def openapi_spec(app):
    paths = {}
    for route in app.router.routes:
        method_entry = {
            "summary": getattr(route.handler, "__name__", "handler"),
            "responses": {"200": {"description": "Successful response"}},
        }
        paths[route.path] = {"get": method_entry}

    return {
        "openapi": "3.0.0",
        "info": {
            "title": app.name,
            "version": app.version,
        },
        "paths": paths,
    }


def scalar_page(app):
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{app.name} - Docs</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin: 0; }}
  </style>
</head>
<body>
  <script id="api-reference" data-url="/openapi.json"></script>
  <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
</body>
</html>"""
    return Response.html(html)


def swagger_page(app):
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{app.name} - Swagger UI</title>
  <link rel="stylesheet"
        href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui.min.css">
  <style>
    body {{ margin: 0; background: #0f1115; }}
    .topbar {{ display: none; }}
    .swagger-ui .info .title {{ color: #e6e6e6; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui-bundle.min.js"></script>
  <script>
    window.onload = () => {{
      window.ui = SwaggerUIBundle({{
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        presets: [SwaggerUIBundle.presets.apis],
        layout: "BaseLayout"
      }});
    }};
  </script>
</body>
</html>"""
    return Response.html(html)


def mount(app):
    """Attach /docs (Scalar), /doc (Swagger UI), and /openapi.json to the app."""
    app.router.add("/docs", lambda req: scalar_page(app))
    app.router.add("/doc", lambda req: swagger_page(app))
    app.router.add("/openapi.json", lambda req: Response.json(openapi_spec(app)))
