mod api;
mod gui;
mod model;

use std::io::Write;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(String::as_str).unwrap_or("gui");
    let client = api::Client::from_env();

    match cmd {
        // Headless end-to-end check (no window) — used to verify the API layer.
        "probe" => {
            if let Err(e) = probe(&client, args.get(2).cloned()) {
                eprintln!("error: {e}");
                std::process::exit(1);
            }
        }
        "docs" => {
            if let Err(e) = docs_probe(&client) {
                eprintln!("error: {e}");
                std::process::exit(1);
            }
        }
        "gui" => {
            let native_options = eframe::NativeOptions::default();
            let result = eframe::run_native(
                "Odysseus",
                native_options,
                Box::new(|_cc| Ok(Box::new(gui::App::new(client)))),
            );
            if let Err(e) = result {
                eprintln!("gui error: {e}");
                std::process::exit(1);
            }
        }
        other => {
            eprintln!("unknown command: {other}\nusage: odysseus [gui|probe [message]]");
            std::process::exit(2);
        }
    }
}

/// Exercises the contract without a window: capabilities → sessions → start a
/// chat run → stream the reply to stdout.
fn probe(client: &api::Client, message: Option<String>) -> api::Result<()> {
    println!("server:  {}", client.base());
    println!("token:   {}", if client.has_token() { "resolved" } else { "NONE" });

    // One call for everything the UI needs.
    let boot = client.bootstrap()?;
    println!("owner:   {}", boot.capabilities["owner"].as_str().unwrap_or(""));
    println!(
        "sessions: {}  models: {}  default: {}",
        boot.sessions.len(),
        boot.models.len(),
        boot.default_model.clone().unwrap_or_default()
    );
    // Pin a working model (prefer terra to skip the fallback), else default.
    let model = boot
        .models
        .iter()
        .find(|m| m.model.contains("terra"))
        .map(|m| m.model.clone())
        .or_else(|| boot.default_model.clone());
    println!("using model: {}", model.clone().unwrap_or_else(|| "default".into()));

    let msg = message.unwrap_or_else(|| "Reply with one short friendly sentence.".to_string());
    println!("\n> {msg}\n");
    let run = client.start_run("chat", &msg, None, model.as_deref())?;
    print!("< ");
    std::io::stdout().flush().ok();

    client.stream_run(&run.run.run_id, |ev| {
        match ev.kind.clone().unwrap_or_default().as_str() {
            "message.delta" => {
                if let Some((text, thinking)) = ev.delta() {
                    if !thinking {
                        print!("{text}");
                        std::io::stdout().flush().ok();
                    }
                }
            }
            "error" => println!("\n[error] {}", ev.summary.unwrap_or_default()),
            _ => {}
        }
    })?;

    // Reload the just-created session's persisted history.
    let hist = client.history(&run.run.session_id)?;
    println!("\n\nrun complete · history reloaded: {} message(s)", hist.history.len());
    for m in &hist.history {
        println!("  {}: {}", m.role, m.content.clone().unwrap_or_default());
    }
    Ok(())
}

/// Verifies the document flow: agent writes a doc (doc_* events) → list → get →
/// edit + save-back → confirm.
fn docs_probe(client: &api::Client) -> api::Result<()> {
    let boot = client.bootstrap()?;
    let model = boot.models.iter().find(|m| m.model.contains("terra")).map(|m| m.model.clone());

    let mut doc_id: Option<String> = None;
    let run = client.start_run(
        "agent",
        "Use the document tool to create a markdown doc titled RustCheck with the single line: hello from rust.",
        None,
        model.as_deref(),
    )?;
    print!("streaming agent run… ");
    std::io::stdout().flush().ok();
    client.stream_run(&run.run.run_id, |ev| {
        if ev.kind.as_deref() == Some("doc_update") {
            if let Some(id) = ev.payload["doc_id"].as_str() {
                doc_id = Some(id.to_string());
            }
        }
    })?;
    let doc_id = doc_id.ok_or("no doc_update event — agent didn't create a document")?;
    println!("doc created via doc_update: {doc_id}");

    let list = client.documents()?;
    println!("documents list: {} (contains it: {})", list.documents.len(), list.documents.iter().any(|d| d.id == doc_id));

    let d = client.document_get(&doc_id)?;
    let content = d.current_content.clone().unwrap_or_default();
    println!("get: title={:?} v{} content={:?}", d.title, d.version_count.unwrap_or(0), content);

    let edited = format!("{content}\nEdited from the Rust client.");
    let updated = client.document_update(&doc_id, &edited)?;
    println!("save-back: v{} (force? no; coalesced within 60s)", updated.version_count.unwrap_or(0));

    let d2 = client.document_get(&doc_id)?;
    println!("verified content:\n---\n{}\n---", d2.current_content.unwrap_or_default());
    Ok(())
}
