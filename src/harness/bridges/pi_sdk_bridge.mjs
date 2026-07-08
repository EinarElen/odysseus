#!/usr/bin/env node

import { createInterface } from "node:readline";
import { join } from "node:path";

let piModule = null;
let session = null;
let modelRegistry = null;
let sessionManager = null;
let currentRun = null;
let currentConfig = {};
const pendingToolCalls = new Map();
const pendingControlRequests = new Map();

function write(payload) {
	process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function ok(id, command, data = {}) {
	write({ id, type: "response", command, success: true, data });
}

function fail(id, command, error) {
	write({ id, type: "response", command, success: false, error: error instanceof Error ? error.message : String(error) });
}

async function loadPi() {
	if (piModule) return piModule;
	try {
		piModule = await import("@earendil-works/pi-coding-agent");
		return piModule;
	} catch (error) {
		throw new Error(
			`Could not import @earendil-works/pi-coding-agent. Install the Pi SDK package or set ODYSSEUS_PI_SDK_COMMAND to a packaged bridge command. ${error.message}`,
		);
	}
}

function textFromMessage(message) {
	const content = message?.content;
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.map((block) => {
			if (!block || typeof block !== "object") return "";
			if (block.type === "text" && typeof block.text === "string") return block.text;
			if (typeof block.content === "string") return block.content;
			return "";
		})
		.join("");
}

function compactValue(value, depth = 0, seen = new WeakSet()) {
	if (value === null || value === undefined) return value;
	if (typeof value === "string") return value.length > 1000 ? `${value.slice(0, 1000)}...` : value;
	if (typeof value !== "object") return value;
	if (seen.has(value)) return "[circular]";
	if (depth >= 3) return Array.isArray(value) ? `[${value.length} items]` : "[object]";
	seen.add(value);
	if (Array.isArray(value)) return value.slice(0, 20).map((item) => compactValue(item, depth + 1, seen));
	const out = {};
	for (const [key, item] of Object.entries(value)) {
		if (["messages", "result", "assistantMessage", "session", "agent"].includes(key)) continue;
		out[key] = compactValue(item, depth + 1, seen);
	}
	return out;
}

function statusEvent(phase, label, status = "running", detail = "", extra = {}) {
	const data = {
		phase,
		label,
		status,
		detail,
		...extra,
	};
	return { type: "harness_status", data };
}

function verbosityRank(value) {
	switch (String(value ?? "normal").toLowerCase()) {
		case "quiet": return 0;
		case "debug": return 2;
		default: return 1;
	}
}

function writeStatus(config, level, phase, label, status = "running", detail = "", extra = {}) {
	if (verbosityRank(config?.verbosity) < verbosityRank(level)) return;
	write(statusEvent(phase, label, status, detail, extra));
}

function statusFromPiEvent(event, phase, label, status = "running", detail = "") {
	if (verbosityRank(currentConfig?.verbosity) < verbosityRank("debug")) return null;
	return statusEvent(phase, label, status, detail, {
		event_type: event.type,
		event: compactValue(event),
	});
}

function convertEvent(event) {
	switch (event.type) {
		case "message_update": {
			const update = event.assistantMessageEvent ?? {};
			if (update.type === "text_delta" && typeof update.delta === "string") {
				if (currentRun) currentRun.emittedText = true;
				return { type: "text_delta", data: { text: update.delta } };
			}
			if (update.type === "thinking_delta" && typeof update.delta === "string") {
				return { type: "thinking_delta", data: { text: update.delta } };
			}
			return statusFromPiEvent(event, "message_update", "Model activity", "running", update.type || "");
		}
		case "tool_execution_start":
			return {
				type: "tool_start",
				data: { id: event.toolCallId, name: event.toolName, input: event.args },
			};
		case "tool_execution_update":
			return {
				type: "tool_update",
				data: { id: event.toolCallId, name: event.toolName, partial: event.partialResult },
			};
		case "tool_execution_end": {
			const data = {
				id: event.toolCallId,
				name: event.toolName,
				result: event.result,
				is_error: Boolean(event.isError),
			};
			if (event.result && typeof event.result === "object" && event.result.diff) data.diff = event.result.diff;
			return { type: "tool_end", data };
		}
		case "agent_end": {
			if (currentRun) currentRun.done = true;
			if (currentRun && !currentRun.emittedText) {
				for (const message of [...(event.messages ?? [])].reverse()) {
					if (message?.role !== "assistant") continue;
					const text = textFromMessage(message);
					if (text) return { type: "final_text", data: { text, done: true } };
				}
			}
			return { type: "done", data: {} };
		}
		case "agent_start":
			return statusFromPiEvent(event, "agent_start", "Agent started", "running", "Preparing tools and context");
		case "turn_start":
			return statusFromPiEvent(event, "turn_start", "Turn started", "running", "");
		case "turn_end":
			return statusFromPiEvent(event, "turn_end", "Turn complete", "done", "");
		case "message_start":
			return statusFromPiEvent(event, "message_start", "Model response started", "running", "");
		case "message_end":
			return statusFromPiEvent(event, "message_end", "Model response complete", "done", "");
		case "queue_update":
			return statusFromPiEvent(event, "queue", "Queue updated", "info", "");
		case "compaction_start":
			return statusFromPiEvent(event, "compaction", "Compacting context", "running", "");
		case "compaction_end":
			return statusFromPiEvent(event, "compaction", "Context compacted", "done", "");
		case "session_info_changed":
			return statusFromPiEvent(event, "session_info", "Session updated", "info", "");
		case "thinking_level_changed":
			return statusFromPiEvent(event, "thinking_level", "Thinking level changed", "info", "");
		case "auto_retry_start":
			return statusFromPiEvent(event, "retry", "Retrying", "running", "");
		case "auto_retry_end":
			return statusFromPiEvent(event, "retry", "Retry complete", "done", "");
		case "approval_request":
		case "permission_request":
			return {
				id: event.id ?? event.requestId,
				type: "approval_request",
				blocking: Boolean(event.blocking ?? event.requiresResult),
				data: event,
			};
		case "user_input_request":
			return {
				id: event.id ?? event.requestId,
				type: "user_input_request",
				blocking: Boolean(event.blocking ?? event.requiresResult),
				data: event,
			};
		case "policy_check":
			return {
				id: event.id ?? event.requestId,
				type: "policy_check",
				blocking: Boolean(event.blocking ?? event.requiresResult),
				data: event,
			};
		case "ui_request":
			return {
				id: event.id ?? event.requestId,
				type: "ui_request",
				blocking: Boolean(event.blocking ?? event.requiresResult),
				data: event,
			};
		case "state_update":
			return {
				id: event.id ?? event.requestId,
				type: "state_update",
				data: event,
			};
		case "control_request":
		case "control_yield":
			return {
				id: event.id ?? event.requestId,
				type: "control_yield",
				kind: event.kind ?? event.requestKind,
				blocking: Boolean(event.blocking ?? event.requiresResult),
				data: event.data ?? event,
			};
		default:
			return null;
	}
}

function controlRequest(kind, data = {}, blocking = false) {
	const requestId = `control-${kind}-${Math.random().toString(16).slice(2)}`;
	const resultPromise = new Promise((resolve) => {
		pendingControlRequests.set(requestId, { resolve });
	});
	write({
		id: requestId,
		type: "control_yield",
		kind,
		blocking,
		data,
	});
	return resultPromise;
}

function makeTool(definition) {
	return {
		name: definition.name,
		label: definition.label ?? definition.name,
		description: definition.description ?? "",
		parameters: definition.parameters ?? { type: "object", properties: {} },
		promptSnippet: definition.promptSnippet,
		promptGuidelines: definition.promptGuidelines,
		executionMode: definition.executionMode,
		async execute(toolCallId, params, signal, onUpdate) {
			const requestId = `tool-${toolCallId}-${Math.random().toString(16).slice(2)}`;
			const resultPromise = new Promise((resolve, reject) => {
				pendingToolCalls.set(requestId, { resolve, reject });
				if (signal) {
					signal.addEventListener(
						"abort",
						() => {
							if (!pendingToolCalls.has(requestId)) return;
							pendingToolCalls.delete(requestId);
							reject(new Error("Tool call aborted"));
						},
						{ once: true },
					);
				}
			});
			write({
				id: requestId,
				type: "tool_call",
				toolCallId,
				name: definition.name,
				arguments: params ?? {},
			});
			const result = await resultPromise;
			if (onUpdate && result?.details?.progress) {
				onUpdate(result.details.progress);
			}
			if (result?.isError) {
				throw new Error(String(result?.content ?? "Odysseus tool call failed"));
			}
			return {
				content: [{ type: "text", text: String(result?.content ?? "") }],
				details: result?.details ?? {},
			};
		},
	};
}

async function resolveModel(config) {
	const provider = config.model_provider ?? config.provider;
	const modelId = config.model_id ?? config.model;
	if (!provider || !modelId || !modelRegistry) return undefined;
	const model = modelRegistry.find(String(provider), String(modelId));
	if (!model) throw new Error(`Pi model not found: ${provider}/${modelId}`);
	return model;
}

async function startSession(id, payload) {
	const config = payload.config ?? {};
	currentConfig = config;
	writeStatus(config, "debug", "sdk_loading", "Loading Pi SDK", "running", "");
	const mod = await loadPi();
	writeStatus(config, "debug", "sdk_loaded", "Pi SDK loaded", "done", "");
	const cwd = config.workspace || process.cwd();
	const agentDir = config.agent_dir;
	const sessionDir = config.session_dir;
	const resumeMode = config.resume_mode ?? config.resumeMode ?? "create";
	const sessionFile = config.session_file;
	writeStatus(config, "debug", "auth_loading", "Loading Pi auth and models", "running", agentDir || "default agent dir");
	const authStorage = agentDir ? mod.AuthStorage.create(join(agentDir, "auth.json")) : mod.AuthStorage.create();
	modelRegistry = agentDir ? mod.ModelRegistry.create(authStorage, join(agentDir, "models.json")) : mod.ModelRegistry.create(authStorage);
	const model = await resolveModel(config);
	writeStatus(config, "debug", "auth_loaded", "Pi auth and models loaded", "done", model ? `${model.provider}/${model.id ?? model.model ?? ""}` : "default model");
	const customTools = Array.isArray(payload.tools) ? payload.tools.map(makeTool) : [];
	if (sessionFile) {
		writeStatus(config, "debug", "session_manager_loading", "Opening Pi session", "running", String(sessionFile));
		sessionManager = mod.SessionManager.open(String(sessionFile), sessionDir ? String(sessionDir) : undefined, cwd);
	} else if (resumeMode === "continue" || config.resume === true) {
		writeStatus(config, "debug", "session_manager_loading", "Continuing recent Pi session", "running", cwd);
		sessionManager = mod.SessionManager.continueRecent(cwd, sessionDir ? String(sessionDir) : undefined);
	} else if (config.persist === false || config.in_memory === true || config.inMemory === true) {
		writeStatus(config, "debug", "session_manager_loading", "Creating in-memory Pi session", "running", cwd);
		sessionManager = mod.SessionManager.inMemory(cwd);
	} else {
		writeStatus(config, "debug", "session_manager_loading", "Creating Pi session", "running", cwd);
		const sessionId = config.requested_session_id ?? config.session_id ?? config.odysseus_session_id;
		const options = sessionId ? { id: String(sessionId) } : undefined;
		sessionManager = mod.SessionManager.create(cwd, sessionDir ? String(sessionDir) : undefined, options);
	}
	writeStatus(config, "debug", "session_manager_ready", "Pi session manager ready", "done", sessionManager?.getSessionFile?.() || "");

	writeStatus(config, "debug", "agent_session_creating", "Creating Pi agent session", "running", `${customTools.length} Odysseus tools`);
	const created = await mod.createAgentSession({
		cwd,
		agentDir,
		authStorage,
		modelRegistry,
		model,
		thinkingLevel: config.thinking_level ?? config.thinkingLevel,
		sessionManager,
		customTools,
		noTools: config.disable_native_tools === false ? undefined : config.provide_odysseus_tools ? undefined : config.no_tools,
		tools: Array.isArray(config.tools) ? config.tools : undefined,
		excludeTools: Array.isArray(config.exclude_tools) ? config.exclude_tools : undefined,
	});
	session = created.session;
	writeStatus(config, "normal", "agent_session_ready", "Pi agent session ready", "done", session.sessionId ?? session.sessionFile ?? "");
	session.subscribe((event) => {
		const converted = convertEvent(event);
		if (converted) write(converted);
	});
	ok(id, "start_session", {
		harnessSessionId: session.sessionId ?? session.sessionFile,
		sessionId: session.sessionId,
		session_file: session.sessionFile,
		session_dir: sessionManager?.getSessionDir?.(),
		modelFallbackMessage: created.modelFallbackMessage,
		activeTools: session.getActiveToolNames(),
	});
}

async function prompt(id, payload) {
	if (!session) throw new Error("Pi SDK session is not started");
	currentRun = { emittedText: false, done: false };
	let acknowledged = false;
	const config = payload.config && typeof payload.config === "object" ? payload.config : {};
	const reconciliation = payload.reconciliation && typeof payload.reconciliation === "object" ? payload.reconciliation : {};
	const contextSerialized = typeof reconciliation.serialized === "string" && Array.isArray(reconciliation.messages)
		? reconciliation.serialized
		: "";
	const contextFingerprint = typeof reconciliation.fingerprint === "string" ? reconciliation.fingerprint : "";
	const userMessage = String(payload.message ?? "");
	const promptText = contextSerialized
		? `<odysseus_context${contextFingerprint ? ` fingerprint="${contextFingerprint}"` : ""}>\n${contextSerialized}\n</odysseus_context>\n\n${userMessage}`
		: userMessage;
	try {
		writeStatus(config, "debug", "prompt_submitting", "Submitting prompt to Pi", "running", "");
		await session.prompt(promptText, {
			images: Array.isArray(payload.attachments) ? payload.attachments : undefined,
			source: "odysseus",
			preflightResult: (success) => {
				acknowledged = true;
				if (success) {
					writeStatus(config, "normal", "prompt_accepted", "Prompt accepted", "done", "");
					ok(id, "prompt");
				} else {
					writeStatus(config, "quiet", "prompt_rejected", "Prompt rejected", "error", "Pi preflight rejected the request");
					fail(id, "prompt", "Pi preflight rejected the request");
				}
			},
		});
		if (!currentRun.done) {
			writeStatus(config, "debug", "prompt_complete", "Pi prompt complete", "done", "");
			write({ type: "done", data: {} });
		}
		if (!acknowledged) {
			ok(id, "prompt");
		}
	} finally {
		currentRun = null;
	}
}

async function command(id, payload) {
	if (!session) throw new Error("Pi SDK session is not started");
	const commandName = String(payload.command ?? "");
	const data = payload.payload ?? {};
	switch (commandName) {
		case "abort":
			session.agent.abort();
			ok(id, commandName);
			break;
		case "set_model": {
			const provider = data.provider ?? data.model_provider;
			const modelId = data.modelId ?? data.model_id ?? data.model;
			const model = modelRegistry?.find(String(provider), String(modelId));
			if (!model) throw new Error(`Pi model not found: ${provider}/${modelId}`);
			await session.setModel(model);
			ok(id, commandName, model);
			break;
		}
		case "set_thinking_level":
			await session.setThinkingLevel(String(data.level ?? data.thinking_level));
			ok(id, commandName, { level: session.thinkingLevel });
			break;
		case "get_state":
			ok(id, commandName, {
				sessionId: session.sessionId,
				session_file: session.sessionFile,
				session_dir: sessionManager?.getSessionDir?.(),
				model: session.model,
				thinkingLevel: session.thinkingLevel,
				isStreaming: session.isStreaming,
				activeTools: session.getActiveToolNames(),
				allTools: session.getAllTools(),
			});
			break;
		default:
			throw new Error(`Unsupported Pi SDK command: ${commandName}`);
	}
}

function resolveToolResult(payload) {
	const requestId = payload.id;
	const pending = pendingToolCalls.get(requestId);
	if (!pending) return;
	pendingToolCalls.delete(requestId);
	pending.resolve(payload.result ?? { content: "", isError: false, details: {} });
}

function resolveControlResult(payload) {
	const requestId = payload.id;
	const pending = pendingControlRequests.get(requestId);
	if (!pending) return;
	pendingControlRequests.delete(requestId);
	pending.resolve(payload.result ?? { status: "observed", data: {} });
}

async function handle(payload) {
	const id = payload.id;
	try {
		switch (payload.type) {
			case "start_session":
				await startSession(id, payload);
				break;
			case "prompt":
				void prompt(id, payload).catch((error) => fail(id, "prompt", error));
				break;
			case "command":
				await command(id, payload);
				break;
			case "tool_result":
				resolveToolResult(payload);
				break;
			case "control_result":
				resolveControlResult(payload);
				break;
			default:
				throw new Error(`Unknown command: ${payload.type}`);
		}
	} catch (error) {
		fail(id, payload.type ?? "unknown", error);
	}
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
	if (!line.trim()) return;
	try {
		const payload = JSON.parse(line);
		void handle(payload);
	} catch (error) {
		write({ type: "error", error: error instanceof Error ? error.message : String(error) });
	}
});

process.on("SIGTERM", () => {
	try {
		session?.dispose?.();
	} finally {
		process.exit(143);
	}
});
