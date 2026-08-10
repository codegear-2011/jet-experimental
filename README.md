<div align="center">

# ⚡ Jet

**A lightweight, Python-first web framework.**

*Created by Code Gear — Copyright 2026–2029*

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1-5b8cff)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](#license)
[![Status](https://img.shields.io/badge/status-early--development-orange)](#)

</div>

---

## Philosophy

Jet follows one simple principle:

> **Configuration describes the application.**
> **Application code describes application behavior.**

Jet avoids unnecessary magic. Every API is readable by beginners,
yet powerful enough to grow into a full framework.

Jet is **not** a clone of Flask, Bottle, Django, or FastAPI — it is
a minimal core built from scratch, in pure Python, with no hidden
decorators and no required abstractions.

---

## Features

- 🐍 **Pure Python** — zero required dependencies
- 🦀 **Optional Rust core (Robyn-style)** — routing, JSON encoding, and the dev server can run on a compiled PyO3 extension with its own hand-written HTTP/1.1 layer (no third-party HTTP framework dependency) using a multi-process worker model, with automatic fallback to pure Python. Works on any standard Python 3.8+, no special interpreter build required
- 🧭 **Function-based routing** — no decorators, just `app.route(path, handler)`
- 📄 **Built-in template engine** — templates compile to real Python for speed and full language support
- 📦 **Static file serving** — out of the box, no configuration needed
- 🔥 **Development server** — auto-reload, colored request logging, and a startup banner
- 🩹 **Beautiful error pages** — 404 / 500 / template errors, with tracebacks in debug mode
- 📚 **Auto-generated docs** — interactive Swagger UI at `/docs`, plus `/openapi.json`
- 🛠️ **Single CLI** — `jet newapp`, `jet run`, `jet build`, `jet shell`

---

## Installation

```bash
git clone https://github.com/<your-username>/jet.git
cd jet
pip install -e .
```

This installs the `jet` command globally (inside your environment).

---

## Quickstart

```bash
jet newapp myapp
cd myapp
jet run
```

This scaffolds a new project:

```
myapp/
├── app.py
├── config.py
├── templates/
│   └── index.html
├── static/
└── uploads/
```

`app.py`:

```python
from jet import *

app = Jet()


def login(request):
    return Response.html("<h1>Login page</h1>")


app.page("/", "index.html")
app.route("/login", login)

if __name__ == "__main__":
    serve(app)
```

Run it:

```bash
jet run
```

```
Jet Web Framework
Created by Code Gear, Copyright 2026-2029
Version 0.1 -stable+1784791143

starting browser...
listening on IPv4: 127.0.0.1:2011
Listening on localhost: localhost:2011
```

Visit **http://127.0.0.1:2011** — plus the built-in `/docs` (Swagger UI)
and `/openapi.json`.

---

## Routing

Jet uses plain functions, not decorators.

```python
def home(request):
    return Response.html("<h1>Welcome</h1>")

def user_profile(request):
    user_id = request.params["id"]
    return Response.json({"user": user_id})

app.route("/", home)
app.route("/user/<id>", user_profile)
```

### Pages (template shortcut)

```python
app.page("/", "index.html")
```

is equivalent to:

```python
app.route("/", lambda request: Response.html(Template("index.html").render()))
```

`app.page()` only passes `request` and route params to the template.
If you need custom variables, render explicitly:

```python
def home(request):
    return Response.html(
        Template("index.html").render(
            title="Jet",
            tagline="Fast. Simple. Python.",
        )
    )

app.route("/", home)
```

---

## Templates

Jet ships a small, dependency-free template engine. Templates compile
into real Python source, so you get full expressions and control flow
— no reinvented mini-language.

```html
<h1>Hello, <%jet name %>!</h1>

<%jet: for item in items %>
    <li><%jet item.upper() %></li>
<%jet: end %>

<%jet: if user_is_admin %>
    <span>Admin Panel</span>
<%jet: elif user_is_staff %>
    <span>Staff Area</span>
<%jet: else %>
    <span>Welcome</span>
<%jet: end %>

{# this is a comment and is removed from output #}
```

| Syntax | Meaning |
|---|---|
| `<%jet expr %>` | Output a Python expression |
| `<%jet: statement %>` | Open a block (`if`, `for`, `while`, `with`) |
| `<%jet: elif condition %>` | `elif` branch |
| `<%jet: else %>` | `else` branch |
| `<%jet: end %>` | Close the current block |
| `{# comment #}` | Removed from output |

---

## Request & Response

```python
def submit(request):
    print(request.method, request.path)
    print(request.query)       # ?key=value
    print(request.form())      # form-encoded body
    print(request.json())      # JSON body
    print(request.cookies)

    return Response.json({"ok": True})
```

`Response` helpers:

```python
Response.html("<h1>Hi</h1>")
Response.json({"ok": True})
Response.redirect("/login")
Response.file("static/logo.png")
```

---

## Static Files & Uploads

- `static/` is served automatically — no route registration needed.
- `uploads/` is a reserved directory for future file-upload handling.

---

## Auto Documentation

Jet automatically mounts:

- **`/docs`** — interactive Swagger UI, generated from your routes
- **`/openapi.json`** — a minimal OpenAPI-style spec

No CLI command required. Disable with `Jet(docs=False)` if not needed.

---

## CLI

```bash
jet newapp myapp   # scaffold a new project
jet run            # run app.py with the dev server
jet build          # reserved for future use
jet shell          # interactive shell with your app loaded
```

Everything goes through the single `jet` command — no extra executables.

---

## Project Structure

```
project/
├── app.py          # application logic (routes, handlers)
├── config.py       # application configuration only
├── templates/
├── static/
└── uploads/
```

- **`config.py`** — configuration only. No routes here.
- **`app.py`** — application behavior only.

---

## Core Modules (v0.1)

Jet's core intentionally contains only:

`App` · `Router` · `Request` · `Response` · `Template` · `Static Files`
· `Development Server` · `Error Pages` · `Auto Documentation`

### Not included in v0.1

The following are reserved for future versions and are **not** part
of the core:

`ORM` · `Authentication` · `Sessions` · `Advanced Cookies API` ·
`Middleware` · `Dependency Injection` · `Cache` · `Mail` · `Queue` ·
`Storage` · `Admin` · `Forms` · `Migrations` · `WebSocket`

---

## Design Rules

- Pure Python, minimal dependencies
- No unnecessary abstractions
- No decorator-based routing
- Simple, stable public API
- Fast startup, easy to read
- Standard terminology — `App`, `Route`, `Request`, `Response`,
  `Template` — no invented jargon

---

## Roadmap

Jet aims to grow carefully from a minimal core (like Bottle) toward a
complete framework — while keeping a single, consistent API. Every
future feature (auth, cache, mail, storage, docs batteries) should
feel like a natural extension of Jet Core, accessible entirely through
the `jet` CLI (`jet auth`, `jet data`, `jet cache`, `jet mail`,
`jet storage`, …).

---

## Rust Core (optional, activates automatically once published)

Jet's router, JSON encoding, and dev server can run on a compiled Rust
extension (`jet_core`, built with **PyO3 0.22.6**) instead of pure
Python. Its HTTP layer is **hand-written against Rust's standard
library only** — no third-party HTTP framework (no hyper, no
actix-web, no xitca-web) — specifically so there's no external
crate's routing/builder API to get wrong. It uses the same
GIL-workaround strategy as [Robyn](https://github.com/sansyrox/robyn):
one OS process per CPU core (each with its own interpreter and GIL,
bound to the same port via `SO_REUSEPORT`), plus several accept
threads per process that only touch Python for the moment your
handler actually runs. **No special Python build required** — it
targets any standard CPython 3.8+ via `abi3`.

### Instant activation on install

```bash
pip install "jet-framework[speed]"
```

installs `jet-core` as a dependency. Once it's published to PyPI as a
prebuilt wheel (see [`.github/workflows/build-wheels.yml`](./.github/workflows/build-wheels.yml)),
this is a **binary download, not a local compile** — Rust is active
immediately, exactly like installing `orjson` or `ruff`. No Rust
toolchain needed on your machine.

```bash
pip install jet-framework            # pure Python only
pip install "jet-framework[speed]"   # + Rust core, auto-detected and used
```

Jet always checks for `jet_core` at import time and switches engines
automatically — you never change your application code either way.

### Opting back into pure Python

If `jet_core` is installed but you want plain Python behavior anyway
(debugging, comparing behavior, or just because), set:

```bash
JET_FORCE_PYTHON=1 jet run
```

or in code, before importing `jet`:

```python
import os
os.environ["JET_FORCE_PYTHON"] = "1"
from jet import *
```

The startup banner always tells you which engine is active and why
(`jet_core not installed` vs `forced via JET_FORCE_PYTHON` vs the
Rust engine with its worker count).

### Building from source yourself

Until prebuilt wheels are published, `pip install "jet-framework[speed]"`
will try to build `jet-core` from source, which requires the Rust
toolchain locally:

```bash
cd rust
pip install maturin
maturin develop --release
```

See [`rust/BUILD.md`](./rust/BUILD.md) for the full architecture,
build steps, and an honest note on what this optimization does (and
doesn't) speed up.

---


## Contributing



Issues and pull requests are welcome. Please keep changes aligned
with Jet's philosophy: simple, readable, and free of unnecessary
magic.

## License

MIT © Code Gear, 2026–2029
