"""
jet.server

Jet's built-in development server.

    serve(app)

Supports:
    - auto reload
    - request logging
    - a beautiful startup banner

When the compiled `jet_core` Rust extension is available, Jet runs an
async Tokio/xitca-web server instead of Python's `http.server`, and
launches **one OS process per CPU core** (like Robyn/Gunicorn), each
process binding the same port via SO_REUSEPORT. Every process has its
own Python interpreter and its own GIL, so requests genuinely run in
parallel across cores -- not just concurrently within one interpreter.
Without the extension, Jet falls back to a single-process pure-Python
server automatically.

Set the environment variable JET_FORCE_PYTHON=1 to force the
single-process pure-Python server even when `jet_core` is installed
-- useful for debugging, or if you just prefer plain Python for now.
"""

import multiprocessing
import os
import signal
import sys
import time
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .request import Request
from . import __version__
from ._engine import HAS_RUST_CORE, core as _core

BANNER_COLOR = "\033[94m"
DIM = "\033[2m"
GREEN = "\033[92m"
RESET = "\033[0m"


# ---------------------------------------------------------------------
# Pure-Python server (fallback)
# ---------------------------------------------------------------------

def _make_handler(app, log_requests):
    class JetHTTPHandler(BaseHTTPRequestHandler):
        server_version = f"Jet/{__version__}"

        def _dispatch(self, method):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""

            request = Request(
                method=method,
                path=self.path,
                headers=dict(self.headers.items()),
                body=body,
            )

            response = app.handle(request)

            self.send_response(response.status)
            for key, value in response.headers.items():
                if key == "__set_cookie__":
                    for cookie in value:
                        self.send_header("Set-Cookie", cookie)
                else:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

            if log_requests:
                self._log(method, response.status)

        def _log(self, method, status):
            color = GREEN if status < 400 else "\033[91m"
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"{DIM}[{timestamp}]{RESET} {method} {self.path} {color}{status}{RESET}")

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def log_message(self, format, *args):
            pass  # silence default stderr logging; we log ourselves

    return JetHTTPHandler


def _serve_python(app, host, port, log):
    handler_class = _make_handler(app, log_requests=log)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{DIM}Jet server stopped.{RESET}")
        httpd.server_close()


# ---------------------------------------------------------------------
# Rust-backed async server (jet_core) -- multi-process
# ---------------------------------------------------------------------

def _build_callback(app, log):
    """
    Bridges jet_core.serve_forever's (method, url, headers, body)
    callback contract to app.handle(), logging the same way the
    pure-Python server does.
    """

    def callback(method, url, headers, body):
        request = Request(method=method, path=url, headers=dict(headers), body=bytes(body))
        response = app.handle(request)

        out_headers = []
        for key, value in response.headers.items():
            if key == "__set_cookie__":
                for cookie in value:
                    out_headers.append(("Set-Cookie", cookie))
            else:
                out_headers.append((key, value))

        if log:
            color = GREEN if response.status < 400 else "\033[91m"
            timestamp = datetime.now().strftime("%H:%M:%S")
            pid = os.getpid()
            print(f"{DIM}[{timestamp}] (worker {pid}){RESET} {method} {url} {color}{response.status}{RESET}")

        return response.status, out_headers, response.body

    return callback


def _worker_main(app, host, port, log, threads_per_worker):
    """Entry point for each forked worker process."""
    # Ignore Ctrl+C in workers; the master process handles shutdown
    # and terminates children explicitly.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    callback = _build_callback(app, log)
    _core.serve_forever(host, port, callback, workers=threads_per_worker, reuse_port=True)


_active_processes = []


def _cleanup_workers():
    for proc in _active_processes:
        if proc.is_alive():
            proc.terminate()
    for proc in _active_processes:
        proc.join(timeout=2)


def _serve_rust(app, host, port, log, workers):
    """
    Launch `workers` OS processes, each running its own async
    Tokio/actix-web reactor and its own Python interpreter, all bound
    to the same port via SO_REUSEPORT. The kernel distributes
    incoming connections across them, giving genuine multi-core
    parallelism instead of single-GIL concurrency.
    """
    threads_per_worker = max(1, (os.cpu_count() or 4) // max(1, workers))

    ctx = multiprocessing.get_context("fork")

    for _ in range(workers):
        proc = ctx.Process(
            target=_worker_main,
            args=(app, host, port, log, threads_per_worker),
            daemon=True,
        )
        proc.start()
        _active_processes.append(proc)

    try:
        for proc in _active_processes:
            proc.join()
    except KeyboardInterrupt:
        print(f"\n{DIM}Stopping {len(_active_processes)} worker process(es)...{RESET}")
        _cleanup_workers()
        print(f"{DIM}Jet server stopped.{RESET}")


# ---------------------------------------------------------------------
# Shared: banner + reload + public entrypoint
# ---------------------------------------------------------------------

def _print_banner(app, host, port, workers):
    if HAS_RUST_CORE:
        engine = f"Rust core (jet_core) -- {workers} worker process(es), xitca-web/Tokio"
    elif os.environ.get("JET_FORCE_PYTHON", "").strip().lower() in ("1", "true", "yes", "on"):
        engine = "pure Python (single process) -- forced via JET_FORCE_PYTHON"
    else:
        engine = "pure Python (single process) -- jet_core not installed"

    print(f"""
{BANNER_COLOR}Jet Web Framework{RESET}
Created by Code Gear, Copyright 2026-2029
Version {__version__} -stable+{int(time.time())}
Engine: {engine}

starting browser...
listening on IPv4: {host}:{port}
Listening on localhost: localhost:{port}
""")


def _watch_and_reload(paths, interval=1.0, cleanup=None):
    """Poll watched files for changes; re-exec the process on change."""
    mtimes = {}

    def snapshot():
        current = {}
        for base in paths:
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for name in files:
                    if name.endswith((".py", ".html")):
                        full = os.path.join(root, name)
                        try:
                            current[full] = os.path.getmtime(full)
                        except OSError:
                            pass
        return current

    mtimes = snapshot()

    while True:
        time.sleep(interval)
        current = snapshot()
        if current != mtimes:
            print(f"{DIM}[reload] change detected, restarting...{RESET}")
            if cleanup is not None:
                cleanup()
            os.execv(sys.executable, [sys.executable] + sys.argv)


def serve(app, host="127.0.0.1", port=2011, reload=True, log=True, open_browser=True, workers=None):
    """
    Run Jet's built-in development server.

    Uses the Rust `jet_core` engine automatically when installed,
    spread across one OS process per CPU core (override with
    `workers=`); otherwise falls back to a single-process pure-Python
    server. Behavior (banner, logging, auto-reload) is consistent
    either way.
    """

    if workers is None:
        workers = os.cpu_count() or 1

    _print_banner(app, host, port, workers if HAS_RUST_CORE else 1)

    if reload:
        watch_thread = threading.Thread(
            target=_watch_and_reload,
            args=([app.templates_dir, os.getcwd()],),
            kwargs={"cleanup": _cleanup_workers if HAS_RUST_CORE else None},
            daemon=True,
        )
        watch_thread.start()

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    if HAS_RUST_CORE:
        _serve_rust(app, host, port, log, workers)
    else:
        _serve_python(app, host, port, log)
