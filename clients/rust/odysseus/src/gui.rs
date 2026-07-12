//! Native egui client over the Terminal Client API.
//!
//! One persistent mpsc channel carries every async result (run stream, document
//! save) back to the UI thread; background threads wake egui with
//! `ctx.request_repaint()`. Quick reads (history, document open, document list)
//! run inline on click — they are single localhost GETs.

use std::sync::mpsc::{Receiver, Sender};

use crate::api::Client;
use crate::model::{Document, ModelInfo, Note, SessionSummary, Task};

enum Msg {
    Answer(String),
    Thinking(String),
    Tool(String),
    Error(String),
    Done(String), // session_id
    DocOpen { title: String, language: String },
    DocDelta(String),
    DocUpdate { id: String, content: String, version: i64, title: String, language: String },
    DocSaved(i64),
}

#[derive(PartialEq)]
enum Role {
    User,
    Assistant,
}

struct ChatMessage {
    role: Role,
    text: String,
    thinking: String,
    tools: Vec<String>,
}

struct DocState {
    id: Option<String>,
    title: String,
    language: String,
    content: String,
    version: i64,
    dirty: bool,
    saving: bool,
}

pub struct App {
    client: Client,
    owner: String,
    model: String,
    models: Vec<ModelInfo>,
    kind: String, // "chat" | "agent"
    session_id: Option<String>,
    sessions: Vec<SessionSummary>,
    documents: Vec<Document>,
    show_docs: bool,
    notes: Vec<Note>,
    notes_loaded: bool,
    tasks: Vec<Task>,
    tasks_loaded: bool,
    messages: Vec<ChatMessage>,
    doc: Option<DocState>,
    input: String,
    busy: bool,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
    status: String,
}

impl App {
    pub fn new(client: Client) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        let mut app = App {
            client,
            owner: String::new(),
            model: String::new(),
            models: Vec::new(),
            kind: "chat".to_string(),
            session_id: None,
            sessions: Vec::new(),
            documents: Vec::new(),
            show_docs: false,
            notes: Vec::new(),
            notes_loaded: false,
            tasks: Vec::new(),
            tasks_loaded: false,
            messages: Vec::new(),
            doc: None,
            input: String::new(),
            busy: false,
            tx,
            rx,
            status: String::new(),
        };
        match app.client.bootstrap() {
            Ok(b) => {
                app.owner = b.capabilities["owner"].as_str().unwrap_or("").to_string();
                app.sessions = b.sessions;
                app.models = b.models;
                app.model = b.default_model.unwrap_or_default();
                app.status = format!("connected to {}", app.client.base());
            }
            Err(e) => app.status = format!("bootstrap failed: {e}"),
        }
        app
    }

    fn open_session(&mut self, session_id: String) {
        self.messages.clear();
        self.session_id = Some(session_id.clone());
        if let Ok(hist) = self.client.history(&session_id) {
            for m in hist.history {
                let role = if m.role == "user" { Role::User } else { Role::Assistant };
                self.messages.push(ChatMessage {
                    role,
                    text: m.content.unwrap_or_default(),
                    thinking: String::new(),
                    tools: Vec::new(),
                });
            }
        }
    }

    fn open_document(&mut self, doc_id: &str) {
        if let Ok(d) = self.client.document_get(doc_id) {
            self.doc = Some(DocState {
                id: Some(d.id),
                title: d.title.unwrap_or_default(),
                language: d.language.unwrap_or_default(),
                content: d.current_content.unwrap_or_default(),
                version: d.version_count.unwrap_or(0),
                dirty: false,
                saving: false,
            });
        }
    }

    fn save_document(&mut self, ctx: &egui::Context) {
        let (id, content) = match &self.doc {
            Some(d) if d.id.is_some() => (d.id.clone().unwrap(), d.content.clone()),
            _ => return,
        };
        if let Some(d) = self.doc.as_mut() {
            d.saving = true;
        }
        let client = self.client.clone();
        let tx = self.tx.clone();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            match client.document_update(&id, &content) {
                Ok(d) => {
                    let _ = tx.send(Msg::DocSaved(d.version_count.unwrap_or(0)));
                }
                Err(e) => {
                    let _ = tx.send(Msg::Error(format!("save failed: {e}")));
                }
            }
            ctx.request_repaint();
        });
    }

    fn send(&mut self, ctx: &egui::Context) {
        let text = self.input.trim().to_string();
        if text.is_empty() || self.busy {
            return;
        }
        self.input.clear();
        self.messages.push(ChatMessage {
            role: Role::User,
            text: text.clone(),
            thinking: String::new(),
            tools: Vec::new(),
        });
        self.messages.push(ChatMessage {
            role: Role::Assistant,
            text: String::new(),
            thinking: String::new(),
            tools: Vec::new(),
        });
        self.busy = true;

        let client = self.client.clone();
        let kind = self.kind.clone();
        let model = self.model.clone();
        let session = self.session_id.clone();
        let tx = self.tx.clone();
        let ctx = ctx.clone();
        std::thread::spawn(move || run_stream(client, kind, model, text, session, tx, ctx));
    }

    fn drain(&mut self) {
        let mut msgs: Vec<Msg> = Vec::new();
        while let Ok(m) = self.rx.try_recv() {
            msgs.push(m);
        }
        for m in msgs {
            match m {
                Msg::Answer(t) => {
                    if let Some(msg) = self.messages.last_mut() {
                        msg.text.push_str(&t);
                    }
                }
                Msg::Thinking(t) => {
                    if let Some(msg) = self.messages.last_mut() {
                        msg.thinking.push_str(&t);
                    }
                }
                Msg::Tool(name) => {
                    if let Some(msg) = self.messages.last_mut() {
                        if !msg.tools.contains(&name) {
                            msg.tools.push(name);
                        }
                    }
                }
                Msg::Error(e) => {
                    self.status = e.clone();
                    if let Some(msg) = self.messages.last_mut() {
                        msg.text.push_str(&format!("\n[error] {e}"));
                    }
                    if let Some(d) = self.doc.as_mut() {
                        d.saving = false;
                    }
                }
                Msg::Done(sid) => {
                    self.session_id = Some(sid);
                    self.busy = false;
                }
                Msg::DocOpen { title, language } => {
                    self.doc = Some(DocState {
                        id: None,
                        title,
                        language,
                        content: String::new(),
                        version: 0,
                        dirty: false,
                        saving: false,
                    });
                }
                Msg::DocDelta(content) => {
                    if let Some(d) = self.doc.as_mut() {
                        d.content = content; // cumulative
                    }
                }
                Msg::DocUpdate { id, content, version, title, language } => {
                    self.doc = Some(DocState {
                        id: Some(id),
                        title,
                        language,
                        content,
                        version,
                        dirty: false,
                        saving: false,
                    });
                }
                Msg::DocSaved(version) => {
                    if let Some(d) = self.doc.as_mut() {
                        d.version = version;
                        d.dirty = false;
                        d.saving = false;
                    }
                    self.status = format!("document saved (v{version})");
                }
            }
        }
    }
}

fn run_stream(
    client: Client,
    kind: String,
    model: String,
    message: String,
    session: Option<String>,
    tx: Sender<Msg>,
    ctx: egui::Context,
) {
    let model = if model.is_empty() { None } else { Some(model.as_str()) };
    let run = match client.start_run(&kind, &message, session.as_deref(), model) {
        Ok(r) => r,
        Err(e) => {
            let _ = tx.send(Msg::Error(e.to_string()));
            ctx.request_repaint();
            return;
        }
    };
    let sid = run.run.session_id.clone();
    let tx2 = tx.clone();
    let ctx2 = ctx.clone();
    let _ = client.stream_run(&run.run.run_id, move |ev| {
        let p = &ev.payload;
        let send = |m: Msg| {
            let _ = tx2.send(m);
            ctx2.request_repaint();
        };
        match ev.kind.clone().unwrap_or_default().as_str() {
            "message.delta" => {
                if let Some((text, thinking)) = ev.delta() {
                    send(if thinking { Msg::Thinking(text.to_string()) } else { Msg::Answer(text.to_string()) });
                }
            }
            "tool_start" | "tool_output" => {
                let name = p["tool"].as_str().or_else(|| p["name"].as_str()).unwrap_or("tool").to_string();
                send(Msg::Tool(name));
            }
            "doc_stream_open" => send(Msg::DocOpen {
                title: p["title"].as_str().unwrap_or("").to_string(),
                language: p["language"].as_str().unwrap_or("").to_string(),
            }),
            "doc_stream_delta" => {
                if let Some(c) = p["content"].as_str() {
                    send(Msg::DocDelta(c.to_string()));
                }
            }
            "doc_update" => send(Msg::DocUpdate {
                id: p["doc_id"].as_str().unwrap_or("").to_string(),
                content: p["content"].as_str().unwrap_or("").to_string(),
                version: p["version"].as_i64().unwrap_or(0),
                title: p["title"].as_str().unwrap_or("").to_string(),
                language: p["language"].as_str().unwrap_or("").to_string(),
            }),
            "error" => send(Msg::Error(ev.summary.clone().unwrap_or_default())),
            _ => {}
        }
    });
    let _ = tx.send(Msg::Done(sid));
    ctx.request_repaint();
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain();

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading("Odysseus");
                ui.separator();
                egui::ComboBox::from_id_source("kind")
                    .selected_text(&self.kind)
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.kind, "chat".to_string(), "chat");
                        ui.selectable_value(&mut self.kind, "agent".to_string(), "agent");
                    });
                let model_label = if self.model.is_empty() { "default".to_string() } else { self.model.clone() };
                egui::ComboBox::from_id_source("model")
                    .selected_text(model_label)
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.model, String::new(), "default");
                        for m in &self.models {
                            ui.selectable_value(&mut self.model, m.model.clone(), &m.model);
                        }
                    });
                ui.separator();
                ui.label(format!("owner: {}", self.owner));
                if self.busy {
                    ui.spinner();
                }
            });
        });

        egui::SidePanel::left("nav").resizable(true).default_width(210.0).show(ctx, |ui| {
            if ui.button("＋ New conversation").clicked() {
                self.session_id = None;
                self.messages.clear();
            }
            ui.separator();
            ui.strong("Sessions");
            egui::ScrollArea::vertical().id_source("sess").max_height(220.0).show(ui, |ui| {
                let picks: Vec<(String, String)> = self
                    .sessions
                    .iter()
                    .map(|s| {
                        let name = if s.name.is_empty() { s.session_id.clone() } else { s.name.clone() };
                        (s.session_id.clone(), format!("{}  ({})", name, s.message_count))
                    })
                    .collect();
                for (sid, label) in picks {
                    if ui.selectable_label(self.session_id.as_deref() == Some(&sid), label).clicked() {
                        self.open_session(sid);
                    }
                }
            });

            ui.separator();
            egui::CollapsingHeader::new("Documents").show(ui, |ui| {
                if !self.show_docs {
                    if let Ok(list) = self.client.documents() {
                        self.documents = list.documents;
                    }
                    self.show_docs = true;
                }
                if ui.small_button("↻ refresh").clicked() {
                    if let Ok(list) = self.client.documents() {
                        self.documents = list.documents;
                    }
                }
                let picks: Vec<(String, String)> = self
                    .documents
                    .iter()
                    .map(|d| {
                        let t = d.title.clone().unwrap_or_else(|| d.id.clone());
                        (d.id.clone(), format!("{}  v{}", t, d.version_count.unwrap_or(0)))
                    })
                    .collect();
                for (id, label) in picks {
                    if ui.selectable_label(false, label).clicked() {
                        self.open_document(&id);
                    }
                }
            });

            egui::CollapsingHeader::new("Tasks").show(ui, |ui| {
                if !self.tasks_loaded {
                    if let Ok(list) = self.client.tasks() {
                        self.tasks = list.tasks;
                    }
                    self.tasks_loaded = true;
                }
                if self.tasks.is_empty() {
                    ui.weak("no tasks");
                }
                for t in &self.tasks {
                    let name = t.name.clone().unwrap_or_else(|| "(unnamed)".into());
                    let status = t.status.clone().unwrap_or_default();
                    ui.label(egui::RichText::new(format!("• {name}")).small());
                    ui.label(
                        egui::RichText::new(format!("   {} · {}", t.task_type.clone().unwrap_or_default(), status))
                            .weak()
                            .small(),
                    );
                }
            });

            egui::CollapsingHeader::new("Notes").show(ui, |ui| {
                if !self.notes_loaded {
                    if let Ok(list) = self.client.notes() {
                        self.notes = list.notes;
                    }
                    self.notes_loaded = true;
                }
                if self.notes.is_empty() {
                    ui.weak("no notes");
                }
                for n in &self.notes {
                    let title = n.title.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| "(untitled)".into());
                    let pin = if n.pinned { "📌 " } else { "" };
                    ui.label(egui::RichText::new(format!("{pin}{title}")).small().strong());
                    if let Some(c) = &n.content {
                        if !c.is_empty() {
                            let preview: String = c.chars().take(80).collect();
                            ui.label(egui::RichText::new(preview).weak().small());
                        }
                    }
                }
            });
        });

        // Right pane: the live/opened document (editable, save-back).
        if self.doc.is_some() {
            egui::SidePanel::right("doc").resizable(true).default_width(420.0).show(ctx, |ui| {
                let mut do_save = false;
                if let Some(d) = self.doc.as_mut() {
                    ui.horizontal(|ui| {
                        ui.strong(if d.title.is_empty() { "Document".to_string() } else { d.title.clone() });
                        ui.label(format!("v{}", d.version));
                        if !d.language.is_empty() {
                            ui.weak(&d.language);
                        }
                        if d.saving {
                            ui.spinner();
                        }
                        let can_save = d.dirty && d.id.is_some() && !d.saving;
                        if ui.add_enabled(can_save, egui::Button::new("Save")).clicked() {
                            do_save = true;
                        }
                    });
                    ui.separator();
                    egui::ScrollArea::vertical().id_source("docbody").show(ui, |ui| {
                        let resp = ui.add(
                            egui::TextEdit::multiline(&mut d.content)
                                .desired_width(f32::INFINITY)
                                .desired_rows(30)
                                .code_editor(),
                        );
                        if resp.changed() {
                            d.dirty = true;
                        }
                    });
                }
                if do_save {
                    self.save_document(ctx);
                }
            });
        }

        egui::TopBottomPanel::bottom("composer").show(ctx, |ui| {
            ui.horizontal(|ui| {
                let hint = if self.kind == "agent" { "message (agent)…" } else { "message…" };
                let resp = ui.add(
                    egui::TextEdit::singleline(&mut self.input)
                        .hint_text(hint)
                        .desired_width(f32::INFINITY),
                );
                if resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
                    self.send(ctx);
                    ui.memory_mut(|m| m.request_focus(resp.id));
                }
            });
            ui.label(egui::RichText::new(&self.status).weak().small());
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true).show(ui, |ui| {
                for msg in &self.messages {
                    let (who, color) = match msg.role {
                        Role::User => ("You", egui::Color32::from_rgb(120, 170, 255)),
                        Role::Assistant => ("Odysseus", egui::Color32::from_rgb(255, 130, 170)),
                    };
                    ui.add_space(6.0);
                    ui.label(egui::RichText::new(who).color(color).strong());
                    if !msg.thinking.is_empty() {
                        ui.label(egui::RichText::new(&msg.thinking).italics().weak());
                    }
                    for tool in &msg.tools {
                        ui.label(egui::RichText::new(format!("· {tool}")).weak().small());
                    }
                    let body = if msg.text.is_empty() && msg.role == Role::Assistant && self.busy {
                        "…".to_string()
                    } else {
                        msg.text.clone()
                    };
                    ui.label(body);
                }
            });
        });
    }
}
