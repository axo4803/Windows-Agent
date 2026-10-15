"use strict";
const $ = id => document.getElementById(id);
const activeStatuses = new Set(["starting", "running", "awaiting_approval", "stopping"]);
let token = "", current = "", cursor = 0, snapshot = null, busy = false, sending = false, online = false;
let chatSignature = "", recentSignature = "", approvalId = "", steps = 0;
let polling = null;
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
function notice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: {"X-Agent-Token": token, ...(body === undefined ? {} : {"Content-Type": "application/json"})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json(); if (!response.ok) throw new Error(data.error || "The local request failed."); return data;
}
function inlineText(parent, text) {
  const pattern = /\*\*([^*\n]+)\*\*|`([^`\n]+)`/g; let offset = 0;
  for (const match of text.matchAll(pattern)) { parent.append(document.createTextNode(text.slice(offset, match.index))); parent.append(el(match[1] ? "strong" : "code", "", match[1] || match[2])); offset = match.index + match[0].length; }
  parent.append(document.createTextNode(text.slice(offset)));
}
function answerText(text) {
  const node = el("div", "message-text"); const lines = text.split("\n"); let index = 0;
  const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith("```")) { const code = []; index++; while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]); node.append(el("pre", "", code.join("\n"))); index++; continue; }
    if (line.includes("|") && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[index + 1] || "")) {
      const wrapper = el("div", "table-scroll"), table = el("table"), head = el("thead"), row = el("tr");
      for (const cell of cells(line)) { const th = el("th"); inlineText(th, cell); row.append(th); } head.append(row); table.append(head); index += 2;
      const body = el("tbody"); while (index < lines.length && lines[index].trim().startsWith("|")) { const tr = el("tr"); for (const cell of cells(lines[index++])) { const td = el("td"); inlineText(td, cell.replace(/<br\s*\/?\s*>/gi, "\n")); tr.append(td); } body.append(tr); } table.append(body); wrapper.append(table); node.append(wrapper); continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+)$/); if (heading) { const title = el("h3"); inlineText(title, heading[1]); node.append(title); }
    else if (/^\s*[-*]\s+/.test(line)) { const bullet = el("div", "answer-bullet"); inlineText(bullet, line.replace(/^\s*[-*]\s+/, "")); node.append(bullet); }
    else if (/^\s*---+\s*$/.test(line)) node.append(el("hr"));
    else { inlineText(node, line); node.append(document.createTextNode("\n")); }
    index++;
  }
  return node;
}
function message(role, text, status = "") {
  const row = el("article", "message " + role); row.append(el("div", "avatar", role === "user" ? "You" : "w"));
  const content = el("div", "message-content"); const label = el("div", "message-label", role === "user" ? "You" : "Windows-Agent");
  if (status === "saved") label.append(el("span", "saved-tag", "Saved conversation")); content.append(label, answerText(text));
  if (role !== "user" && text) { const copy = el("button", "copy-button", "Copy answer"); copy.onclick = async () => { try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; } catch { notice("Your browser could not copy the answer. You can select and copy the text."); } }; content.append(copy); }
  row.append(content); return row;
}
function renderChat(state) {
  const signature = JSON.stringify([state.messages, state.jobs]); if (signature === chatSignature) return; chatSignature = signature;
  const chat = $("chat"), nearBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 100;
  const hadMessages = $("messages").childElementCount > 0; $("messages").replaceChildren();
  $("welcome").hidden = Boolean(state.jobs.length);
  if (state.messages.length) { const saved = el("details", "saved-conversation"); saved.append(el("summary", "", `Previous conversation · ${state.messages.length} saved tasks`)); for (const item of state.messages) saved.append(message("user", item.task, "saved"), message("assistant", item.answer, "saved")); $("messages").append(saved); }
  for (const item of state.jobs) {
    const user = message("user", item.task, item.status); if (item.id) user.id = "task-" + item.id; $("messages").append(user);
    if (item.answer) $("messages").append(message("assistant", item.answer, item.status));
    if (activeStatuses.has(item.status)) { const row = message("assistant", ""); const work = el("div", "working"); work.append(el("span", "spinner"), el("span", "", item.status === "awaiting_approval" ? "Waiting for your approval…" : item.status === "stopping" ? "Stopping task…" : "Working on your request…")); row.querySelector(".message-content").append(work); $("messages").append(row); }
    else if (item.status === "error" || item.status === "stopped") { const row = message("assistant", ""); row.querySelector(".message-content").append(el("div", "task-outcome", item.error || "Task stopped. Review the activity before continuing.")); $("messages").append(row); }
  }
  if (nearBottom || !hadMessages) chat.scrollTop = chat.scrollHeight;
}
function renderRecent(state) {
  const signature = JSON.stringify(state.jobs.map(j => [j.id, j.task])); if (signature === recentSignature) return; recentSignature = signature;
  $("recent").replaceChildren(); if (!state.jobs.length) { $("recent").append(el("p", "muted sidebar-empty", "Your tasks will appear here.")); return; }
  for (const job of [...state.jobs].reverse()) { const button = el("button", "recent-task" + (job.id === state.current ? " selected" : ""), job.task); button.title = job.task; button.onclick = () => $("task-" + job.id)?.scrollIntoView({block: "start", behavior: "smooth"}); $("recent").append(button); }
}
function addEvents(events, truncated) {
  if (truncated) $("timeline").append(el("p", "muted", "Earlier activity was shortened."));
  const pane = $("timeline"), nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
  for (const event of events) {
    $("activity-empty")?.remove(); const row = el("div", "event " + event.kind);
    let label = event.label; if (event.kind === "tool_start") label = "Running " + label.replaceAll("_", " ");
    if (event.kind === "tool_result") label = label.replaceAll("_", " ") + " · result";
    row.append(el("div", "event-title", label), el("time", "", new Date(event.time * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"})));
    if (event.details !== undefined) { const details = el("details"); details.append(el("summary", "", event.kind === "tool_start" ? "View inputs" : "View returned data"), el("pre", "", typeof event.details === "string" ? event.details : JSON.stringify(event.details, null, 2))); row.append(details); }
    pane.append(row); if (event.kind === "tool_result") steps++; while (pane.children.length > 500) pane.firstElementChild.remove();
  }
  $("event-count").textContent = steps + (steps === 1 ? " step" : " steps"); if (nearBottom) pane.scrollTop = pane.scrollHeight;
}
function render(state) {
  snapshot = state; const job = state.jobs.at(-1); busy = job && activeStatuses.has(job.status);
  if (state.current !== current) { current = state.current || ""; cursor = 0; steps = 0; $("timeline").replaceChildren(); if (job) { for (const id of ["read-only", "strict-approval", "offline"]) $(id).checked = job.options[id.replaceAll("-", "_")]; for (const id of ["max-tools", "max-turns", "max-seconds"]) $(id).value = job.options[id.replaceAll("-", "_")]; } if (!current) { const empty = el("div", "activity-empty"); empty.append(el("span", "", "⋮"), el("strong", "", "Nothing running yet"), el("p", "", "Tool calls, results, and approvals will appear here.")); empty.id = "activity-empty"; $("timeline").append(empty); } }
  renderChat(state); renderRecent(state); addEvents(state.events, state.events_truncated); cursor = state.cursor;
  const labels = {starting: "Starting", running: "Working", awaiting_approval: "Approval", stopping: "Stopping", completed: "Finished", stopped: "Stopped", error: "Error"};
  $("run-state").textContent = job ? labels[job.status] : "Ready"; $("run-state").className = "status-tag" + (busy ? " busy" : job?.status === "error" ? " error" : "");
  $("conversation-title").textContent = state.jobs.length || state.messages.length ? "Conversation" : "New conversation";
  $("stop").hidden = !busy; $("stop").disabled = job?.status === "stopping"; $("send").hidden = Boolean(busy);
  $("send").disabled = sending || !online || !$("task").value.trim(); $("task").disabled = Boolean(busy || sending); $("new-chat").disabled = Boolean(busy || sending); $("new-chat-mobile").disabled = Boolean(busy || sending);
  for (const id of ["model", "read-only", "strict-approval", "max-tools", "max-turns", "max-seconds", "offline"]) $(id).disabled = Boolean(busy || sending);
  $("approval-card").hidden = !state.approval; approvalId = state.approval?.id || ""; $("approval-description").textContent = state.approval?.description || "";
  $("approve").disabled = false; $("deny").disabled = false;
  $("mode-note").textContent = $("read-only").checked ? "Read-only mode" : $("strict-approval").checked ? "Expanded · strict approvals" : "Expanded mode";
}
async function refreshModels(preferred) {
  $("refresh-models").disabled = true;
  try { const result = await api("/api/models"); online = result.online && result.models.length > 0; const selected = preferred || $("model").value; $("model").replaceChildren();
    const models = result.models.length ? result.models : [selected || "qwen3.5:4b"]; for (const model of models) { const option = el("option", "", model); option.value = model; $("model").append(option); }
    const runningModel = snapshot?.jobs.at(-1); const wanted = runningModel && (preferred || activeStatuses.has(runningModel.status)) ? runningModel.model : selected;
    if (models.includes(wanted)) $("model").value = wanted;
    $("model-health").textContent = online ? "Ollama connected" : result.online ? "No installed models" : "Ollama offline"; $("model-health").parentElement.classList.toggle("offline", !online);
    if (!online) notice(result.error || "Install a model with ollama pull qwen3.5:4b, then refresh models."); else notice("");
    if (snapshot) render({...snapshot, events: [], events_truncated: false});
  } catch (error) { notice(error.message); } finally { $("refresh-models").disabled = false; }
}
async function poll() {
  if (polling) return polling;
  polling = (async () => { try { const state = await api(`/api/state?cursor=${cursor}&job=${encodeURIComponent(current)}`); render(state); }
  catch (error) { notice("Cannot reach the local interface. Keep its terminal open and refresh this page after restarting. " + error.message); } })();
  try { await polling; } finally { polling = null; }
}
$("task-form").onsubmit = async event => {
  event.preventDefault(); if (busy || sending || !online || !$("task").value.trim()) return;
  sending = true; const task = $("task").value; $("send").disabled = true;
  try { await api("/api/tasks", {task, model: $("model").value, read_only: $("read-only").checked, strict_approval: $("strict-approval").checked, offline: $("offline").checked, max_tools: Number($("max-tools").value), max_turns: Number($("max-turns").value), max_seconds: Number($("max-seconds").value)}); $("task").value = ""; notice(""); }
  catch (error) { notice(error.message); } finally { sending = false; await poll(); }
};
$("task").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("task-form").requestSubmit(); } });
$("task").addEventListener("input", () => { $("send").disabled = busy || sending || !online || !$("task").value.trim(); });
$("stop").onclick = async () => { $("stop").disabled = true; try { await api("/api/stop", {job: current}); } catch (error) { notice(error.message); } await poll(); };
async function decide(approved) { $("approve").disabled = true; $("deny").disabled = true; try { await api("/api/approval", {job: current, approval: approvalId, approved}); } catch (error) { notice(error.message); } await poll(); }
$("approve").onclick = () => decide(true); $("deny").onclick = () => decide(false);
$("new-chat").onclick = async () => { if (busy || sending) return; try { await api("/api/new-chat", {}); $("task").value = ""; notice(""); await poll(); $("task").focus(); } catch (error) { notice(error.message); } };
$("new-chat-mobile").onclick = () => $("new-chat").click();
$("refresh-models").onclick = () => refreshModels();
for (const button of document.querySelectorAll("[data-prompt]")) button.onclick = () => { $("task").value = button.dataset.prompt; $("task").dispatchEvent(new Event("input")); $("task").focus(); };
for (const id of ["read-only", "strict-approval"]) $(id).onchange = () => { if (snapshot) render({...snapshot, events: [], events_truncated: false}); };
function updateActivityExpanded() { const small = matchMedia("(max-width: 900px)").matches; $("activity-toggle").setAttribute("aria-expanded", String(small ? document.body.classList.contains("activity-visible") : !document.body.classList.contains("activity-hidden"))); }
$("activity-toggle").onclick = () => { const small = matchMedia("(max-width: 900px)").matches; if (small) document.body.classList.toggle("activity-visible"); else document.body.classList.toggle("activity-hidden"); updateActivityExpanded(); };
matchMedia("(max-width: 900px)").addEventListener("change", updateActivityExpanded); updateActivityExpanded();
document.addEventListener("keydown", event => { if (event.key.toLowerCase() === "n" && event.altKey && !busy) { event.preventDefault(); $("new-chat").click(); } });
(async () => { try { const bootstrap = await api("/api/bootstrap"); token = bootstrap.token; if (bootstrap.desktop_mode) { let sizeTimer; const saveSize = () => { clearTimeout(sizeTimer); sizeTimer = setTimeout(() => { if (window.outerWidth >= 960 && window.outerHeight >= 640) api('/api/desktop-window', {width: window.outerWidth, height: window.outerHeight}).catch(() => {}); }, 300); }; window.addEventListener('resize', saveSize); window.addEventListener('pagehide', () => { try { fetch('/api/desktop-close', {method: 'POST', headers: {'X-Agent-Token': token, 'Content-Type': 'application/json'}, body: '{}', keepalive: true}); } catch {} }); saveSize(); } await poll(); await refreshModels(bootstrap.default_model); $("task").focus(); } catch (error) { notice(error.message); } finally { const repeat = async () => { await poll(); setTimeout(repeat, 800); }; setTimeout(repeat, 800); } })();
