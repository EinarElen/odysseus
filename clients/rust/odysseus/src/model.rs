//! Wire types for the Terminal Client API (`ody.event.v1` + run responses).
//!
//! `payload` stays a `serde_json::Value` on purpose: the normalized envelope is
//! typed, but per-kind payloads are still loose on the server side, so a client
//! reads the fields it knows about and ignores the rest.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct Event {
    pub schema: Option<String>,
    pub seq: Option<i64>,
    pub kind: Option<String>,
    pub level: Option<String>,
    pub summary: Option<String>,
    pub run_id: Option<String>,
    pub session_id: Option<String>,
    #[serde(default)]
    pub payload: serde_json::Value,
}

impl Event {
    /// Text of a `message.delta` (answer or thinking token), if any.
    pub fn delta(&self) -> Option<(&str, bool)> {
        let text = self
            .payload
            .get("delta")
            .or_else(|| self.payload.get("text"))
            .or_else(|| self.payload.get("content"))
            .and_then(|v| v.as_str())?;
        let thinking = self
            .payload
            .get("thinking")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        Some((text, thinking))
    }
}

#[derive(Debug, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    pub session_id: String,
    #[serde(default)]
    pub status: String,
}

#[derive(Debug, Deserialize)]
pub struct RunStartResponse {
    pub run: RunSummary,
}

#[derive(Debug, Deserialize)]
pub struct SessionSummary {
    pub session_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub message_count: i64,
}

#[derive(Debug, Deserialize)]
pub struct SessionsResponse {
    #[serde(default)]
    pub sessions: Vec<SessionSummary>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModelInfo {
    pub model: String,
    #[serde(default)]
    pub endpoint_url: String,
    #[serde(default)]
    pub endpoint_name: String,
}

/// One-call init payload from `GET /api/terminal/bootstrap`.
#[derive(Debug, Deserialize)]
pub struct Bootstrap {
    #[serde(default)]
    pub capabilities: serde_json::Value,
    #[serde(default)]
    pub sessions: Vec<SessionSummary>,
    #[serde(default)]
    pub models: Vec<ModelInfo>,
    pub default_model: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct HistoryMessage {
    pub role: String,
    #[serde(default)]
    pub content: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct History {
    #[serde(default)]
    pub history: Vec<HistoryMessage>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Document {
    pub id: String,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub current_content: Option<String>,
    #[serde(default)]
    pub version_count: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct DocumentsList {
    #[serde(default)]
    pub documents: Vec<Document>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Note {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub note_type: Option<String>,
    #[serde(default)]
    pub pinned: bool,
}

#[derive(Debug, Deserialize)]
pub struct NotesList {
    #[serde(default)]
    pub notes: Vec<Note>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Task {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub task_type: Option<String>,
    #[serde(default)]
    pub last_run: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct TasksList {
    #[serde(default)]
    pub tasks: Vec<Task>,
}
