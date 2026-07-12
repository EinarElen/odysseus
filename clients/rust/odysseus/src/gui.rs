//! Native egui client over the Terminal Client API.
//!
//! One persistent mpsc channel carries every async result (run stream, document
//! save) back to the UI thread; background threads wake egui with
//! `ctx.request_repaint()`. Quick reads (history, document open, document list)
//! run inline on click — they are single localhost GETs.

use std::sync::mpsc::{Receiver, Sender};

use egui_commonmark::{CommonMarkCache, CommonMarkViewer};

use crate::api::Client;
use crate::model::{Document, ModelInfo, Note, SessionSummary, Task};
use crate::theme;

enum Msg {
    Answer(String),
    Thinking(String),
    Error(String),
    Done(String), // session_id
    Plan(String), // proposed/updated checklist (markdown)
    Ask { question: String, options: Vec<String>, multi: bool },
    ToolStart { name: String, command: String },
    ToolEnd { output: String, exit_code: i64 },
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

/// One agent tool invocation, rendered as an expandable card in the bubble.
struct ToolCall {
    name: String,
    command: String,
    output: String,
    exit_code: i64,
    done: bool,
}

struct ChatMessage {
    role: Role,
    text: String,
    thinking: String,
    tools: Vec<ToolCall>,
}

/// An outstanding `ask_user` prompt: the agent ended its turn awaiting a choice,
/// which we send back as the next message.
struct AskState {
    question: String,
    options: Vec<String>,
    multi: bool,
    selected: Vec<bool>,
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
    plan_mode: bool,
    plan: Option<String>,
    awaiting_plan: bool,
    ask: Option<AskState>,
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
    md_cache: CommonMarkCache,
    focused_once: bool,
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
            plan_mode: false,
            plan: None,
            awaiting_plan: false,
            ask: None,
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
            md_cache: CommonMarkCache::default(),
            focused_once: false,
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
                let thinking = m.metadata["thinking"].as_str().unwrap_or("").to_string();
                let tools = m.metadata["tool_events"]
                    .as_array()
                    .map(|arr| arr.iter().map(tool_from_event).collect())
                    .unwrap_or_default();
                self.messages.push(ChatMessage {
                    role,
                    text: m.content.unwrap_or_default(),
                    thinking,
                    tools,
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
        // Plan mode only applies to agent runs.
        let plan_mode = self.plan_mode && self.kind == "agent";
        self.spawn_run(ctx, text, plan_mode, None);
    }

    /// Re-run the same session with the approved checklist so the agent executes it.
    fn approve_plan(&mut self, ctx: &egui::Context) {
        let plan = match self.plan.take() {
            Some(p) => p,
            None => return,
        };
        self.kind = "agent".to_string();
        self.spawn_run(ctx, "Proceed with the approved plan.".to_string(), false, Some(plan));
    }

    /// Answer an outstanding ask_user prompt: the choice goes back as the next
    /// message in the same session (the agent's turn resumes from there).
    fn answer_ask(&mut self, ctx: &egui::Context, answer: String) {
        self.ask = None;
        self.spawn_run(ctx, answer, false, None);
    }

    fn spawn_run(&mut self, ctx: &egui::Context, text: String, plan_mode: bool, approved_plan: Option<String>) {
        if self.busy {
            return;
        }
        self.awaiting_plan = plan_mode;
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
        std::thread::spawn(move || {
            run_stream(client, kind, model, text, session, plan_mode, approved_plan, tx, ctx)
        });
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
                Msg::ToolStart { name, command } => {
                    if let Some(msg) = self.messages.last_mut() {
                        msg.tools.push(ToolCall {
                            name,
                            command,
                            output: String::new(),
                            exit_code: 0,
                            done: false,
                        });
                    }
                }
                Msg::ToolEnd { output, exit_code } => {
                    if let Some(msg) = self.messages.last_mut() {
                        // Fill the most recent still-running tool card.
                        if let Some(tc) = msg.tools.iter_mut().rev().find(|t| !t.done) {
                            tc.output = output;
                            tc.exit_code = exit_code;
                            tc.done = true;
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
                    // Plan mode ends the turn without executing; if the model
                    // didn't call update_plan, its answer text *is* the plan.
                    if self.awaiting_plan && self.plan.is_none() {
                        if let Some(msg) = self.messages.last() {
                            if msg.role == Role::Assistant && !msg.text.trim().is_empty() {
                                self.plan = Some(msg.text.clone());
                            }
                        }
                    }
                    self.awaiting_plan = false;
                }
                Msg::Plan(p) => {
                    self.plan = Some(p);
                }
                Msg::Ask { question, options, multi } => {
                    let selected = vec![false; options.len()];
                    self.ask = Some(AskState { question, options, multi, selected });
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

#[allow(clippy::too_many_arguments)]
fn run_stream(
    client: Client,
    kind: String,
    model: String,
    message: String,
    session: Option<String>,
    plan_mode: bool,
    approved_plan: Option<String>,
    tx: Sender<Msg>,
    ctx: egui::Context,
) {
    let model = if model.is_empty() { None } else { Some(model.as_str()) };
    let opts = crate::api::RunOptions {
        session_id: session.as_deref(),
        model,
        plan_mode,
        approved_plan: approved_plan.as_deref(),
    };
    let run = match client.start_run(&kind, &message, opts) {
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
            "tool_start" => {
                let name = p["tool"].as_str().or_else(|| p["name"].as_str()).unwrap_or("tool").to_string();
                let command = p["command"]
                    .as_str()
                    .or_else(|| p["input"].as_str())
                    .unwrap_or("")
                    .to_string();
                send(Msg::ToolStart { name, command });
            }
            "tool_output" => {
                let output = p["output"].as_str().unwrap_or("").to_string();
                let exit_code = p["exit_code"].as_i64().unwrap_or(0);
                send(Msg::ToolEnd { output, exit_code });
            }
            "plan_update" => {
                if let Some(plan) = p["plan"].as_str() {
                    send(Msg::Plan(plan.to_string()));
                }
            }
            "ask_user" => {
                let options = p["options"]
                    .as_array()
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|o| o["label"].as_str().map(str::to_string))
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                send(Msg::Ask {
                    question: p["question"].as_str().unwrap_or("").to_string(),
                    options,
                    multi: p["multi"].as_bool().unwrap_or(false),
                });
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

        let top_frame = egui::Frame::none()
            .fill(theme::PANEL)
            .inner_margin(egui::Margin::symmetric(14.0, 9.0));
        egui::TopBottomPanel::top("top").frame(top_frame).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("◆ odysseus").heading().color(theme::FG).strong());
                ui.add_space(12.0);
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
                if self.kind == "agent" {
                    ui.checkbox(&mut self.plan_mode, "plan")
                        .on_hover_text("Propose a plan and wait for approval before executing");
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if self.busy {
                        ui.spinner();
                    }
                    let dot = if self.owner.is_empty() { theme::RED } else { theme::GREEN };
                    let (r, _) = ui.allocate_exact_size(egui::vec2(9.0, 9.0), egui::Sense::hover());
                    ui.painter().circle_filled(r.center(), 4.0, dot);
                    let who = if self.owner.is_empty() { "offline" } else { self.owner.as_str() };
                    ui.label(egui::RichText::new(who).color(theme::MUTED).small());
                });
            });
        });

        // Proposed plan: floating window with the checklist + approve/dismiss.
        if self.plan.is_some() {
            let mut approve = false;
            let mut dismiss = false;
            egui::Window::new("Proposed plan")
                .collapsible(true)
                .default_width(460.0)
                .anchor(egui::Align2::CENTER_TOP, [0.0, 60.0])
                .show(ctx, |ui| {
                    if let Some(plan) = &self.plan {
                        egui::ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
                            CommonMarkViewer::new().show(ui, &mut self.md_cache, plan);
                        });
                    }
                    ui.separator();
                    ui.horizontal(|ui| {
                        let go = egui::Button::new(egui::RichText::new("✔ Approve & run").color(theme::BG).strong())
                            .fill(theme::GREEN);
                        if ui.add_enabled(!self.busy, go).clicked() {
                            approve = true;
                        }
                        if ui.button("Dismiss").clicked() {
                            dismiss = true;
                        }
                    });
                });
            if approve {
                self.approve_plan(ctx);
            } else if dismiss {
                self.plan = None;
            }
        }

        // Outstanding ask_user prompt: render the choices as buttons/checkboxes.
        if self.ask.is_some() {
            let mut answer: Option<String> = None;
            egui::Window::new("The agent asks")
                .collapsible(false)
                .default_width(420.0)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .show(ctx, |ui| {
                    let ask = self.ask.as_mut().unwrap();
                    ui.label(egui::RichText::new(&ask.question).strong());
                    ui.separator();
                    if ask.multi {
                        for (i, opt) in ask.options.iter().enumerate() {
                            ui.checkbox(&mut ask.selected[i], opt);
                        }
                        let any = ask.selected.iter().any(|s| *s);
                        if ui.add_enabled(any && !self.busy, egui::Button::new("Submit")).clicked() {
                            let chosen: Vec<String> = ask
                                .options
                                .iter()
                                .zip(&ask.selected)
                                .filter(|(_, s)| **s)
                                .map(|(o, _)| o.clone())
                                .collect();
                            answer = Some(chosen.join(", "));
                        }
                    } else {
                        for opt in &ask.options {
                            if ui.add_enabled(!self.busy, egui::Button::new(opt)).clicked() {
                                answer = Some(opt.clone());
                            }
                        }
                    }
                });
            if let Some(a) = answer {
                self.answer_ask(ctx, a);
            }
        }

        let nav_frame = egui::Frame::none()
            .fill(theme::PANEL)
            .inner_margin(egui::Margin::symmetric(10.0, 12.0));
        egui::SidePanel::left("nav")
            .resizable(true)
            .default_width(220.0)
            .frame(nav_frame)
            .show(ctx, |ui| {
            let new_btn = egui::Button::new(egui::RichText::new("＋  New conversation").color(theme::BG).strong())
                .fill(theme::FG)
                .min_size(egui::vec2(ui.available_width(), 30.0));
            if ui.add(new_btn).clicked() {
                self.session_id = None;
                self.messages.clear();
                self.plan = None;
                self.ask = None;
            }
            ui.add_space(10.0);
            ui.label(egui::RichText::new("SESSIONS").color(theme::MUTED).small().strong());
            ui.add_space(2.0);
            egui::ScrollArea::vertical().id_source("sess").max_height(240.0).show(ui, |ui| {
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
            let doc_frame = egui::Frame::none()
                .fill(theme::PANEL)
                .inner_margin(egui::Margin::symmetric(12.0, 12.0));
            egui::SidePanel::right("doc")
                .resizable(true)
                .default_width(440.0)
                .frame(doc_frame)
                .show(ctx, |ui| {
                let mut do_save = false;
                if let Some(d) = self.doc.as_mut() {
                    ui.horizontal(|ui| {
                        let title = if d.title.is_empty() { "Untitled".to_string() } else { d.title.clone() };
                        ui.label(egui::RichText::new(format!("▤ {title}")).color(theme::FG).strong());
                        ui.label(egui::RichText::new(format!("v{}", d.version)).color(theme::MUTED).small());
                        if !d.language.is_empty() {
                            ui.label(egui::RichText::new(&d.language).color(theme::MUTED).small());
                        }
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if d.saving {
                                ui.spinner();
                            }
                            let can_save = d.dirty && d.id.is_some() && !d.saving;
                            let save = egui::Button::new(egui::RichText::new("Save").color(theme::BG).strong())
                                .fill(if can_save { theme::GREEN } else { theme::MUTED });
                            if ui.add_enabled(can_save, save).clicked() {
                                do_save = true;
                            }
                        });
                    });
                    ui.add_space(4.0);
                    ui.separator();
                    egui::ScrollArea::vertical().id_source("docbody").show(ui, |ui| {
                        // Syntax-highlight the editor by the document's language.
                        let lang = syntect_lang(&d.language);
                        let theme = egui_extras::syntax_highlighting::CodeTheme::dark(13.5);
                        let mut layouter = |ui: &egui::Ui, text: &str, wrap_width: f32| {
                            let mut job = egui_extras::syntax_highlighting::highlight(
                                ui.ctx(),
                                ui.style(),
                                &theme,
                                text,
                                lang,
                            );
                            job.wrap.max_width = wrap_width;
                            ui.fonts(|f| f.layout_job(job))
                        };
                        let resp = ui.add(
                            egui::TextEdit::multiline(&mut d.content)
                                .desired_width(f32::INFINITY)
                                .desired_rows(30)
                                .code_editor()
                                .layouter(&mut layouter),
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

        let composer_frame = egui::Frame::none()
            .fill(theme::PANEL)
            .inner_margin(egui::Margin::symmetric(14.0, 10.0));
        egui::TopBottomPanel::bottom("composer").frame(composer_frame).show(ctx, |ui| {
            if !self.status.is_empty() {
                ui.label(egui::RichText::new(&self.status).color(theme::MUTED).small());
                ui.add_space(4.0);
            }
            ui.horizontal(|ui| {
                let send_w = 74.0;
                let hint = if self.kind == "agent" { "message the agent…" } else { "message…" };
                let resp = ui.add_sized(
                    [ui.available_width() - send_w - 8.0, 34.0],
                    egui::TextEdit::singleline(&mut self.input)
                        .hint_text(hint)
                        .vertical_align(egui::Align::Center),
                );
                if !self.focused_once {
                    resp.request_focus();
                    self.focused_once = true;
                }
                let enter = resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
                let send_btn = egui::Button::new(egui::RichText::new("Send").color(theme::BG).strong())
                    .fill(if self.busy { theme::MUTED } else { theme::FG })
                    .min_size(egui::vec2(send_w, 34.0));
                let clicked = ui.add_enabled(!self.busy, send_btn).clicked();
                if enter || clicked {
                    self.send(ctx);
                    ui.memory_mut(|m| m.request_focus(resp.id));
                }
            });
        });

        let central_frame = egui::Frame::none()
            .fill(theme::BG)
            .inner_margin(egui::Margin::symmetric(18.0, 14.0));
        egui::CentralPanel::default().frame(central_frame).show(ctx, |ui| {
            if self.messages.is_empty() {
                ui.vertical_centered(|ui| {
                    ui.add_space(ui.available_height() * 0.4);
                    ui.label(egui::RichText::new("◆").size(40.0).color(theme::BORDER));
                    ui.label(egui::RichText::new("Start a conversation").color(theme::MUTED));
                });
                return;
            }
            let busy = self.busy;
            let messages = &self.messages;
            let cache = &mut self.md_cache;
            egui::ScrollArea::vertical()
                .auto_shrink([false, false])
                .stick_to_bottom(true)
                .show(ui, |ui| {
                    for (idx, msg) in messages.iter().enumerate() {
                        render_bubble(ui, idx, msg, cache, busy);
                        ui.add_space(10.0);
                    }
                });
        });
    }
}

/// Rebuild a tool card from a persisted `tool_events` entry (metadata on a
/// reloaded assistant message).
fn tool_from_event(ev: &serde_json::Value) -> ToolCall {
    ToolCall {
        name: ev["tool"].as_str().unwrap_or("tool").to_string(),
        command: ev["command"].as_str().unwrap_or("").to_string(),
        output: ev["output"].as_str().unwrap_or("").to_string(),
        exit_code: ev["exit_code"].as_i64().unwrap_or(0),
        done: true,
    }
}

/// Map a document `language` label to the token egui_extras/syntect matches on
/// (a file extension). Falls back to markdown, which most docs are.
fn syntect_lang(language: &str) -> &'static str {
    match language.trim().to_lowercase().as_str() {
        "python" | "py" => "py",
        "rust" | "rs" => "rs",
        "javascript" | "js" | "node" => "js",
        "typescript" | "ts" => "ts",
        "json" => "json",
        "yaml" | "yml" => "yaml",
        "toml" => "toml",
        "html" => "html",
        "css" => "css",
        "bash" | "sh" | "shell" | "zsh" => "sh",
        "c" => "c",
        "cpp" | "c++" | "cxx" => "cpp",
        "go" => "go",
        "sql" => "sql",
        "xml" => "xml",
        "" | "markdown" | "md" | "text" | "txt" => "md",
        _ => "md",
    }
}

/// An agent tool call as an expandable card: status dot + name + command
/// preview in the header, full command and output when expanded.
fn render_tool_card(ui: &mut egui::Ui, tool: &ToolCall, salt: (usize, usize)) {
    let (dot, tint) = if !tool.done {
        (theme::WARN, theme::WARN)
    } else if tool.exit_code == 0 {
        (theme::GREEN, theme::MUTED)
    } else {
        (theme::RED, theme::RED)
    };
    let preview: String = tool.command.replace('\n', " ").chars().take(60).collect();
    egui::Frame::none()
        .fill(theme::FIELD_BG)
        .stroke(egui::Stroke::new(1.0, theme::BORDER))
        .rounding(egui::Rounding::same(7.0))
        .inner_margin(egui::Margin::symmetric(8.0, 4.0))
        .show(ui, |ui| {
            let header = egui::CollapsingHeader::new(
                egui::RichText::new(format!("⚙ {}", tool.name)).color(tint).small().strong(),
            )
            .id_salt(salt)
            .default_open(false);
            header.show_unindented(ui, |ui| {
                if !tool.command.is_empty() {
                    ui.label(egui::RichText::new("command").color(theme::MUTED).small());
                    ui.add(egui::Label::new(egui::RichText::new(&tool.command).monospace().small()).wrap());
                }
                if !tool.output.is_empty() {
                    ui.add_space(4.0);
                    ui.label(egui::RichText::new("output").color(theme::MUTED).small());
                    let shown: String = tool.output.chars().take(4000).collect();
                    ui.add(egui::Label::new(egui::RichText::new(shown).monospace().small()).wrap());
                }
            });
            // Status dot + command preview on the header row.
            let rect = ui.min_rect();
            let painter = ui.painter();
            painter.circle_filled(egui::pos2(rect.right() - 6.0, rect.top() + 10.0), 3.5, dot);
            if !preview.is_empty() {
                ui.label(egui::RichText::new(preview).color(theme::MUTED).small());
            }
        });
}

/// One chat bubble in the web app's style: user right + tail bottom-right,
/// assistant left + tail bottom-left, colored role dot, teal-bordered panel.
fn render_bubble(ui: &mut egui::Ui, idx: usize, msg: &ChatMessage, cache: &mut CommonMarkCache, busy: bool) {
    let user = msg.role == Role::User;
    let (who, dot, fill) = if user {
        ("you", theme::FG, theme::USER_BUBBLE)
    } else {
        ("odysseus", theme::RED, theme::AI_BUBBLE)
    };
    let max_w = (ui.available_width() * 0.82).min(760.0);
    let align = if user { egui::Align::Max } else { egui::Align::Min };

    ui.with_layout(egui::Layout::top_down(align), |ui| {
        ui.set_max_width(max_w);
        theme::bubble(fill, !user).show(ui, |ui| {
            ui.set_max_width(max_w - 26.0);
            // Role line: colored dot + name.
            ui.horizontal(|ui| {
                let (r, _) = ui.allocate_exact_size(egui::vec2(9.0, 9.0), egui::Sense::hover());
                ui.painter().circle_filled(r.center(), 4.0, dot);
                ui.label(egui::RichText::new(who).color(dot).strong().small());
            });
            if !msg.thinking.is_empty() {
                egui::CollapsingHeader::new(egui::RichText::new("💭 thinking").color(theme::MUTED).small())
                    .id_salt((idx, "think"))
                    .default_open(false)
                    .show_unindented(ui, |ui| {
                        ui.label(egui::RichText::new(&msg.thinking).italics().color(theme::MUTED));
                    });
            }
            for (i, tool) in msg.tools.iter().enumerate() {
                render_tool_card(ui, tool, (idx, i));
            }
            if msg.text.trim().is_empty() && !user && busy {
                ui.label(egui::RichText::new("▍").color(theme::MUTED));
            } else if user {
                // User input is shown verbatim (no markdown surprises).
                ui.label(egui::RichText::new(&msg.text).color(theme::FG));
            } else {
                // Assistant text is markdown, like the web app.
                CommonMarkViewer::new().show(ui, cache, &msg.text);
            }
        });
    });
}
