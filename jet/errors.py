"""
jet.errors

Beautiful built-in error pages for 404, 500, template errors,
and route errors. Tracebacks are shown only in development mode.
"""

import html
import traceback

_BASE_STYLE = """
<style>
  body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1115; color: #e6e6e6;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .card {
    max-width: 720px; width: 90%; padding: 40px;
    background: #161922; border-radius: 12px;
    border: 1px solid #262b3a;
    box-shadow: 0 20px 60px rgba(0,0,0,0.4);
  }
  .code { font-size: 64px; font-weight: 800; color: #5b8cff; margin: 0; }
  .title { font-size: 20px; margin: 4px 0 16px; color: #ffffff; }
  .message { color: #a3a9bb; line-height: 1.5; }
  pre {
    background: #0b0d12; border: 1px solid #262b3a; border-radius: 8px;
    padding: 16px; overflow-x: auto; color: #ff8080; font-size: 13px;
  }
  .badge {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    background: #1d2436; color: #5b8cff; font-size: 12px; margin-bottom: 16px;
  }
  a { color: #5b8cff; }
</style>
"""


def _page(code, title, message, traceback_text=None):
    tb_html = ""
    if traceback_text:
        tb_html = f"<pre>{html.escape(traceback_text)}</pre>"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{code} - {title}</title>
  {_BASE_STYLE}
</head>
<body>
  <div class="card">
    <span class="badge">Jet</span>
    <p class="code">{code}</p>
    <p class="title">{title}</p>
    <p class="message">{message}</p>
    {tb_html}
  </div>
</body>
</html>"""


def not_found(path):
    return _page(404, "Page Not Found", f"No route matches <code>{html.escape(path)}</code>.")


def server_error(exc, debug=False):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if debug else None
    return _page(500, "Internal Server Error", str(exc) or "Something went wrong.", tb)


def template_error(exc, debug=False):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if debug else None
    return _page(500, "Template Error", str(exc), tb)


def route_error(exc, debug=False):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if debug else None
    return _page(500, "Route Error", str(exc), tb)
