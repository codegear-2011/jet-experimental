//! jet_core
//!
//! Jet's performance-critical core, written in Rust and exposed to
//! Python through PyO3 0.29, built as an **abi3** extension -- one
//! compiled wheel works unmodified on **any standard CPython 3.8+**.
//! No special interpreter build required.
//!
//! Three pieces live here:
//!
//! 1. `Router`        -- compiled path matching (used by jet.router)
//! 2. `json_dumps`     -- Rust-side JSON encoding (used by jet.response)
//! 3. `serve_forever`  -- an async, non-blocking xitca-web/Tokio server
//!                        (used by jet.server)
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
//! 2. **A GIL-free I/O layer, per process.** Inside each process, the
//!    entire accept/parse/serialize path runs in xitca-web on Tokio's
//!    async reactor -- no Python bytecode executes during any of it,
//!    so it's outside the GIL's reach entirely. The GIL is acquired
//!    for the *shortest possible window*: only for the moment your
//!    Python handler (`app.handle`) actually runs, via
//!    `tokio::task::spawn_blocking`. Everything else -- header
//!    parsing, response building, JSON encoding -- happens GIL-free.
//!
//! `Router`'s internal state is `Mutex`-guarded rather than relying
//! on the GIL for synchronization. This costs nothing on ordinary
//! Python (the mutex is uncontended almost all the time) and is a
//! free bonus of extra safety if you happen to run this on a Python
//! 3.13/3.14 free-threaded build, where the GIL genuinely isn't there
//! to protect shared state -- but that build is not required.
//!
//! ## A note on verification
//!
//! This crate targets xitca-web 0.8.1's public API and PyO3 0.29.0 as
//! documented; it was written without the ability to `cargo build` or
//! fetch crates.io docs in the environment that generated it. If
//! xitca-web's `HttpServer`/`fn_service` surface has shifted slightly
//! from what's used below, the fix is almost always confined to the
//! `serve_forever` function -- the `Router` and `json_dumps` pieces
//! don't depend on it at all.

use bytes::Bytes;
use futures_util::StreamExt;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString};
use serde_json::Value as JsonValue;
use socket2::{Domain, Socket, Type};
use std::net::SocketAddr;
use std::sync::Mutex;
use xitca_web::{
    body::{RequestBody, ResponseBody},
    bytes::Bytes as XitcaBytes,
    http::{Request, Response, StatusCode},
    service::fn_service,
    HttpServer,
};

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
// Async server (jet.server.serve) -- xitca-web / Tokio
// ---------------------------------------------------------------------

/// Bind a TCP listener with SO_REUSEPORT so multiple OS processes can
/// share the same port; the kernel load-balances connections between
/// them.
fn reuse_port_listener(host: &str, port: u16) -> std::io::Result<std::net::TcpListener> {
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, format!("{e}")))?;

    let socket = Socket::new(Domain::for_address(addr), Type::STREAM, None)?;
    socket.set_reuse_address(true)?;
    #[cfg(unix)]
    socket.set_reuse_port(true)?;
    socket.set_nonblocking(true)?;
    socket.bind(&addr.into())?;
    socket.listen(1024)?;
    Ok(socket.into())
}

/// Collect a xitca-web request body into a single `Bytes` buffer.
async fn collect_body(mut body: RequestBody) -> Bytes {
    let mut buf = Vec::new();
    while let Some(chunk) = body.next().await {
        if let Ok(chunk) = chunk {
            buf.extend_from_slice(&chunk);
        } else {
            break;
        }
    }
    Bytes::from(buf)
}

/// The single request handler: every path and method funnels through
/// here (Jet does its own routing in Python/`Router`, so xitca-web is
/// used purely as the async accept/parse layer, not as a router).
async fn handle_request(
    callback: PyObject,
    req: Request<RequestBody>,
) -> Result<Response<ResponseBody>, std::convert::Infallible> {
    let method = req.method().as_str().to_string();
    let url = req.uri().to_string();

    let mut headers = Vec::new();
    for (name, value) in req.headers().iter() {
        if let Ok(v) = value.to_str() {
            headers.push((name.as_str().to_string(), v.to_string()));
        }
    }

    let body_bytes = collect_body(req.into_body()).await;

    // Free-threaded Python has no GIL to release/reacquire around
    // this call, but `Python::with_gil` remains a correct no-op-ish
    // entry point on both build flavors -- PyO3 handles the
    // difference internally.
    let result = tokio::task::spawn_blocking(move || {
        Python::with_gil(|py| -> PyResult<(u16, Vec<(String, String)>, Vec<u8>)> {
            let headers_dict = PyDict::new_bound(py);
            for (k, v) in &headers {
                let _ = headers_dict.set_item(k, v);
            }
            let body_obj = PyBytes::new_bound(py, &body_bytes);

            let result = callback.call1(
                py,
                (method.as_str(), url.as_str(), headers_dict, body_obj),
            )?;
            result.extract(py)
        })
    })
    .await;

    let response = match result {
        Ok(Ok((status, resp_headers, resp_body))) => {
            let mut builder = Response::builder().status(
                StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            );
            for (k, v) in resp_headers {
                builder = builder.header(k, v);
            }
            builder
                .body(ResponseBody::from(XitcaBytes::from(resp_body)))
                .unwrap_or_else(|_| {
                    Response::builder()
                        .status(StatusCode::INTERNAL_SERVER_ERROR)
                        .body(ResponseBody::from(XitcaBytes::from_static(
                            b"jet_core: failed to build response",
                        )))
                        .expect("static fallback response must build")
                })
        }
        Ok(Err(err)) => {
            Python::with_gil(|py| err.print(py));
            Response::builder()
                .status(StatusCode::INTERNAL_SERVER_ERROR)
                .body(ResponseBody::from(XitcaBytes::from_static(
                    b"Internal Server Error (jet_core)",
                )))
                .expect("static error response must build")
        }
        Err(join_err) => {
            eprintln!("[jet_core] worker task panicked: {join_err}");
            Response::builder()
                .status(StatusCode::INTERNAL_SERVER_ERROR)
                .body(ResponseBody::from(XitcaBytes::from_static(
                    b"Internal Server Error (jet_core)",
                )))
                .expect("static error response must build")
        }
    };

    Ok(response)
}

/// Run the async Jet dev server for this process. Blocks until
/// interrupted. Intended to be called once per worker process (see
/// `jet/server.py`), each with `reuse_port=True` so the OS
/// distributes connections across all of them.
#[pyfunction]
#[pyo3(signature = (host, port, callback, workers=4, reuse_port=true))]
fn serve_forever(
    py: Python<'_>,
    host: String,
    port: u16,
    callback: PyObject,
    workers: usize,
    reuse_port: bool,
) -> PyResult<()> {
    py.allow_threads(move || {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(workers.max(1))
            .enable_all()
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to start Tokio runtime: {e}")))?;

        runtime.block_on(async move {
            let std_listener = if reuse_port {
                reuse_port_listener(&host, port)
            } else {
                std::net::TcpListener::bind((host.as_str(), port))
            }
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to bind {host}:{port}: {e}")))?;

            HttpServer::new(move || {
                let callback = Python::with_gil(|py| callback.clone_ref(py));
                fn_service(move |req: Request<RequestBody>| {
                    let callback = Python::with_gil(|py| callback.clone_ref(py));
                    handle_request(callback, req)
                })
            })
            .listen(std_listener)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to start server: {e}")))?
            .run()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Server error: {e}")))
        })
    })
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
