//! HTTP client for the Odysseus Terminal Client API.
//!
//! Blocking (ureq) on purpose: the streaming NDJSON body is a plain
//! line-per-event `Read`, which maps cleanly to `BufRead::lines()`. Auth mirrors
//! the nvim client: `$ODY_NVIM_TOKEN` → `$ODY_TERM_TOKEN` → the `ody-term`
//! keychain entry. URL: `$ODY_TERM_URL` → `$ODYSSEUS_URL` → `:7860`.

use std::io::{BufRead, BufReader};

use serde_json::{json, Value};

use crate::model::{
    Bootstrap, Document, DocumentsList, Event, History, RunStartResponse, SessionsResponse,
};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Clone)]
pub struct Client {
    base: String,
    token: Option<String>,
    agent: ureq::Agent,
}

impl Client {
    pub fn from_env() -> Self {
        let base = std::env::var("ODY_TERM_URL")
            .or_else(|_| std::env::var("ODYSSEUS_URL"))
            .unwrap_or_else(|_| "http://127.0.0.1:7860".to_string());
        Client {
            base: base.trim_end_matches('/').to_string(),
            token: resolve_token(),
            agent: ureq::AgentBuilder::new().build(),
        }
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    pub fn has_token(&self) -> bool {
        self.token.is_some()
    }

    fn request(&self, method: &str, path: &str) -> ureq::Request {
        let mut req = self
            .agent
            .request(method, &format!("{}{}", self.base, path))
            // The server's gzip middleware mangles the NDJSON stream unless we
            // ask for identity (see the earlier server fix); always opt out.
            .set("Accept-Encoding", "identity");
        if let Some(token) = &self.token {
            req = req.set("Authorization", &format!("Bearer {}", token));
        }
        req
    }

    pub fn capabilities(&self) -> Result<Value> {
        Ok(self.request("GET", "/api/terminal/capabilities").call()?.into_json()?)
    }

    pub fn sessions(&self) -> Result<SessionsResponse> {
        Ok(self.request("GET", "/api/terminal/sessions").call()?.into_json()?)
    }

    /// One request to render the initial UI: capabilities + sessions + models.
    pub fn bootstrap(&self) -> Result<Bootstrap> {
        Ok(self.request("GET", "/api/terminal/bootstrap").call()?.into_json()?)
    }

    /// Persisted conversation history for a session.
    pub fn history(&self, session_id: &str) -> Result<History> {
        Ok(self
            .request("GET", &format!("/api/terminal/sessions/{}/history", session_id))
            .call()?
            .into_json()?)
    }

    /// Start a run. `kind` is "chat" or "agent". Reuse `session_id` for context;
    /// pass `model` to pin a specific model (skips the default-model fallback).
    pub fn start_run(
        &self,
        kind: &str,
        message: &str,
        session_id: Option<&str>,
        model: Option<&str>,
    ) -> Result<RunStartResponse> {
        let mut body = json!({ "kind": kind, "message": message });
        if let Some(sid) = session_id {
            body["session_id"] = json!(sid);
        }
        if let Some(m) = model {
            if !m.is_empty() {
                body["model"] = json!(m);
            }
        }
        Ok(self
            .request("POST", "/api/terminal/runs")
            .send_json(body)?
            .into_json()?)
    }

    pub fn documents(&self) -> Result<DocumentsList> {
        Ok(self.request("GET", "/api/terminal/documents").call()?.into_json()?)
    }

    pub fn document_get(&self, doc_id: &str) -> Result<Document> {
        Ok(self
            .request("GET", &format!("/api/terminal/documents/{}", doc_id))
            .call()?
            .into_json()?)
    }

    pub fn document_update(&self, doc_id: &str, content: &str) -> Result<Document> {
        Ok(self
            .request("PUT", &format!("/api/terminal/documents/{}", doc_id))
            .send_json(json!({ "content": content, "summary": "Edited in Rust GUI" }))?
            .into_json()?)
    }

    /// Follow a run's event stream, invoking `on_event` per `ody.event.v1` line
    /// until the stream closes.
    pub fn stream_run(&self, run_id: &str, mut on_event: impl FnMut(Event)) -> Result<()> {
        let resp = self
            .request("GET", &format!("/api/terminal/events/stream?run_id={}", run_id))
            .call()?;
        let reader = BufReader::new(resp.into_reader());
        for line in reader.lines() {
            let line = line?;
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<Event>(line) {
                Ok(ev) => on_event(ev),
                Err(_) => { /* tolerate a partial/foreign line */ }
            }
        }
        Ok(())
    }
}

fn resolve_token() -> Option<String> {
    for var in ["ODY_NVIM_TOKEN", "ODY_TERM_TOKEN"] {
        if let Ok(val) = std::env::var(var) {
            if !val.is_empty() {
                return Some(val);
            }
        }
    }
    // macOS keychain, same slot ody-term uses.
    let out = std::process::Command::new("security")
        .args(["find-generic-password", "-s", "ody-term", "-a", "ody-term/default", "-w"])
        .output()
        .ok()?;
    if out.status.success() {
        let token = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !token.is_empty() {
            return Some(token);
        }
    }
    None
}
