# Jet Rust Core (`jet_core`)

Jet's own performance core — a **hand-written HTTP/1.1 server**, a
compiled **Router**, and **Rust-side JSON encoding** — written in
Rust and exposed to Python through **PyO3 0.22.6** + **maturin**.

**No third-party HTTP framework or server crate is used at all** — no
tiny_http, no xitca-web, no hyper, no actix-web. The only
dependencies are `pyo3` (Python bindings), `socket2` (a thin wrapper
around raw OS socket options, not a framework), and `serde_json`
(JSON encoding). The HTTP/1.1 request line, headers, and
Content-Length body are parsed by hand against `std::net`/`std::io`,
and responses are written the same way.

## Why build our own HTTP layer instead of using a library?

Two earlier iterations of this crate tried wiring in an external
async web framework (xitca-web). Both failed to compile, because:

1. Their exact builder/routing API couldn't be verified against real
   documentation from the environment that generated this code — it
   was a best-effort guess, and the guess was wrong (twice).
2. Jet specifically needs a **catch-all handler** — "every path,
   every method, forward it to Python" — since Jet does its own
   routing in `Router`. That shape isn't shown in any framework's
   quick-start examples, which all register specific paths.

`std::net::TcpListener`/`TcpStream` and `std::io::{Read, Write,
BufRead}` are part of Rust's stable, fully-documented standard
library. Parsing an HTTP/1.1 request line and headers by hand against
that API involves nothing that needs verifying against external docs
— it's a well-understood, bounded problem, and it removes the
recurring failure point entirely.

## Architecture (Robyn-style, zero HTTP dependencies)

```
                     ┌─────────────────────────────┐
   Incoming          │   Kernel (SO_REUSEPORT)      │
   connections  ───▶  │   load-balances across       │
                     │   worker processes            │
                     └───────────┬─────────┬─────────┘
                                 │         │
                       ┌─────────▼──┐ ┌────▼───────┐   ... one per
                       │ Worker #1   │ │ Worker #2   │   CPU core
                       │ (OS process)│ │ (OS process)│
                       │             │ │             │
                       │ N threads,  │ │ N threads,  │
                       │ each        │ │ each        │
                       │ blocking on │ │ blocking on │
                       │ accept()    │ │ accept()    │
                       │  -- no GIL  │ │  -- no GIL  │
                       │  needed here│ │  needed here│
                       │             │ │             │
                       │ own Python  │ │ own Python  │
                       │ interpreter │ │ interpreter │
                       │ + own GIL,  │ │ + own GIL,  │
                       │ held only   │ │ held only   │
                       │ during the  │ │ during the  │
                       │ handler call│ │ handler call│
                       └─────────────┘ └─────────────┘
```

**How this works around the GIL, without needing a special Python
build** — the same approach Robyn uses:

1. **Multi-process workers.** One OS process per CPU core, each with
   its own interpreter and its own GIL, all bound to the *same port*
   via `SO_REUSEPORT`. The kernel load-balances connections across
   them. N processes running means N GILs running in parallel.

2. **Multiple accept threads per process.** Several OS threads all
   call `TcpListener::accept()` on the same shared listener
   concurrently (safe because `accept()` takes `&self`, and the OS
   hands each incoming connection to exactly one caller). Connection
   accept, request parsing, and response writing all happen with the
   GIL released. The GIL is acquired for the *smallest possible
   window*: only for the moment your Python handler (`app.handle`)
   actually executes.

You do **not** need Python 3.14 free-threaded for any of this — it
works the same way on any Python 3.8+.

## Scope limits (read this before deploying)

This is a **dev-server-grade** HTTP/1.1 implementation:

- ✅ Request line, headers, `Content-Length`-delimited body
- ✅ Any HTTP method, any path (Jet's own `Router` decides what's valid)
- ❌ Keep-alive (every response sends `Connection: close`)
- ❌ Chunked transfer-encoding
- ❌ HTTP/2
- ❌ TLS

If you need any of the ❌ items for production, put a real reverse
proxy (nginx, Caddy, an actual load balancer) in front of Jet — the
same way you'd put one in front of Flask's or Django's built-in dev
servers. This mirrors how those frameworks are actually deployed.

## Build it yourself

### 1. Install the Rust toolchain

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Rust **1.85 or newer** is required (check with `cargo --version`) —
some of `jet_core`'s dependencies use the `edition2024` Cargo feature,
which older toolchains reject. If you're on an older version:

```bash
rustup update stable
```

### 2. Install maturin

```bash
pip install maturin
```

(No specific Python version required — any 3.8+ works, thanks to
`abi3`.)

### 3. Build and install into your Python environment

`maturin develop` needs an active virtualenv:

```bash
cd rust
python -m venv .venv          # if you don't already have one
source .venv/bin/activate     # Linux/macOS
# .venv\Scripts\activate      # Windows

pip install maturin
maturin develop --release
```

If you'd rather not use a virtualenv, build a wheel directly and
install it into your normal Python:

```bash
cd rust
pip install maturin
maturin build --release
pip install target/wheels/jet_core-*.whl
```

To build a distributable wheel for others:

```bash
maturin build --release
```

Because the wheel is `abi3`, it's portable — build once per OS/CPU
architecture and it works across Python 3.8 through whatever's newest.

> **Windows note:** `SO_REUSEPORT` is a Unix socket option — the
> multi-process model works on Linux/macOS. On Windows, `jet_core`
> still runs (single process, still multi-threaded), but won't spread
> across OS processes the same way.

## Verifying it's active

Run any Jet app — the startup banner reports the engine and worker
count:

```
Jet Web Framework
Created by Code Gear, Copyright 2026-2029
Version 0.1 -stable+...
Engine: Rust core (jet_core) -- 8 worker process(es)

starting browser...
listening on IPv4: 127.0.0.1:2011
Listening on localhost: localhost:2011
```

If you see `Engine: pure Python (single process) -- jet_core not
installed`, the extension isn't built or isn't importable from your
current interpreter — check that `maturin develop --release` (or
`pip install target/wheels/jet_core-*.whl`) completed without errors
and that you're running Python from the same environment you built
into.

## Controlling worker count

```python
serve(app, workers=4)   # override; defaults to os.cpu_count()
```

## Opting back into pure Python

Even with `jet_core` installed, you can force Jet back to
pure-Python behavior:

```bash
JET_FORCE_PYTHON=1 jet run
```

## Source layout

```
rust/
├── Cargo.toml         -- pyo3 0.22.6 (pinned, abi3-py38), socket2, serde_json
├── pyproject.toml      -- maturin build config
└── src/
    └── lib.rs           -- Router, json_dumps, serve_forever (hand-written HTTP/1.1)
```

## API surface exposed to Python

```python
import jet_core

# Router (Mutex-guarded internally; safe to call from multiple
# threads, whether or not your Python build has a GIL)
router = jet_core.Router()
index = router.add("/user/<id>")          # -> 0
result = router.resolve("/user/42")       # -> (0, {"id": "42"}) | None

# JSON (used by Response.json())
raw_bytes = jet_core.json_dumps({"ok": True})

# Multi-threaded server (called once per forked OS process by
# jet/server.py -- you normally won't call this directly)
jet_core.serve_forever(
    host="127.0.0.1",
    port=2011,
    callback=my_callback,   # (method, url, headers, body) -> (status, headers, body)
    workers=8,                # accept threads *within this process*
    reuse_port=True,          # bind via SO_REUSEPORT for multi-process sharing
)
```

`jet/router.py`, `jet/response.py`, and `jet/server.py` are the only
files that import `jet_core` — everything else in Jet is
backend-agnostic and works identically with or without it.

## Honest notes

- **Not compiled or benchmarked in the environment that generated
  this code.** No Rust toolchain and no network access to crates.io
  were available there. This version was written specifically to
  minimize the risk of another failed build: everything used is
  either Rust's standard library (fully documented, stable) or one of
  three small, narrowly-scoped crates (`pyo3` pinned to an exact
  known-good version, `socket2` for one socket option, `serde_json`
  for encoding) — nothing here depends on guessing an unfamiliar
  framework's API surface.
- **This does not eliminate the GIL** on ordinary Python — it works
  around it the same way Robyn/Gunicorn/uvicorn's multi-worker mode
  do: many processes, each with its own GIL, plus keeping Python out
  of the connection-handling hot path as much as possible. Your
  handler's own Python code still runs at Python speed inside
  whichever process/thread picks up that request.
- **This is a simpler concurrency model than a full async reactor**
  (epoll/io_uring-based, like xitca-web/hyper would give you) —
  thread-per-connection instead of async tasks. For a dev server, or
  moderate concurrency in production behind a reverse proxy, this
  difference is usually not the bottleneck; it becomes more relevant
  at very high simultaneous connection counts (thousands+), where an
  async reactor uses less memory per connection than an OS thread
  does.
- **"Faster than FastAPI" is not a claim this document makes.**
  Whether this beats a specific FastAPI/uvicorn deployment depends
  entirely on your workload — measure it with `wrk` or `oha` against
  both, on your own hardware, before drawing conclusions.
