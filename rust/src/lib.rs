//! jet_core
//!
//! Jet's own performance core, written in Rust and exposed to Python
//! through PyO3 (pinned to 0.22.6 -- see the version note below),
//! built as an **abi3** extension -- one compiled wheel works
//! unmodified on **any standard CPython 3.8+**. No special interpreter
//! build required.
//!
//! Three pieces live here:
//!
//! 1. `Router`        -- compiled path matching (used by jet.router)
//! 2. `json_dumps`     -- Rust-side JSON encoding (used by jet.response)
//! 3. `serve_forever`  -- a hand-written, std-only HTTP/1.1 server
//!                        (used by jet.server)
//!
//! **No third-party HTTP framework or server crate is used anywhere
//! in this file** -- not tiny_http, not xitca-web, not hyper, not
//! actix-web. The HTTP/1.1 request line, headers, and
//! Content-Length-delimited body are parsed by hand against
//! `std::net`/`std::io`, and responses are written the same way. This
//! is deliberate, not an oversight: two earlier attempts wired in an
//! external async web framework and failed to compile, because their
//! exact builder/routing API couldn't be verified from real docs in
//! the environment that generated this code, and Jet specifically
//! needs a "every path, every method" catch-all handler that isn't
//! shown in any framework's quick-start examples (Jet does its own
//! routing in Python). std's socket API is stable and fully
//! documented, so nothing here is a guess.
//!
//! ## How this works around the GIL (Robyn-style)
//!
//! On ordinary (GIL-enabled) Python, only one thread runs Python
//! bytecode at a time, no matter how fast Rust is. `jet_core` doesn't
//! try to fight that -- it works around it the same way Robyn does,
//! with two complementary tactics:
//!
//! 1. **Multi-process workers.** `jet/server.py` launches one OS
//!    process per CPU core, each with its *own* interpreter and its
//!    own GIL, all bound to the same port via `SO_REUSEPORT`. The
//!    kernel load-balances connections across them. N processes means
//!    N GILs running in parallel -- genuine multi-core throughput
//!    without needing a special Python build.
//!
//! 2. **Multiple accept/parse threads per process.** Within each
//!    process, `serve_forever` spawns several OS threads that all
//!    block on `TcpListener::accept()` in parallel. The GIL is
//!    acquired for the *shortest possible window*: only for the
//!    moment your Python handler (`app.handle`) actually runs.
//!    Connection accept, request parsing, and response writing all
//!    happen GIL-free.
//!
//! `Router`'s internal state is `Mutex`-guarded rather than relying
//! on the GIL for synchronization -- cheap insurance either way, and
//! genuinely necessary if this happens to run on a Python 3.13/3.14
//! free-threaded build, where the GIL isn't there to protect shared
//! state at all.
//!
//! ## Scope limits of the hand-written HTTP layer
//!
//! This implements a practical subset of HTTP/1.1 suitable for a dev
//! server: request line + headers + Content-Length body in, status +
//! headers + body out. It does **not** implement keep-alive
//! (`Connection: close` is sent on every response), chunked
//! transfer-encoding, HTTP/2, or TLS. That's an intentional scope
//! boundary for a framework's built-in dev server, not a bug -- if
//! you need those for production, put a real reverse proxy (nginx,
//! Caddy) in front, the same way you would in front of Flask's or
//! Django's dev servers.
//!
//! ## A note on the PyO3 version pin
//!
//! `Cargo.toml` pins PyO3 to `=0.22.6` rather than a loose range. This
//! code was written against 0.22's API (`Python::with_gil`,
//! `py.allow_threads`, `PyBytes::new_bound`, `.downcast()`), and a
//! newer PyO3 release resolved from a loose version requirement
//! renamed or removed several of those -- which is exactly what broke
//! an earlier build attempt. The pin trades "always get the latest"
//! for "actually compiles with the code as written."

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString};
use serde_json::Value as JsonValue;
use socket2::{Domain, Socket, Type};
use std::net::SocketAddr;
use std::sync::Mutex;

// ---------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------

struct CompiledRoute {
    segments: Vec<Segment>,
}

enum Segment {
    Literal(String),
    Param(String),
}

fn compile_path(path: &str) -> Vec<Segment> {
    path.trim_matches('/')
        .split('/')
        .filter(|s| !s.is_empty())
        .map(|part| {
            if part.starts_with('<') && part.ends_with('>') && part.len() > 2 {
                Segment::Param(part[1..part.len() - 1].to_string())
            } else {
                Segment::Literal(part.to_string())
            }
        })
        .collect()
}

impl CompiledRoute {
    fn matches(&self, path: &str) -> Option<Vec<(String, String)>> {
        let incoming: Vec<&str> = path
            .trim_matches('/')
            .split('/')
            .filter(|s| !s.is_empty())
            .collect();

        if incoming.len() != self.segments.len() {
            return None;
        }

        let mut params = Vec::new();
        for (segment, value) in self.segments.iter().zip(incoming.iter()) {
            match segment {
                Segment::Literal(expected) => {
                    if expected != value {
                        return None;
                    }
                }
                Segment::Param(name) => {
                    params.push((name.clone(), value.to_string()));
                }
            }
        }
        Some(params)
    }
}

/// Fast route matcher. Python keeps its own `index -> handler`
/// mapping; this class only ever returns an index plus captured
/// params.
///
/// State is `Mutex`-guarded rather than relying on the GIL: on a
/// free-threaded Python build, multiple native threads can call
/// `add`/`resolve` on the *same* Router object concurrently with no
/// GIL to serialize them.
#[pyclass]
struct Router {
    inner: Mutex<RouterState>,
}

struct RouterState {
    routes: Vec<CompiledRoute>,
    paths: Vec<String>,
}

#[pymethods]
impl Router {
    #[new]
    fn new() -> Self {
        Router {
            inner: Mutex::new(RouterState {
                routes: Vec::new(),
                paths: Vec::new(),
            }),
        }
    }

    fn add(&self, path: &str) -> usize {
        let mut state = self.inner.lock().expect("jet_core Router mutex poisoned");
        state.routes.push(CompiledRoute {
            segments: compile_path(path),
        });
        state.paths.push(path.to_string());
        state.routes.len() - 1
    }

    fn resolve<'py>(
        &self,
        py: Python<'py>,
        path: &str,
    ) -> Option<(usize, Bound<'py, PyDict>)> {
        let state = self.inner.lock().expect("jet_core Router mutex poisoned");
        for (index, route) in state.routes.iter().enumerate() {
            if let Some(params) = route.matches(path) {
                let dict = PyDict::new_bound(py);
                for (key, value) in params {
                    let _ = dict.set_item(key, value);
                }
                return Some((index, dict));
            }
        }
        None
    }

    fn list_routes(&self) -> Vec<String> {
        self.inner
            .lock()
            .expect("jet_core Router mutex poisoned")
            .paths
            .clone()
    }

    fn __len__(&self) -> usize {
        self.inner
            .lock()
            .expect("jet_core Router mutex poisoned")
            .routes
            .len()
    }
}

// ---------------------------------------------------------------------
// JSON encoding (jet.response.Response.json)
// ---------------------------------------------------------------------

fn py_to_json(obj: &Bound<'_, PyAny>) -> PyResult<JsonValue> {
    if obj.is_none() {
        return Ok(JsonValue::Null);
    }
    if let Ok(b) = obj.downcast::<PyBool>() {
        return Ok(JsonValue::Bool(b.is_true()));
    }
    if let Ok(i) = obj.downcast::<PyInt>() {
        let value: i64 = i.extract()?;
        return Ok(JsonValue::from(value));
    }
    if let Ok(f) = obj.downcast::<PyFloat>() {
        let value: f64 = f.extract()?;
        return Ok(serde_json::Number::from_f64(value)
            .map(JsonValue::Number)
            .unwrap_or(JsonValue::Null));
    }
    if let Ok(s) = obj.downcast::<PyString>() {
        return Ok(JsonValue::String(s.to_string()));
    }
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut items = Vec::with_capacity(list.len());
        for item in list.iter() {
            items.push(py_to_json(&item)?);
        }
        return Ok(JsonValue::Array(items));
    }
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = serde_json::Map::with_capacity(dict.len());
        for (key, value) in dict.iter() {
            let key_str: String = key.str()?.extract()?;
            map.insert(key_str, py_to_json(&value)?);
        }
        return Ok(JsonValue::Object(map));
    }
    // Fallback: anything else (tuples, custom objects, etc.) becomes
    // its string representation rather than failing the request.
    Ok(JsonValue::String(obj.str()?.extract()?))
}

/// Encode a Python object (dict/list/str/int/float/bool/None) to
/// pretty-printed JSON bytes using serde_json, bypassing Python's
/// `json` module on the hot path.
#[pyfunction]
fn json_dumps<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let value = py_to_json(obj)?;
    let encoded = serde_json::to_vec_pretty(&value)
        .map_err(|e| PyRuntimeError::new_err(format!("JSON encode error: {e}")))?;
    Ok(PyBytes::new_bound(py, &encoded))
}

// ---------------------------------------------------------------------
// Server (jet.server.serve) -- hand-written HTTP/1.1, std-only
// ---------------------------------------------------------------------
//
// No third-party HTTP crate at all -- just std::net and std::io. This
// is deliberate: two earlier attempts at wiring in an external async
// web framework (xitca-web) failed to compile because their exact
// builder/routing API couldn't be verified without a real cargo build
// in this environment, and Jet needs a catch-all "every path, every
// method" handler that isn't shown in any framework's quick-start
// docs. Parsing HTTP/1.1 request lines and headers by hand is well
// within std's documented, stable API -- nothing here is guessed.
//
// This intentionally supports a *practical subset* of HTTP/1.1: a
// request line, headers, and a Content-Length-delimited body.
// Chunked transfer-encoding, HTTP/2, keep-alive pipelining, and TLS
// are not implemented -- each response closes the connection
// (`Connection: close`), matching a simple dev-server rather than a
// production-grade edge server. That's an intentional scope
// boundary, not an oversight; note it if you deploy this behind
// something that expects keep-alive.
//
// Concurrency comes from two places: several OS *worker threads* in
// this process, each blocking on `TcpListener::accept()` in parallel
// (below), and several OS *worker processes* sharing the port via
// SO_REUSEPORT (jet/server.py spawns those). The GIL is only held for
// the moment the Python callback actually runs.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;

/// Bind a TCP listener with SO_REUSEPORT so multiple OS processes can
/// share the same port; the kernel load-balances connections between
/// them.
fn reuse_port_listener(host: &str, port: u16) -> std::io::Result<TcpListener> {
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, format!("{e}")))?;

    let socket = Socket::new(Domain::for_address(addr), Type::STREAM, None)?;
    socket.set_reuse_address(true)?;
    #[cfg(unix)]
    socket.set_reuse_port(true)?;
    socket.bind(&addr.into())?;
    socket.listen(1024)?;
    Ok(socket.into())
}

/// A parsed HTTP/1.1 request: method, path (including query string,
/// unparsed -- Python's Request class handles that split), headers,
/// and body.
struct ParsedRequest {
    method: String,
    path: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

#[derive(Debug)]
enum ParseError {
    ConnectionClosed,
    Malformed(String),
    Io(std::io::Error),
}

impl From<std::io::Error> for ParseError {
    fn from(e: std::io::Error) -> Self {
        ParseError::Io(e)
    }
}

/// Read and parse one HTTP/1.1 request from a buffered stream.
fn read_request(reader: &mut BufReader<&TcpStream>) -> Result<ParsedRequest, ParseError> {
    // Request line: "GET /path HTTP/1.1"
    let mut request_line = String::new();
    let bytes_read = reader.read_line(&mut request_line)?;
    if bytes_read == 0 {
        return Err(ParseError::ConnectionClosed);
    }
    let request_line = request_line.trim_end();
    let mut parts = request_line.split_whitespace();
    let method = parts
        .next()
        .ok_or_else(|| ParseError::Malformed("missing method".into()))?
        .to_string();
    let path = parts
        .next()
        .ok_or_else(|| ParseError::Malformed("missing path".into()))?
        .to_string();
    // HTTP version (parts.next()) is read but not otherwise used --
    // every response is written back as HTTP/1.1 regardless.

    // Headers: lines until a blank line.
    let mut headers = Vec::new();
    let mut content_length: usize = 0;
    loop {
        let mut line = String::new();
        let n = reader.read_line(&mut line)?;
        if n == 0 {
            return Err(ParseError::ConnectionClosed);
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            let name = name.trim().to_string();
            let value = value.trim().to_string();
            if name.eq_ignore_ascii_case("content-length") {
                content_length = value.parse().unwrap_or(0);
            }
            headers.push((name, value));
        }
    }

    // Body: exactly Content-Length bytes, if any.
    let mut body = vec![0u8; content_length];
    if content_length > 0 {
        reader.read_exact(&mut body)?;
    }

    Ok(ParsedRequest {
        method,
        path,
        headers,
        body,
    })
}

/// Reason phrase for common status codes; falls back to a generic
/// label for anything else so the response line is always well-formed.
fn reason_phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        301 => "Moved Permanently",
        302 => "Found",
        304 => "Not Modified",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        422 => "Unprocessable Entity",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        _ => "Status",
    }
}

/// Write an HTTP/1.1 response and close the connection.
fn write_response(
    stream: &mut TcpStream,
    status: u16,
    headers: &[(String, String)],
    body: &[u8],
) -> std::io::Result<()> {
    let mut out = Vec::with_capacity(128 + body.len());
    out.extend_from_slice(
        format!("HTTP/1.1 {} {}\r\n", status, reason_phrase(status)).as_bytes(),
    );

    let mut has_content_length = false;
    for (k, v) in headers {
        if k.eq_ignore_ascii_case("content-length") {
            has_content_length = true;
        }
        out.extend_from_slice(format!("{k}: {v}\r\n").as_bytes());
    }
    if !has_content_length {
        out.extend_from_slice(format!("Content-Length: {}\r\n", body.len()).as_bytes());
    }
    out.extend_from_slice(b"Connection: close\r\n");
    out.extend_from_slice(b"\r\n");
    out.extend_from_slice(body);

    stream.write_all(&out)?;
    stream.flush()
}

/// Handle one connection: parse the request, call into Python, write
/// the response.
fn handle_connection(mut stream: TcpStream, callback: &PyObject) {
    let peer_stream = match stream.try_clone() {
        Ok(s) => s,
        Err(_) => return,
    };
    let mut reader = BufReader::new(&peer_stream);

    let request = match read_request(&mut reader) {
        Ok(req) => req,
        Err(ParseError::ConnectionClosed) => return,
        Err(_) => {
            let _ = write_response(&mut stream, 400, &[], b"Bad Request");
            return;
        }
    };

    let response_data = Python::with_gil(|py| -> PyResult<(u16, Vec<(String, String)>, Vec<u8>)> {
        let headers_dict = PyDict::new_bound(py);
        for (k, v) in &request.headers {
            let _ = headers_dict.set_item(k, v);
        }
        let body_obj = PyBytes::new_bound(py, &request.body);

        let result = callback.call1(
            py,
            (request.method.as_str(), request.path.as_str(), headers_dict, body_obj),
        )?;
        result.extract(py)
    });

    match response_data {
        Ok((status, resp_headers, resp_body)) => {
            let _ = write_response(&mut stream, status, &resp_headers, &resp_body);
        }
        Err(err) => {
            Python::with_gil(|py| err.print(py));
            let _ = write_response(
                &mut stream,
                500,
                &[],
                b"Internal Server Error (jet_core)",
            );
        }
    }
}

/// Run the Jet dev server for this process. Blocks until interrupted.
/// Intended to be called once per worker process (see
/// `jet/server.py`), each with `reuse_port=True` so the OS
/// distributes connections across all of them.
#[pyfunction]
#[pyo3(signature = (host, port, callback, workers=8, reuse_port=true))]
fn serve_forever(
    py: Python<'_>,
    host: String,
    port: u16,
    callback: PyObject,
    workers: usize,
    reuse_port: bool,
) -> PyResult<()> {
    let listener = if reuse_port {
        reuse_port_listener(&host, port)
    } else {
        TcpListener::bind((host.as_str(), port))
    }
    .map_err(|e| PyRuntimeError::new_err(format!("Failed to bind {host}:{port}: {e}")))?;

    let listener = Arc::new(listener);
    let callback = Arc::new(callback);

    // Release the GIL while worker threads block on accept(); each
    // thread re-acquires the GIL only for the duration of the Python
    // callback call itself. `TcpListener::accept` takes `&self`, so
    // it's safe to call concurrently from multiple threads sharing
    // one `Arc<TcpListener>` -- the OS serializes actual handoff of
    // each connection to exactly one caller.
    py.allow_threads(move || {
        let mut handles = Vec::new();

        for _ in 0..workers.max(1) {
            let listener = Arc::clone(&listener);
            let callback = Arc::clone(&callback);

            handles.push(thread::spawn(move || loop {
                match listener.accept() {
                    Ok((stream, _addr)) => handle_connection(stream, &callback),
                    Err(_) => continue,
                }
            }));
        }

        for handle in handles {
            let _ = handle.join();
        }
    });

    Ok(())
}

// ---------------------------------------------------------------------
// Module definition
// ---------------------------------------------------------------------

#[pymodule]
fn jet_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add_function(wrap_pyfunction!(serve_forever, m)?)?;
    m.add_function(wrap_pyfunction!(json_dumps, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
