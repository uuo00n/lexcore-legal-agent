// 法律咨询助手 — 前端主逻辑
const $ = (sel) => document.querySelector(sel);

const state = {
  threadId: localStorage.getItem("legal.threadId") || newId(),
  doc: null,        // {doc_id, filename, char_count, truncated}
  evidence: null,   // {evidence_id, filename, status, frame_count}
  busyThreads: new Set(),
  streams: new Map(),
  compacting: false,
  contextStatus: null,
};

const DOC_EXTS = new Set([".pdf", ".docx", ".txt"]);
const VIDEO_EXTS = new Set([".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".ts"]);

function newId() {
  return crypto.randomUUID ? crypto.randomUUID() : `tid-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fileExt(name) {
  const idx = String(name || "").lastIndexOf(".");
  return idx >= 0 ? String(name).slice(idx).toLowerCase() : "";
}

function setThread(id) {
  state.threadId = id;
  localStorage.setItem("legal.threadId", id);
}

function isThreadBusy(threadId = state.threadId) {
  return state.busyThreads.has(threadId);
}

function setThreadBusy(threadId, busy) {
  if (busy) {
    state.busyThreads.add(threadId);
  } else {
    state.busyThreads.delete(threadId);
  }
  renderBusyState();
}

function renderBusyState() {
  const send = $("#send");
  if (send) send.disabled = isThreadBusy();
  renderSlashMenu();
  renderContextStatus(state.contextStatus);
}

// ----- DOM 渲染 -----
function appendUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
  $("#messages").appendChild(el);
  scrollBottom();
  return el;
}

function appendAssistant() {
  const el = document.createElement("div");
  el.className = "msg assistant streaming";
  el.dataset.raw = "";
  el.innerHTML = '<span class="pending">正在分析...</span>';
  $("#messages").appendChild(el);
  scrollBottom();
  return el;
}

function appendError(text) {
  const el = document.createElement("div");
  el.className = "msg error";
  el.textContent = `❌ ${text}`;
  $("#messages").appendChild(el);
  scrollBottom();
}

function appendThought(text) {
  const el = document.createElement("div");
  el.className = "thought-card";
  el.textContent = "处理过程：" + text;
  $("#messages").appendChild(el);
  scrollBottom();
  return el;
}

function appendLawCard(laws) {
  const el = document.createElement("div");
  el.className = "tool-card";
  if (!Array.isArray(laws) || laws.length === 0) {
    el.textContent = "🔍 法律检索：本地法库未命中相关条款";
  } else {
    const summary = `📚 已检索到 ${laws.length} 条相关法律（点击展开）`;
    el.innerHTML = `<details><summary>${summary}</summary></details>`;
    const container = el.querySelector("details");
    laws.forEach((law) => {
      const item = document.createElement("div");
      item.className = "law-item";
      const title = document.createElement("div");
      title.className = "law-title";
      title.textContent = law.title || "（无标题）";
      const content = document.createElement("div");
      content.textContent = law.content || "";
      item.appendChild(title);
      item.appendChild(content);
      container.appendChild(item);
    });
  }
  $("#messages").appendChild(el);
  scrollBottom();
}

function insertBeforeAssistant(el, aiMsg) {
  const messages = $("#messages");
  if (aiMsg && aiMsg.parentNode === messages) {
    messages.insertBefore(el, aiMsg);
    return;
  }
  if (el.parentNode !== messages) {
    messages.appendChild(el);
  }
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderAssistantContent(text) {
  let html = escapeHtml(text || "");
  html = html.replace(/^###\s+(.+)$/gm, "<strong>$1</strong>");
  html = html.replace(/^##\s+(.+)$/gm, "<strong>$1</strong>");
  html = html.replace(/^#\s+(.+)$/gm, "<strong>$1</strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\n/g, "<br>");
  return html;
}

function normalizeLaws(output) {
  if (Array.isArray(output)) {
    return output;
  }
  if (!output || typeof output !== "object") {
    return [];
  }
  if (Array.isArray(output.results)) {
    return output.results;
  }
  if (Array.isArray(output.relevant_laws)) {
    return output.relevant_laws;
  }
  if (output.law_a && output.law_b) {
    const left = Array.isArray(output.law_a.articles)
      ? output.law_a.articles.map((item) => ({ ...item, law_name: output.law_a.name }))
      : [];
    const right = Array.isArray(output.law_b.articles)
      ? output.law_b.articles.map((item) => ({ ...item, law_name: output.law_b.name }))
      : [];
    return [...left, ...right];
  }
  return [];
}

function scrollBottom() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

function clearMessages() {
  $("#messages").innerHTML = "";
}

// ----- doc chip -----
function renderDocChip() {
  const chip = $("#doc-chip");
  if (!state.doc) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  chip.querySelector(".doc-name").textContent = `📄 ${state.doc.filename}`;
  const meta = `${state.doc.char_count.toLocaleString()} 字${state.doc.truncated ? " · 已截断" : ""}`;
  chip.querySelector(".doc-meta").textContent = meta;
}

function renderEvidenceChip() {
  const chip = $("#evidence-chip");
  if (!chip) return;
  if (!state.evidence) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  chip.querySelector(".evidence-name").textContent = `🎞️ ${state.evidence.filename}`;
  const status = state.evidence.status || "queued";
  const count = Number(state.evidence.frame_count || 0);
  const meta = status === "success"
    ? `${count} 张截图`
    : status === "dependency_missing"
      ? "缺少 ffmpeg"
      : "处理中";
  chip.querySelector(".evidence-meta").textContent = meta;
}

function formatTokens(value) {
  const n = Number(value || 0);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function renderContextStatus(status) {
  if (!status) return;
  const percent = $("#context-percent");
  const bar = $("#context-bar");
  const barFill = $("#context-bar span");
  const meta = $("#context-meta");
  const compactButton = $("#compact-context");
  if (!percent || !bar || !barFill || !meta || !compactButton) return;
  state.contextStatus = status;
  const ratio = Math.max(0, Math.min(1.5, Number(status.usage_ratio || 0)));
  const pct = Math.round(ratio * 100);
  const fill = Math.min(100, pct);
  percent.textContent = `${pct}%`;
  barFill.style.width = `${fill}%`;
  bar.classList.toggle("warn", ratio >= Number(status.auto_compact_ratio || 0.75));
  bar.classList.toggle("full", ratio >= 1);
  meta.textContent = `${formatTokens(status.estimated_tokens)} / ${formatTokens(status.token_budget)} tokens`;
  compactButton.disabled = isThreadBusy() || state.compacting || !status.compactable_messages;
}

function renderSlashMenu() {
  const menu = $("#slash-menu");
  const input = $("#input");
  if (!menu || !input) return;
  const value = input.value.trim();
  menu.hidden = isThreadBusy() || !value.startsWith("/");
}

function describeContextStatus() {
  const status = state.contextStatus;
  if (!status) return "上下文状态还没有加载完成。";
  const pct = Math.round(Number(status.usage_ratio || 0) * 100);
  const compactable = Number(status.compactable_messages || 0);
  return `上下文窗口已用 ${pct}%，约 ${formatTokens(status.estimated_tokens)} / ${formatTokens(status.token_budget)} tokens，可压缩旧消息 ${compactable} 条。`;
}

function showSlashTools() {
  appendThought("可用命令：/context 查看上下文用量；/compact 主动压缩上下文；/tools 查看命令。");
}

function handleSlashCommand(text) {
  const command = text.trim().split(/\s+/, 1)[0].toLowerCase();
  const menu = $("#slash-menu");
  if (menu) menu.hidden = true;
  if (command === "/context") {
    appendThought(describeContextStatus());
    refreshContextStatus();
    return true;
  }
  if (command === "/compact") {
    compactContext();
    return true;
  }
  showSlashTools();
  return true;
}

async function refreshContextStatus() {
  try {
    const res = await fetch(`/api/threads/${state.threadId}/context`);
    if (!res.ok) return;
    const status = await res.json();
    renderContextStatus(status);
  } catch {
    // ignore
  }
}

async function compactContext() {
  if (isThreadBusy() || state.compacting) return;
  const compactButton = $("#compact-context");
  state.compacting = true;
  if (compactButton) {
    compactButton.disabled = true;
    compactButton.textContent = "压缩中";
  }
  try {
    const res = await fetch(`/api/threads/${state.threadId}/compact`, { method: "POST" });
    if (!res.ok) {
      appendError(`压缩失败 (${res.status})：${await res.text()}`);
      return;
    }
    const payload = await res.json();
    renderContextStatus(payload.context_status);
    appendThought(payload.compacted ? "已主动压缩上下文并更新实体记忆。" : "当前上下文暂不需要压缩。");
  } catch (err) {
    appendError(`压缩异常：${err.message}`);
  } finally {
    state.compacting = false;
    if (compactButton) compactButton.textContent = "压缩";
    refreshContextStatus();
  }
}

$("#doc-chip .doc-remove").addEventListener("click", () => {
  state.doc = null;
  renderDocChip();
});

$("#evidence-chip .evidence-remove").addEventListener("click", () => {
  state.evidence = null;
  renderEvidenceChip();
});

// ----- 上传 -----
$("#file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const ext = fileExt(file.name);
  if (DOC_EXTS.has(ext)) {
    await uploadDocument(file);
  } else if (VIDEO_EXTS.has(ext)) {
    await uploadVideoEvidence(file);
  } else {
    appendError(`不支持的文件类型：${ext || "未知"}`);
  }
  e.target.value = "";
});

async function uploadDocument(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      appendError(`上传失败：${err.detail || res.statusText}`);
      return;
    }
    state.doc = await res.json();
    renderDocChip();
  } catch (err) {
    appendError(`上传异常：${err.message}`);
  }
}

async function uploadVideoEvidence(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    appendThought("正在上传视频证据并创建抽帧任务...");
    const res = await fetch("/api/evidence/video/extract", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      appendError(`视频上传失败：${err.detail || res.statusText}`);
      return;
    }
    const payload = await res.json();
    state.evidence = {
      evidence_id: payload.evidence_id,
      filename: payload.filename,
      task_id: payload.task_id,
      status: "queued",
      frame_count: 0,
    };
    renderEvidenceChip();
    pollEvidenceTask(payload.task_id, payload.evidence_id);
  } catch (err) {
    appendError(`视频上传异常：${err.message}`);
  }
}

async function pollEvidenceTask(taskId, evidenceId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) continue;
      const task = await res.json();
      if (task.status === "queued" || task.status === "running") continue;
      if (task.status === "error") {
        if (state.evidence && state.evidence.evidence_id === evidenceId) {
          state.evidence.status = "error";
          renderEvidenceChip();
        }
        appendError(`视频抽帧失败：${task.error || "未知错误"}`);
        return;
      }
      const result = task.result || {};
      if (state.evidence && state.evidence.evidence_id === evidenceId) {
        state.evidence.status = result.status || "success";
        state.evidence.frame_count = result.kept_after_dedup || 0;
        renderEvidenceChip();
      }
      if (result.status === "dependency_missing") {
        appendError(`视频已上传，但本机缺少依赖：${(result.missing || []).join(", ")}`);
      } else {
        appendThought(`视频证据处理完成：已提取 ${result.kept_after_dedup || 0} 张截图。`);
      }
      return;
    } catch {
      // retry
    }
  }
  appendError("视频抽帧任务仍在处理中，请稍后查看任务状态。");
}

// ----- 流式聊天 -----
function createStreamState(threadId, userText, aiMsg) {
  const stream = {
    threadId,
    userText,
    aiMsg,
    raw: aiMsg ? (aiMsg.dataset.raw || "") : "",
    cards: [],
    done: false,
  };
  state.streams.set(threadId, stream);
  return stream;
}

function renderToolEndCard(laws) {
  const el = document.createElement("div");
  el.className = "tool-card";
  if (!Array.isArray(laws) || laws.length === 0) {
    el.textContent = "🔍 法律检索：本地法库未命中相关条款";
  } else {
    const summary = `📚 已检索到 ${laws.length} 条相关法律（点击展开）`;
    el.innerHTML = `<details><summary>${summary}</summary></details>`;
    const container = el.querySelector("details");
    laws.forEach((law) => {
      const item = document.createElement("div");
      item.className = "law-item";
      const title = document.createElement("div");
      title.className = "law-title";
      title.textContent = law.title || `${law.law_name || ""} ${law.article_no || ""}`.trim() || "（无标题）";
      const content = document.createElement("div");
      content.textContent = law.content || "";
      item.appendChild(title);
      item.appendChild(content);
      container.appendChild(item);
    });
  }
  return el;
}

function renderStreamCard(card) {
  if (card.kind === "thought") {
    return createThoughtCard(card.text);
  }
  if (card.kind === "tool_start") {
    const el = document.createElement("div");
    el.className = "tool-card";
    el.textContent = "🔍 正在检索法条...";
    return el;
  }
  if (card.kind === "tool_end") {
    return renderToolEndCard(card.laws);
  }
  if (card.kind === "error") {
    const el = document.createElement("div");
    el.className = "msg error";
    el.textContent = `❌ ${card.text}`;
    return el;
  }
  return null;
}

function renderStreamForCurrentThread() {
  const stream = state.streams.get(state.threadId);
  if (!stream) return;

  appendUser(stream.userText);
  stream.cards.forEach((card) => {
    const el = renderStreamCard(card);
    if (el) $("#messages").appendChild(el);
  });
  const aiMsg = appendAssistant();
  aiMsg.dataset.raw = stream.raw || "";
  aiMsg.innerHTML = stream.raw ? renderAssistantContent(stream.raw) : '<span class="pending">正在分析...</span>';
  if (stream.done) aiMsg.classList.remove("streaming");
  stream.aiMsg = aiMsg;
  scrollBottom();
}

function createThoughtCard(text) {
  const el = document.createElement("div");
  el.className = "thought-card";
  el.textContent = "处理过程：" + text;
  return el;
}

function addStreamCard(stream, card) {
  stream.cards.push(card);
  if (stream.threadId !== state.threadId) return;
  const el = renderStreamCard(card);
  if (!el) return;
  insertBeforeAssistant(el, stream.aiMsg);
  scrollBottom();
}

async function sendMessage(text) {
  const requestThreadId = state.threadId;
  const requestDocId = state.doc ? state.doc.doc_id : null;
  const requestEvidenceId = state.evidence ? state.evidence.evidence_id : null;
  if (isThreadBusy(requestThreadId)) return;
  setThreadBusy(requestThreadId, true);
  appendUser(text);

  const aiMsg = appendAssistant();
  const stream = createStreamState(requestThreadId, text, aiMsg);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: requestThreadId,
        message: text,
        doc_id: requestDocId,
        evidence_id: requestEvidenceId,
      }),
    });

    if (!res.ok || !res.body) {
      const errorText = `请求失败 (${res.status})：${await res.text()}`;
      if (requestThreadId === state.threadId) {
        aiMsg.classList.remove("streaming");
        appendError(errorText);
      } else {
        stream.cards.push({ kind: "error", text: errorText });
      }
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // sse-starlette 使用 \r\n 作为行分隔符，事件间用 \r\n\r\n 分隔
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() || "";
      for (const block of events) {
        const evt = parseSSEBlock(block);
        if (!evt) continue;
        handleEvent(evt, stream);
      }
    }
  } catch (err) {
    const errorText = `通信异常：${err.message}`;
    if (requestThreadId === state.threadId) {
      appendError(errorText);
    } else {
      stream.cards.push({ kind: "error", text: errorText });
    }
  } finally {
    stream.done = true;
    if (requestThreadId === state.threadId && stream.aiMsg) {
      stream.aiMsg.classList.remove("streaming");
    }
    setThreadBusy(requestThreadId, false);
    refreshThreads();
    refreshContextStatus();
  }
}

function parseSSEBlock(block) {
  const lines = block.split(/\r?\n/);
  let event = "message";
  const dataParts = [];
  for (const ln of lines) {
    if (ln.startsWith("event:")) event = ln.slice(6).trim();
    else if (ln.startsWith("data:")) dataParts.push(ln.slice(5));
    else if (ln.startsWith("data")) dataParts.push("");
  }
  // SSE 标准：多行 data 用换行连接
  const data = dataParts.join("\n").trim();
  return { event, data };
}

function normalizeStreamTarget(target) {
  if (target && target.threadId) return target;
  return {
    threadId: state.threadId,
    aiMsg: target,
    raw: target && target.dataset ? target.dataset.raw || "" : "",
    cards: [],
    done: false,
  };
}

function handleEvent(evt, streamTarget) {
  const stream = normalizeStreamTarget(streamTarget);
  if (evt.event === "token") {
    stream.raw = (stream.raw || "") + evt.data;
    if (stream.threadId === state.threadId && stream.aiMsg) {
      stream.aiMsg.dataset.raw = stream.raw;
      stream.aiMsg.innerHTML = renderAssistantContent(stream.raw);
      scrollBottom();
    }
  } else if (evt.event === "thought") {
    // 处理过程卡片：只展示可控的流程状态，不展示模型内部推理。
    try {
      const payload = JSON.parse(evt.data);
      addStreamCard(stream, { kind: "thought", text: payload.content || evt.data });
    } catch {
      addStreamCard(stream, { kind: "thought", text: evt.data });
    }
  } else if (evt.event === "context_status") {
    if (stream.threadId === state.threadId) {
      try {
        renderContextStatus(JSON.parse(evt.data));
      } catch {
        // ignore
      }
    }
  } else if (evt.event === "tool_start") {
    addStreamCard(stream, { kind: "tool_start" });
  } else if (evt.event === "tool_end") {
    try {
      const payload = JSON.parse(evt.data);
      const laws = normalizeLaws(payload.output);
      addStreamCard(stream, { kind: "tool_end", laws });
    } catch {
      // ignore
    }
  } else if (evt.event === "error") {
    try {
      const err = JSON.parse(evt.data);
      addStreamCard(stream, { kind: "error", text: err.message || "未知错误" });
    } catch {
      addStreamCard(stream, { kind: "error", text: evt.data || "未知错误" });
    }
  } else if (evt.event === "done" && !stream.raw && stream.threadId === state.threadId && stream.aiMsg) {
    stream.aiMsg.textContent = "本轮没有生成回复，请稍后重试。";
  }
}

// ----- 输入交互 -----
$("#send").addEventListener("click", () => {
  const text = $("#input").value.trim();
  if (!text) return;
  $("#input").value = "";
  const menu = $("#slash-menu");
  if (menu) menu.hidden = true;
  if (text.startsWith("/") && handleSlashCommand(text)) return;
  sendMessage(text);
});

$("#input").addEventListener("input", renderSlashMenu);

$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#send").click();
  }
});

const slashMenu = $("#slash-menu");
if (slashMenu) slashMenu.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-command]");
  if (!button) return;
  const command = button.dataset.command;
  $("#input").value = "";
  handleSlashCommand(command);
});

// ----- 会话列表 -----
async function refreshThreads() {
  try {
    const res = await fetch("/api/threads");
    if (!res.ok) return;
    const data = await res.json();
    const list = $("#thread-list");
    list.innerHTML = "";
    for (const t of data.threads) {
      const li = document.createElement("li");
      if (t.thread_id === state.threadId) li.classList.add("active");
      if (isThreadBusy(t.thread_id)) li.classList.add("generating");
      const title = document.createElement("span");
      title.textContent = `${t.title || "新对话"}${isThreadBusy(t.thread_id) ? "（生成中）" : ""}`;
      title.style.flex = "1";
      title.style.overflow = "hidden";
      title.style.textOverflow = "ellipsis";
      const del = document.createElement("button");
      del.className = "del-btn";
      del.textContent = "✕";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`删除会话「${t.title}」？`)) return;
        await fetch(`/api/threads/${t.thread_id}`, { method: "DELETE" });
        if (t.thread_id === state.threadId) {
          startNewThread();
        } else {
          refreshThreads();
        }
      });
      li.addEventListener("click", () => switchThread(t.thread_id));
      li.appendChild(title);
      li.appendChild(del);
      list.appendChild(li);
    }
  } catch {
    // ignore
  }
}

async function switchThread(id) {
  setThread(id);
  state.doc = null;
  state.evidence = null;
  renderDocChip();
  renderEvidenceChip();
  clearMessages();
  renderBusyState();
  try {
    const res = await fetch(`/api/threads/${id}/history`);
    if (res.ok) {
      const { messages } = await res.json();
      if (state.threadId === id) {
        clearMessages();
        for (const m of messages) {
          if (m.role === "user") {
            appendUser(m.content);
          } else if (m.role === "assistant" && m.content) {
            const el = document.createElement("div");
            el.className = "msg assistant";
            el.textContent = m.content;
            $("#messages").appendChild(el);
          }
        }
        renderStreamForCurrentThread();
        scrollBottom();
      }
    }
  } catch {
    // ignore
  }
  refreshThreads();
  refreshContextStatus();
}

function startNewThread() {
  const id = newId();
  setThread(id);
  state.doc = null;
  state.evidence = null;
  renderDocChip();
  renderEvidenceChip();
  clearMessages();
  renderBusyState();
  refreshContextStatus();
  refreshThreads();
}

$("#new-chat").addEventListener("click", startNewThread);
const compactButton = $("#compact-context");
if (compactButton) compactButton.addEventListener("click", compactContext);

// ----- 健康/provider 标签 -----
async function loadProvider() {
  try {
    const res = await fetch("/api/health");
    if (res.ok) {
      const data = await res.json();
      $("#provider-tag").textContent = `LLM: ${data.provider}`;
    }
  } catch {
    $("#provider-tag").textContent = "LLM: ?";
  }
}

// ----- 启动 -----
(async function init() {
  loadProvider();
  await switchThread(state.threadId);
  refreshContextStatus();
})();
