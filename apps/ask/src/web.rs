//! The one local address the window talks to (http://127.0.0.1:8720), so the page never needs to know where the
//! office Library or the model live:
//!   /local/*  this app: status, model download, settings, saved chats
//!   /llm/*    the model on this PC
//!   the page itself (index.html, app.js, …) is built into this app, so it and the saved chats appear at once,
//!   even while the Library is still starting or out of reach
//!   the rest  the office Library
use std::convert::Infallible;
use std::net::TcpListener;
use std::sync::{LazyLock, Mutex, OnceLock};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{DefaultBodyLimit, Path, Request};
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post};
use axum::{Json, Router};
use include_dir::{include_dir, Dir};
use serde_json::{json, Value};

use crate::model;

static WEB: Dir = include_dir!("$CARGO_MANIFEST_DIR/../../web");
static PORT: OnceLock<u16> = OnceLock::new();
static CHATS_LOCK: Mutex<()> = Mutex::new(());
static CLIENT: LazyLock<reqwest::Client> = LazyLock::new(|| {
    reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(600))
        .build()
        .expect("http client")
});
const HOP: [&str; 8] = ["host", "connection", "content-length", "transfer-encoding", "accept-encoding", "keep-alive", "origin", "referer"];
const CUT: &[u8] = b"\ndata: {\"error\": \"The answer was cut off.\"}\n\n";

pub fn chats_file() -> std::path::PathBuf {
    model::DATA.join("chats").join("chats.json")
}

/// The usual port, or the next free one. Windows sometimes reserves a block of ports after a restart (Hyper-V,
/// WSL, Docker); the old launcher then could not start at all. The saved chats no longer depend on the port.
pub fn bind() -> std::io::Result<TcpListener> {
    let first = *model::PORT;
    let mut last = None;
    for p in (first..first.saturating_add(10)).chain([0]) {
        match TcpListener::bind(("127.0.0.1", p)) {
            Ok(l) => {
                if p != first {
                    model::log(&format!("port {first} was not free; using {}", l.local_addr()?.port()));
                }
                return Ok(l);
            }
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap())
}

pub async fn serve(listener: TcpListener) {
    let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
    let _ = PORT.set(port);
    let _ = listener.set_nonblocking(true);
    let Ok(listener) = tokio::net::TcpListener::from_std(listener) else { return };
    let app = Router::new()
        .route("/local/status", get(status))
        .route("/local/discover", post(discover))
        .route("/local/download", post(download))
        .route("/local/retune", post(retune))
        .route("/local/unload", post(unload))
        .route("/local/settings", post(settings))
        .route("/local/chats", get(chats_get).put(chats_put))
        .route("/llm/{*rest}", any(llm))
        .fallback(fallback)
        .layer(DefaultBodyLimit::max(64 << 20))
        .layer(middleware::from_fn(guard));
    if let Err(e) = axum::serve(listener, app).await {
        model::log(&format!("local server stopped: {e}"));
    }
}

fn err(code: StatusCode, msg: impl Into<String>) -> Response {
    (code, Json(json!({"error": msg.into()}))).into_response()
}

/// Only this PC's own DexMentor Ask window may talk to this address: a web page open in a browser must not be
/// able to repoint it, start downloads or read the saved chats ("DNS rebinding" included).
async fn guard(req: Request, next: Next) -> Response {
    let port = PORT.get().copied().unwrap_or(0);
    let mine = |h: &str| h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}");
    let host = req.headers().get(header::HOST).and_then(|h| h.to_str().ok()).unwrap_or("");
    let origin = req.headers().get(header::ORIGIN).and_then(|h| h.to_str().ok());
    if !mine(host) || origin.is_some_and(|o| !mine(o.split("://").last().unwrap_or(""))) {
        return err(StatusCode::FORBIDDEN, "Not allowed.");
    }
    next.run(req).await
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    tokio::task::spawn_blocking(f).await.expect("worker")
}

async fn status() -> Json<Value> {
    Json(blocking(model::status).await)
}

async fn discover() -> Json<Value> {
    Json(blocking(|| json!({"connected": model::auto_connect(), "server_url": model::server_url()})).await)
}

async fn download() -> Json<Value> {
    std::thread::spawn(model::download);
    Json(json!({"ok": true}))
}

/// "Test the graphics card", pressed by the engineer.
async fn retune() -> Json<Value> {
    std::thread::spawn(model::test_gpu);
    Json(json!({"ok": true}))
}

async fn unload() -> Json<Value> {
    blocking(model::stop_model).await;
    Json(json!({"ok": true}))
}

async fn settings(body: Bytes) -> Response {
    let v: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let url = v["server_url"].as_str().unwrap_or("").trim().trim_end_matches('/').to_string();
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return err(StatusCode::BAD_REQUEST, "The address must start with http:// or https://");
    }
    blocking(move || model::set_server(&url)).await;
    Json(json!({"ok": true})).into_response()
}

async fn chats_get() -> Response {
    let v = blocking(|| {
        let _one = CHATS_LOCK.lock().unwrap();
        dexwin::chats::load(&chats_file())
    })
    .await;
    Json(v).into_response()
}

async fn chats_put(body: Bytes) -> Response {
    let Ok(v) = serde_json::from_slice::<Value>(&body) else {
        return err(StatusCode::BAD_REQUEST, "That is not a chat history.");
    };
    let res = blocking(move || {
        let _one = CHATS_LOCK.lock().unwrap();
        dexwin::chats::save(&chats_file(), &v)
    })
    .await;
    match res {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => {
            model::log(&format!("chats not saved: {e}"));
            err(StatusCode::INTERNAL_SERVER_ERROR, format!("The chats could not be saved ({e})."))
        }
    }
}

async fn llm(Path(rest): Path<String>, req: Request) -> Response {
    // The higher CPU ceiling only while the model loads and writes; back to the idle one after.
    let hold = model::BOOST.hold();
    if let Err(e) = blocking(model::ensure_model).await {
        return err(StatusCode::SERVICE_UNAVAILABLE, e);
    }
    let q = req.uri().query().map(|q| format!("?{q}")).unwrap_or_default();
    let base = format!("http://127.0.0.1:{}", *model::LLM_PORT);
    proxy(&base, &format!("/{rest}{q}"), req, Some(hold)).await
}

async fn fallback(req: Request) -> Response {
    if matches!(*req.method(), Method::GET | Method::HEAD) {
        let p = req.uri().path().trim_start_matches('/');
        let p = if p.is_empty() { "index.html" } else { p };
        if let Some(f) = WEB.get_file(p) {
            let ty = match p.rsplit('.').next().unwrap_or("") {
                "html" => "text/html; charset=utf-8",
                "js" => "text/javascript; charset=utf-8",
                "css" => "text/css; charset=utf-8",
                "json" | "webmanifest" => "application/json",
                "png" => "image/png",
                "svg" => "image/svg+xml",
                "ico" => "image/x-icon",
                "woff2" => "font/woff2",
                "jpg" => "image/jpeg",
                _ => "application/octet-stream",
            };
            return ([(header::CONTENT_TYPE, ty), (header::CACHE_CONTROL, "no-cache")], f.contents()).into_response();
        }
    }
    let path = req.uri().path_and_query().map(|p| p.as_str().to_string()).unwrap_or_else(|| "/".into());
    proxy(&model::server_url(), &path, req, None).await
}

/// Passes a request on and streams the answer back as it arrives (answers are written word by word).
async fn proxy(base: &str, path: &str, req: Request, hold: Option<dexwin::job::BoostGuard>) -> Response {
    let is_llm = hold.is_some();
    let (parts, body) = req.into_parts();
    let body = match axum::body::to_bytes(body, 64 << 20).await {
        Ok(b) => b,
        Err(_) => return err(StatusCode::PAYLOAD_TOO_LARGE, "Request too large."),
    };
    let mut headers = HeaderMap::new();
    for (k, v) in parts.headers.iter() {
        if !HOP.contains(&k.as_str()) {
            headers.insert(k.clone(), v.clone());
        }
    }
    let url = format!("{}{}", base.trim_end_matches('/'), path);
    let mut rb = CLIENT.request(parts.method.clone(), &url).headers(headers);
    if !body.is_empty() {
        rb = rb.body(body);
    }
    let r = match rb.send().await {
        Ok(r) => r,
        Err(e) => return err(StatusCode::BAD_GATEWAY, format!("Could not reach {base} ({e})")),
    };
    let mut out = Response::builder().status(r.status().as_u16());
    for (k, v) in r.headers() {
        if !HOP.contains(&k.as_str()) {
            out = out.header(k, v);
        }
    }
    // The boost (if any) travels with the stream and ends when the last word has been sent.
    let stream = futures_util::stream::unfold(Some((r, hold)), move |st| async move {
        let (mut r, hold) = st?;
        match r.chunk().await {
            Ok(Some(b)) => {
                if is_llm {
                    model::touch();
                }
                Some((Ok::<Bytes, Infallible>(b), Some((r, hold))))
            }
            Ok(None) => None,
            Err(_) => Some((Ok(Bytes::from_static(CUT)), None)), // the far end died part-way through its reply
        }
    });
    out.header(header::CONNECTION, HeaderValue::from_static("close"))
        .body(Body::from_stream(stream))
        .unwrap_or_else(|_| err(StatusCode::BAD_GATEWAY, "Bad answer from the far end."))
}
