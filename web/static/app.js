"use strict";
// NEMESIS web terminal — transcript only; sessions are managed via /resume.

const $ = (sel, root) => (root || document).querySelector(sel);
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
const escapeHtml = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = {
  sessionId: null,
  commands: [],
  eventSource: null,
  menuOpen: false,
  menuSel: 0,
  menuItems: [],
  suppressOutput: false,
  currentThinkingRow: null,
  seenAuthReqs: new Set(),
  pendingAuth: null,
  history: [],
  historyIdx: -1,
};

// ---------------------------------------------------------------- markdown-lite
// ------------------------------------------------------------- markdown-lite
function mdToHtml(src) {
  if (src == null) return "";
  const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  const n = lines.length;
  let i = 0;
  while (i < n) {
    const line = lines[i];

    // fenced code block
    const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/);
    if (fence) {
      const marker = fence[1][0];
      const lang = fence[2] || "";
      const buf = [];
      i++;
      const closeRe = new RegExp("^\\s*" + marker + "{3,}\\s*$");
      while (i < n && !closeRe.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre><code${lang ? ` class="lang-${escapeHtml(lang)}"` : ""}>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { out.push("<hr>"); i++; continue; }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < n && /^\s*>\s?/.test(lines[i])) { buf.push(inline(lines[i].replace(/^\s*>\s?/, ""))); i++; }
      out.push(`<blockquote>${buf.join("<br>")}</blockquote>`);
      continue;
    }

    // table
    if (isTableRow(line) && i + 1 < n && isTableSep(lines[i + 1])) {
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < n && isTableRow(lines[i])) { rows.push(splitRow(lines[i])); i++; }
      out.push(tableHtml(header, rows));
      continue;
    }

    // list
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const res = parseList(lines, i);
      out.push(res.html); i = res.next; continue;
    }

    // blank line
    if (!line.trim()) { i++; continue; }

    // paragraph (merge consecutive lines, stop at next block)
    const buf = [];
    while (i < n && lines[i].trim() && !isBlockStart(lines[i]) && !(isTableRow(lines[i]) && i + 1 < n && isTableSep(lines[i + 1]))) {
      buf.push(lines[i]); i++;
    }
    out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
  }
  return out.join("\n");
}

function inline(s) {
  s = String(s == null ? "" : s);
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
  s = escapeHtml(s);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  s = s.replace(/\u0000(\d+)\u0000/g, (_, idx) => `<code>${escapeHtml(codes[+idx])}</code>`);
  return s;
}

function isBlockStart(l) {
  return /^\s*(#{1,6})\s+/.test(l) || /^\s*(`{3,}|~{3,})/.test(l) || /^\s*>/.test(l) ||
    /^\s*([-*+]|\d+[.)])\s+/.test(l) || /^\s*([-*_])\s*(\1\s*){2,}$/.test(l);
}
function isTableRow(l) { return l.trim().includes("|"); }
function isTableSep(l) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l) && l.includes("-"); }
function splitRow(l) { return l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()); }
function tableHtml(header, rows) {
  const head = "<thead><tr>" + header.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead>";
  const body = "<tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody>";
  return `<table>${head}${body}</table>`;
}
function parseList(lines, start) {
  const first = lines[start].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
  const baseIndent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let i = start;
  while (i < lines.length) {
    const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (!m || m[1].length < baseIndent) break;
    if (m[1].length > baseIndent) {
      const sub = parseList(lines, i);
      if (items.length) items[items.length - 1].children.push(sub.html);
      i = sub.next;
      continue;
    }
    if (/\d/.test(m[2]) !== ordered) break;
    let content = m[3];
    let task = null;
    const t = content.match(/^\[([ xX])\]\s+(.*)$/);
    if (t) { task = t[1].toLowerCase() === "x"; content = t[2]; }
    items.push({ text: content, children: [], task });
    i++;
  }
  const tag = ordered ? "ol" : "ul";
  const html = `<${tag}>` + items.map((it) => {
    let li = it.task != null
      ? `<li class="task"><input type="checkbox" disabled${it.task ? " checked" : ""}>${inline(it.text)}`
      : `<li>${inline(it.text)}`;
    return li + it.children.join("") + "</li>";
  }).join("") + `</${tag}>`;
  return { html, next: i };
}

// ---------------------------------------------------------------- API helpers
async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  if (!res.ok) throw new Error((await res.text().catch(() => "") || res.status));
  return res.json();
}

// ----------------------------------------------------------------- transcript
const transcript = $("#transcript");
let welcome = $("#welcome");
let autoScroll = true;
transcript.addEventListener("scroll", () => {
  autoScroll = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 90;
});
function scrollToBottom() { if (autoScroll) transcript.scrollTop = transcript.scrollHeight; }
function hideWelcome() { if (welcome && welcome.parentNode) { welcome.remove(); welcome = null; } }
function addLine(node) { hideWelcome(); transcript.appendChild(node); scrollToBottom(); return node; }

function fmtToolParams(tool, params) {
  if (!params) return "";
  if (tool === "bash") return params.command || "";
  if (["read_file", "edit", "write_file", "delete_file", "list_dir", "grep", "glob"].includes(tool)) {
    return params.path || params.file_path || params.pattern || (params.paths ? params.paths.join(", ") : "");
  }
  try { const k = Object.keys(params); return k.length ? `${k[0]}=${String(params[k[0]]).slice(0,48)}` : ""; } catch { return ""; }
}

function renderUser(text) {
  const d = el("div", "tl user");
  d.innerHTML = `<span class="prompt-caret">❯</span> ${escapeHtml(text)}`;
  return addLine(d);
}

function renderAssistant(text) {
  const d = el("div", "tl ai");
  d.appendChild(el("div", "role", "NEMESIS"));
  const b = el("div", "md"); b.innerHTML = mdToHtml(text);
  d.appendChild(b);
  return addLine(d);
}

function renderSystem(text, level) {
  const d = el("div", "tl system " + (level || ""));
  d.textContent = text;
  return addLine(d);
}

function renderThinking(on, label) {
  if (!on) { if (state.currentThinkingRow) { state.currentThinkingRow.remove(); state.currentThinkingRow = null; } setBusy(false); return; }
  setBusy(true);
  const d = el("div", "tl thinking");
  const row = el("div", "thinking-row", `<span class="dots"><i></i><i></i><i></i></span> ${escapeHtml(label || "réflexion...")}`);
  d.appendChild(row); state.currentThinkingRow = addLine(d);
}

function renderTaskSummary(d) {
  const line = el("div", "summary-line", `— ${d.mmss || d.elapsed + "s"} · ${d.tools} appel(s) outil${d.tools > 1 ? "s" : ""} —`);
  hideWelcome(); transcript.appendChild(line); scrollToBottom();
}

function buildToolCard(callId, tool, params, status) {
  const card = el("div", "tool-card running msg-none tl");
  card.dataset.callId = callId;
  const head = el("div", "tool-head");
  head.appendChild(el("span", "tool-icon", "●"));
  head.appendChild(el("span", "tool-name", escapeHtml(tool)));
  head.appendChild(el("span", "tool-summary", escapeHtml(fmtToolParams(tool, params))));
  head.appendChild(el("span", "tool-status", "..."));
  head.appendChild(el("span", "tool-chev", "▸"));
  card.appendChild(head);

  const body = el("div", "tool-body");
  const ppre = el("pre", "tool-pre tool-params"); ppre.textContent = JSON.stringify(params || {}, null, 2);
  body.appendChild(ppre);

  const osec = el("div", "tool-output");
  const opre = el("pre", "tool-pre"); opre.textContent = "";
  const toggle = el("button", "output-toggle", state.suppressOutput ? "afficher" : "masquer");
  toggle.addEventListener("click", (e) => { e.stopPropagation(); osec.classList.toggle("hidden"); toggle.textContent = osec.classList.contains("hidden") ? "afficher" : "masquer"; });
  osec.appendChild(opre); osec.appendChild(document.createTextNode(" ")); osec.appendChild(toggle);
  body.appendChild(osec);
  card.appendChild(body);

  head.addEventListener("click", () => card.classList.toggle("open"));
  card._statusEl = head.querySelector(".tool-status");
  card._outPre = opre; card._outSec = osec;
  return addLine(card);
}

function updateToolStatus(card, success) {
  if (!card) return;
  card.classList.remove("running");
  card.classList.add(success ? "ok" : "fail");
  const st = card._statusEl;
  if (st) { st.textContent = success ? "✓" : "✗"; st.className = "tool-status " + (success ? "ok" : "fail"); }
}

function appendToolStream(card, chunk) {
  if (!card || !card._outPre) return;
  card._outPre.textContent += chunk;
  scrollToBottom();
}

function setToolResult(card, data) {
  if (!card) return;
  updateToolStatus(card, data.success);
  const out = data.preview || data.error || "";
  if (card._outPre) card._outPre.textContent = out;
  if (data.output_id != null && card._outSec) card._outSec.dataset.outputId = data.output_id;
  if (out.length > 0 && out.length < 700) card.classList.add("open");
}

transcript.addEventListener("click", async (e) => {
  const head = e.target.closest(".tool-head"); if (!head) return;
  if (e.target.closest(".output-toggle")) return;
  const card = head.parentElement;
  const sec = card._outSec;
  if (card.classList.contains("open") && sec && sec.dataset.outputId && !sec.dataset.loaded) {
    sec.dataset.loaded = "1";
    try {
      const d = await api(`/api/sessions/${state.sessionId}/output/${sec.dataset.outputId}`);
      card._outPre.textContent = d.output || "";
    } catch (_) {}
  }
});

function findOrRenderTodoCard() {
  let card = transcript.querySelector("[data-todo-card]");
  if (card) return card;
  card = el("div", "tl");
  card.setAttribute("data-todo-card", "1");
  const wrap = el("ul", "todo-list"); card.appendChild(wrap);
  card._wrap = wrap;
  return addLine(card);
}
function renderTodo(items) {
  const card = findOrRenderTodoCard();
  card._wrap.innerHTML = "";
  for (const it of items || []) {
    const li = el("li", "todo-item " + (it.status || ""));
    li.appendChild(el("span", "todo-box"));
    li.appendChild(el("span", "", escapeHtml(it.content || "")));
    card._wrap.appendChild(li);
  }
  scrollToBottom();
}

function renderCommand(content) {
  const d = el("div", "tl cmd-result");
  const pre = el("pre", "cmd-pre"); pre.textContent = content;
  d.appendChild(pre); addLine(d);
}

function resolveChoice(node) {
  node.classList.add("resolved");
  node.querySelectorAll("button").forEach((b) => { b.disabled = true; });
}

function renderChoice(data) {
  const d = el("div", "tl choice");
  const head = el("div", "choice-head");
  head.appendChild(el("span", "choice-icon", "❯"));
  head.appendChild(el("span", "choice-title", escapeHtml(data.title || "Options")));
  d.appendChild(head);
  if (data.subtitle) d.appendChild(el("div", "choice-sub", escapeHtml(data.subtitle)));
  if (data.prompt) d.appendChild(el("div", "choice-prompt", escapeHtml(data.prompt)));

  const options = Array.isArray(data.options) ? data.options : [];
  let list = null;

  if (data.filterable) {
    const filter = el("input", "choice-filter");
    filter.type = "text";
    filter.placeholder = "filtrer…";
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      list.querySelectorAll(".choice-item").forEach((row) => {
        const hit = !q || (row.dataset.search || "").includes(q);
        row.classList.toggle("hidden", !hit);
      });
    });
    d.appendChild(filter);
  }

  list = el("div", "choice-list");
  if (!options.length) list.appendChild(el("div", "choice-empty", "aucune option"));
  for (const opt of options) {
    const row = el("div", "choice-item" + (opt.current ? " current" : ""));
    const search = [opt.label, opt.value, opt.hint].filter(Boolean).join(" ").toLowerCase();
    row.dataset.search = search;

    const main = el("button", "choice-main");
    main.type = "button";
    main.innerHTML =
      `<span class="choice-label">${escapeHtml(opt.label == null ? opt.value : opt.label)}</span>` +
      (opt.hint ? `<span class="choice-hint">${escapeHtml(opt.hint)}</span>` : "") +
      (opt.current ? `<span class="choice-badge">actuel</span>` : "");
    if (opt.command) {
      main.addEventListener("click", () => { resolveChoice(d); runCommand(opt.command); });
    } else {
      main.disabled = true;
      main.classList.add("info");
    }
    row.appendChild(main);

    for (const a of (opt.actions || [])) {
      const ab = el("button", "choice-action" + (a.danger ? " danger" : ""));
      ab.type = "button";
      ab.textContent = a.label;
      ab.addEventListener("click", (e) => { e.stopPropagation(); resolveChoice(d); runCommand(a.command); });
      row.appendChild(ab);
    }
    list.appendChild(row);
  }
  d.appendChild(list);
  return addLine(d);
}


function applyOutputVisibility() {
  transcript.classList.toggle("muted-all", state.suppressOutput);
  document.querySelectorAll(".output-toggle").forEach(b => b.textContent = state.suppressOutput ? "afficher" : "masquer");
}

// ------------------------------------------------------------- auth (inline)
const authAsk = $("#auth-ask");

function showAuth(data) {
  state.seenAuthReqs.add(data.request_id);
  state.pendingAuth = data.request_id;
  authAsk.innerHTML = "";
  const q = el("div", "auth-q");
  q.innerHTML = `autoriser <b>${escapeHtml(data.tool)}</b> ?` +
    ` <span class="auth-badge ${escapeHtml(data.risk || "")}">${escapeHtml(data.risk || "medium")}</span>`;
  authAsk.appendChild(q);
  const pre = el("pre"); pre.textContent = JSON.stringify(data.params || {}, null, 2);
  authAsk.appendChild(pre);
  const keys = el("div", "auth-keys");
  keys.innerHTML = "<kbd>y</kbd> oui (une fois) · <kbd>a</kbd> toujours (cet outil) · <kbd>n</kbd> refuser";
  authAsk.appendChild(keys);
  authAsk.classList.remove("hidden");
  input.focus();
}

async function resolveAuth(choice) {
  if (!state.pendingAuth) return;
  const rid = state.pendingAuth;
  state.pendingAuth = null;
  authAsk.classList.add("hidden");
  authAsk.innerHTML = "";
  try { await api("/api/auth/respond", { method: "POST", body: JSON.stringify({ session: state.sessionId, request_id: rid, choice }) }); } catch (_) {}
}

// ------------------------------------------------------------------ busy/stop
function setBusy(on) {
  $("#btn-send").disabled = !!on;
  $("#btn-interrupt").classList.toggle("hidden", !on);
  const chip = $("#st-state");
  chip.textContent = on ? "occupé..." : "prêt";
}

// ---------------------------------------------------------------- events (SSE)
function connectEvents() {
  if (state.eventSource) state.eventSource.close();
  const es = new EventSource("/api/events");
  state.eventSource = es;
  es.onmessage = (ev) => {
    let data; try { data = JSON.parse(ev.data); } catch { return; }
    if (data.type === "select_session") { adoptSession(data.data.id); return; }
    if (data.session && data.session !== state.sessionId) return;
    handleEvent(data);
  };
  es.onerror = () => {};
}

function handleEvent(e) {
  const d = e.data || {};
  switch (e.type) {
    case "user_message": renderUser(d.text); break;
    case "ai_message": renderAssistant(d.text); break;
    case "system_message": renderSystem(d.text, d.level); break;
    case "thinking": renderThinking(d.on, d.label); break;
    case "task_summary": renderTaskSummary(d); break;
    case "tool_start": buildToolCard(d.call_id, d.tool, d.params); break;
    case "tool_output": { const c = cardByCall(d.call_id); appendToolStream(c, d.chunk); break; }
    case "tool_result": { const c = cardByCall(d.call_id); setToolResult(c, d); if (d.items && d.tool === "todo") renderTodo(d.items); break; }
    case "auth_request": showAuth(d); break;
    case "auth_resolved":
      if (state.seenAuthReqs.has(d.request_id)) {
        const label = d.choice === "y" ? "autorisé (une fois)" : d.choice === "a" ? "autorisé (permanent)" : "refusé";
        renderSystem(`${label}${d.allowed ? " → exécution" : " → annulé"}`, d.allowed ? undefined : "warning");
      }
      break;
    default: break;
  }
}
function cardByCall(callId) { return transcript.querySelector(`[data-call-id="${callId}"]`); }

// ------------------------------------------------------------------ sessions
function clearTranscript() {
  transcript.querySelectorAll(":scope > *:not(#welcome)").forEach(n => n.remove());
}

async function selectSession(sid) {
  state.sessionId = sid;
  state.pendingAuth = null;
  authAsk.classList.add("hidden"); authAsk.innerHTML = "";
  clearTranscript();
  try {
    const detail = await api(`/api/sessions/${sid}`);
    for (const m of detail.messages || []) replayMessage(m);
    applyOutputVisibility();
  } catch (err) { renderSystem("Impossible de charger la session: " + err.message, "error"); }
  refreshStatus();
}

// the server told us to switch (e.g. after /resume): just rebind, no reload loop
async function adoptSession(sid) {
  if (sid === state.sessionId) return;
  await selectSession(sid);
}

function replayMessage(m) {
  switch (m.role) {
    case "user": renderUser(m.content); break;
    case "assistant": renderAssistant(m.content); break;
    case "system": renderSystem(m.content, m.level); break;
    case "tool_call": buildToolCard(m.call_id, m.tool, m.params); break;
    case "tool_result": { const c = cardByCall(m.call_id); if (c) setToolResult(c, { success: m.success, preview: m.truncated_preview, error: m.error, output_id: m.output_id }); break; }
    default: break;
  }
}

async function createSession() {
  const r = await api("/api/sessions", { method: "POST", body: JSON.stringify({}) });
  state.sessionId = r.session.id;
  clearTranscript();
  refreshStatus();
  input.focus();
}

async function refreshStatus() {
  if (!state.sessionId) return;
  try {
    const s = await api(`/api/status?session=${state.sessionId}`);
    const prov = $("#st-provider .label");
    prov.textContent = `nemapi · ${s.model || s.host || "—"}${s.connected ? "" : " (hors ligne)"}`;
    $("#st-provider").className = "chip " + (s.connected ? "ok" : "err");
    $("#st-workspace").textContent = s.workspace || "";
    $("#st-mode").classList.toggle("hidden", !s.plan_mode);
    $("#st-session").textContent = "sess " + (state.sessionId || "").slice(0, 8);
    // bannière d'accueil (panel facon Rich de header.py)
    const put = (sel, val) => { const n = $(sel); if (n) n.textContent = val; };
    put("#w-provider", "NEMAPI");
    put("#w-model", s.model || "—");
    put("#w-endpoint", s.host || "—");
    put("#w-workspace", s.workspace || "—");
    put("#tb-model", s.model ? `${s.model}` : "");
    const st = $("#w-status");
    if (st) {
      st.textContent = s.connected === true ? "connected" : s.connected === false ? "offline" : "not tested";
      st.className = "v " + (s.connected === true ? "ok" : s.connected === false ? "err" : "warn");
    }
  } catch (_) {}
}
function fmtNum(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }

// ------------------------------------------------------------------ composer
const input = $("#input");
const cmdMenu = $("#cmd-menu");

function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 220) + "px"; }
input.addEventListener("input", () => { autosize(); updateMenu(); });

async function submit() {
  const text = input.value.trim(); if (!text) return;
  if (state.history[state.history.length - 1] !== text) state.history.push(text);
  state.historyIdx = state.history.length;
  if (!state.sessionId) await createSession();
  input.value = ""; autosize(); closeMenu();
  if (text.startsWith("/")) {
    runCommand(text);
  } else if (state.pendingAuth) {
    // an authorization is pending: plain text answers it (y/a/n)
    const c = text[0].toLowerCase();
    if (c === "y" || c === "a" || c === "n") resolveAuth(c);
    else renderSystem("réponse attendue: y / a / n", "warning");
  } else {
    renderUser(text);
    try { await api(`/api/sessions/${state.sessionId}/message`, { method: "POST", body: JSON.stringify({ text }) }); }
    catch (e) { renderSystem("Envoi impossible: " + e.message, "error"); }
  }
}
$("#btn-send").addEventListener("click", submit);

async function runCommand(cmdline) {
  try {
    const r = await api(`/api/sessions/${state.sessionId}/command`, { method: "POST", body: JSON.stringify({ command: cmdline }) });
    if (r.type === "choice") { renderChoice(r); return; }
    if (r.type === "internal_prompt") {
      if (r.output) renderCommand(`$ ${cmdline}\n${r.output}`);
      renderSystem("→ instruction envoyée à l'agent");
      return;
    }
    if (r.type === "commands") { renderCommand("$ /\n" + r.commands.map(c => "/" + c).join("\n")); return; }
    if (r.type === "error") { renderSystem(r.message, "error"); return; }
    const txt = (typeof r.text === "string" && r.text) || r.output || (r.result != null ? JSON.stringify(r.result, null, 2) : null);
    if (txt) renderCommand(`$ ${cmdline}\n${txt}`);
    const name = cmdline.trim().split(/\s+/)[0];
    if (["/resume", "/clear", "/new", "/models", "/model", "/config"].includes(name)) refreshStatus();
    input.focus();
  } catch (e) {
    renderSystem("Commande échouée: " + e.message, "error");
  }
}

// command palette autocomplete
async function ensureCommands() { if (!state.commands.length) { try { state.commands = (await api("/api/commands")).commands || []; } catch {} } }
function updateMenu() {
  const v = input.value;
  if (!v.startsWith("/") || /\s/.test(v)) { closeMenu(); return; }
  const q = v.slice(1).toLowerCase();
  const items = state.commands.filter(c => c.name.slice(1).startsWith(q));
  if (!items.length) { closeMenu(); return; }
  state.menuItems = items; state.menuSel = 0; state.menuOpen = true;
  renderMenu();
}
function renderMenu() {
  cmdMenu.innerHTML = "";
  state.menuItems.forEach((c, i) => {
    const it = el("div", "cmd-item" + (i === state.menuSel ? " sel" : ""));
    it.appendChild(el("span", "cmd-name", escapeHtml(c.name)));
    it.appendChild(el("span", "cmd-desc", escapeHtml(c.description || "")));
    if (c.usage) it.appendChild(el("span", "cmd-usage", escapeHtml(c.usage)));
    it.addEventListener("mousedown", (e) => { e.preventDefault(); acceptMenu(c); });
    cmdMenu.appendChild(it);
  });
  cmdMenu.classList.remove("hidden");
}
function acceptMenu(c) {
  const name = c && c.name ? c.name : c;
  const takesArgs = !!(c && c.takes_args);
  const interactive = !!(c && c.interactive);
  closeMenu();
  if (name && !takesArgs && !interactive) {
    input.value = name;
    autosize();
    submit();
    return;
  }
  input.value = name + " ";
  input.focus();
  autosize();
}
function closeMenu() { state.menuOpen = false; cmdMenu.classList.add("hidden"); }

// keyboard
input.addEventListener("keydown", (e) => {
  if (state.menuOpen) {
    if (e.key === "ArrowDown") { e.preventDefault(); state.menuSel = (state.menuSel + 1) % state.menuItems.length; renderMenu(); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); state.menuSel = (state.menuSel - 1 + state.menuItems.length) % state.menuItems.length; renderMenu(); return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); acceptMenu(state.menuItems[state.menuSel]); return; }
    if (e.key === "Escape") { closeMenu(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); return; }
  if (e.key === "ArrowUp" && input.selectionStart === 0) {
    if (state.history.length) { e.preventDefault(); state.historyIdx = Math.max(0, state.historyIdx - 1); input.value = state.history[state.historyIdx] || ""; autosize(); }
    return;
  }
  if (e.key === "ArrowDown" && input.selectionStart === input.value.length) {
    if (state.history.length) {
      e.preventDefault();
      state.historyIdx = Math.min(state.history.length, state.historyIdx + 1);
      input.value = state.historyIdx >= state.history.length ? "" : (state.history[state.historyIdx] || "");
      autosize();
    }
    return;
  }
});

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.value = "/"; input.focus(); autosize(); updateMenu(); return; }
  if (state.pendingAuth && document.activeElement !== input) {
    if (e.key === "y") resolveAuth("y");
    else if (e.key === "n") resolveAuth("n");
    else if (e.key === "a") resolveAuth("a");
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && document.activeElement !== input) interrupt();
});

async function interrupt() {
  if (!state.sessionId) return;
  try { await api(`/api/sessions/${state.sessionId}/interrupt`, { method: "POST" }); } catch (_) {}
}
$("#btn-interrupt").addEventListener("click", interrupt);

// ascii banner (exact NEMESIS-CLI logo, same as src/ui/header.py)
const ASCII_LOGO = [
  " _   _ _____ __  __ _____ ____ ___ ____",
  "| \\ | | ____|  \\/  | ____/ ___|_ _/ ___|",
  "|  \\| |  _| | |\\/| |  _| \\___ \\| |\\___ \\",
  "| |\\  | |___| |  | | |___ ___) | | ___) |",
  "|_| \\_|_____|_|  |_|_____|____/___|____/",
  "                 NEMESIS-CLI",
].join("\n");
(function initBanner() {
  const logo = $("#ascii-logo");
  if (logo) logo.textContent = ASCII_LOGO;
})();

// boot: no session persisted between runs — always start fresh
(async function boot() {
  await ensureCommands();
  connectEvents();
  await createSession();
  autosize(); input.focus();
})();
