# Jet Rust Core (`jet_core`)

Jet's optional performance core — an async **xitca-web/Tokio** server,
a compiled **Router**, and **Rust-side JSON encoding** — written in
Rust and exposed to Python through **PyO3 0.29.0** + **maturin**.

Built as an **abi3 extension**: one compiled wheel runs unmodified on
**any standard CPython 3.8 or newer**. No free-threaded interpreter,
no special build, no per-version recompiling.

## Architecture (Robyn-style)

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
                       │ Tokio async │ │ Tokio async │
                       │ reactor     │ │ reactor     │
                       │ (xitca-web) │ │ (xitca-web) │
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
build** — this is the same approach Robyn uses:

1. **Multi-process workers.** One OS process per CPU core, each with
   its own interpreter and its own GIL, all bound to the *same port*
   via `SO_REUSEPORT`. The kernel load-balances connections across
   them. N processes running means N GILs running in parallel — real
   multi-core throughput on completely ordinary Python.

2. **A GIL-free I/O layer inside each process.** Connection accept,
   HTTP parsing, and response serialization all happen in xitca-web on
   Tokio's async reactor — no Python bytecode runs during any of that,
   so it's outside the GIL's reach entirely regardless of which Python
   build you're on. The GIL is acquired for the *smallest possible
   window*: only for the moment your Python handler (`app.handle`)
   actually executes, via `tokio::task::spawn_blocking`.

You do **not** need Python 3.14 free-threaded for any of this. It
works the same way on Python 3.8, 3.11, 3.12, whatever you already
have installed. (If you *do* happen to be on a free-threaded build,
`Router`'s internal `Mutex` guard means it's still safe to use — that
part was written defensively either way — but it's a bonus, not a
requirement.)

## Why it wasn't built for you

Compiling this requires the Rust toolchain and network access to
crates.io / PyPI (for `pyo3`, `xitca-web`, `tokio`, `maturin`).
Neither was available in the environment that generated this project,
so the source is provided ready-to-build but not pre-compiled, and
**not yet verified to compile** — see the honesty note at the bottom.

## Build it yourself

### 1. Install the Rust toolchain

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### 2. Install maturin

Using whatever Python you already have (no special version needed):

```bash
pip install maturin
```

### 3. Build and install into your Python environment

```bash
cd rust
maturin develop --release
```

This compiles `jet_core` (xitca-web + Tokio + PyO3 0.29.0, abi3) and
installs it into your active virtualenv/interpreter. `import jet_core`
will now succeed, and `Response.json()` / `Router` / `serve()` all
pick it up automatically.

To build a distributable wheel instead:

```bash
maturin build --release
pip install target/wheels/jet_core-*.whl
```

Because the wheel is `abi3`, it's portable — build once per OS/CPU
architecture and it works across Python 3.8 through whatever's newest,
no rebuild needed per Python version.

> **Linux/macOS note:** `SO_REUSEPORT` is a Unix socket option — the
> multi-process model works there. On Windows, `jet_core` still runs
> (single process, still async/non-blocking), but won't spread across
> OS processes the same way.

## Verifying it's active

Run any Jet app — the startup banner reports the engine and worker
count:

```
Jet Web Framework
Created by Code Gear, Copyright 2026-2029
Version 0.1 -stable+...
Engine: Rust core (jet_core) -- 8 worker process(es), xitca-web/Tokio

starting browser...
listening on IPv4: 127.0.0.1:2011
Listening on localhost: localhost:2011
```

Each request log line also shows which worker PID served it:

```
[14:02:11] (worker 48213) GET / 200
[14:02:11] (worker 48217) GET /login 200
```

If you see `Engine: pure Python (single process)`, the extension
isn't built or isn't importable from your current interpreter — check
that `maturin develop --release` completed without errors and that
you're running Python from the same environment you built into.

## Controlling worker count

```python
serve(app, workers=4)   # override; defaults to os.cpu_count()
```

## Source layout

```
rust/
├── Cargo.toml         -- pyo3 0.29.0 (abi3-py38), xitca-web 0.8.1, tokio, socket2, serde_json
├── pyproject.toml      -- maturin build config
└── src/
    └── lib.rs           -- Router, json_dumps, serve_forever (xitca-web/Tokio)
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

# Async multi-worker server (called once per forked OS process by
# jet/server.py -- you normally won't call this directly)
jet_core.serve_forever(
    host="127.0.0.1",
    port=2011,
    callback=my_callback,   # (method, url, headers, body) -> (status, headers, body)
    workers=4,               # Tokio reactor threads *within this process*
    reuse_port=True,         # bind via SO_REUSEPORT for multi-process sharing
)
```

`jet/router.py`, `jet/response.py`, and `jet/server.py` are the only
files that import `jet_core` — everything else in Jet is
backend-agnostic and works identically with or without it.

## Honest notes

- **Not compiled or benchmarked here.** No Rust toolchain and no
  network access to crates.io were available in the environment that
  generated this code. Treat `src/lib.rs` as a best-effort
  implementation against xitca-web 0.8.1's and PyO3 0.29.0's
  documented APIs, not as something proven to `cargo build` cleanly.
  If xitca-web's `HttpServer`/`fn_service`/body-stream surface has
  shifted slightly, the fix is almost always confined to
  `serve_forever` and `handle_request` in `src/lib.rs` — `Router` and
  `json_dumps` don't depend on the web framework at all and are much
  lower-risk.
- **This does not eliminate the GIL** on ordinary Python — it works
  around it the same way Robyn/Gunicorn/uvicorn's multi-worker mode
  do: many processes, each with its own GIL, plus keeping Python out
  of the I/O hot path as much as possible. Your handler's own Python
  code still runs at Python speed inside whichever process picks up
  that request.
- **"Faster than FastAPI" is not a claim this document makes.**
  Whether this beats a specific FastAPI/uvicorn deployment depends
  entirely on your workload — measure it with `wrk` or `oha` against
  both, on your own hardware, before drawing conclusions.
- I/O-bound workloads (DB calls, external APIs) will see the least
  relative benefit from any of this, since the bottleneck there is
  the I/O wait itself, not the framework layer.
