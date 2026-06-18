const $ = (selector) => document.querySelector(selector);

function fmtTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function fmtPct(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function renderMetrics(summary) {
  const items = [
    ["Trace 总数", summary.total_traces],
    ["成功率", fmtPct(summary.success_rate)],
    ["平均耗时", `${summary.avg_trace_latency_ms || 0} ms`],
    ["LLM 调用", summary.llm_calls],
    ["失败调用", summary.failed_llm_calls],
    ["Fallback", summary.fallback_count],
    ["评测次数", summary.eval_runs],
  ];
  $("#metrics").innerHTML = items.map(([label, value]) => `
    <div class="metric">
      <strong>${value}</strong>
      <span>${label}</span>
    </div>
  `).join("");
}

function renderTraces(items) {
  $("#traceList").innerHTML = items.map((item) => `
    <div class="trace-item" data-trace-id="${item.trace_id}">
      <strong>${item.user_message || "(空问题)"}</strong>
      <div class="meta">
        ${item.trace_id} · ${item.status} · ${item.latency_ms || 0} ms · ${fmtTime(item.started_at)}
      </div>
    </div>
  `).join("") || "<div class='row'>暂无 Trace</div>";

  document.querySelectorAll("[data-trace-id]").forEach((node) => {
    node.addEventListener("click", async () => {
      const detail = await fetchJson(`/api/admin/traces/${node.dataset.traceId}/timeline`);
      renderTimeline(detail.timeline);
    });
  });
}

function renderTimeline(items) {
  $("#traceDetail").innerHTML = items.map((item) => `
    <div class="timeline-item">
      <strong>${item.title}${item.name ? ` · ${item.name}` : ""}</strong>
      <div class="meta">${item.type} · ${fmtTime(item.time)}</div>
      ${item.summary ? `<div>${escapeHtml(item.summary)}</div>` : ""}
      <code>${escapeHtml(JSON.stringify(item.payload, null, 2))}</code>
    </div>
  `).join("") || "暂无时间线事件";
}

function renderLlmCalls(items) {
  $("#llmCalls").innerHTML = items.map((item) => `
    <div class="row">
      <strong>${item.provider} / ${item.model}</strong>
      <div class="meta ${item.status === "success" ? "" : "status-error"}">
        ${item.status} · route ${item.model_route || "-"} · ${item.latency_ms} ms · tokens ${item.total_tokens || "-"} · ${fmtTime(item.created_at)}
      </div>
      ${item.error ? `<div class="meta">${item.error}</div>` : ""}
    </div>
  `).join("") || "<div class='row'>暂无 LLM 调用</div>";
}

function renderEvalTrends(data) {
  const runs = data.runs || [];
  $("#evalRuns").innerHTML = runs.map((item) => {
    const hitRate = item.metrics.hit_rate ?? item.metrics.faithfulness ?? 0;
    return `
    <div class="row">
      <strong>${item.mode} · ${item.num_queries} queries</strong>
      <div class="meta">
        top_k ${item.top_k || "-"} · ${fmtTime(item.created_at)}
      </div>
      <div class="meta">${JSON.stringify(item.metrics)}</div>
      <div class="bar"><span style="width:${Math.max(0, Math.min(100, hitRate * 100))}%"></span></div>
    </div>
  `}).join("") || "<div class='row'>暂无评测历史</div>";
}

function renderRouteStats(items) {
  const counts = {};
  items.forEach((item) => {
    const route = item.model_route || "unrouted";
    counts[route] = (counts[route] || 0) + 1;
  });
  $("#routeStats").innerHTML = Object.entries(counts).map(([route, count]) => `
    <div class="row">
      <strong>${route}</strong>
      <div class="meta">${count} calls</div>
    </div>
  `).join("") || "<div class='row'>暂无路由数据</div>";
}

function renderQuota(items) {
  $("#quotaUsage").innerHTML = items.map((item) => `
    <div class="row">
      <strong>${item.subject}</strong>
      <div class="meta">${item.usage_date}</div>
      <div class="meta">requests ${item.request_count}/${item.request_limit || "∞"} · tokens ${item.token_count}/${item.token_limit || "∞"}</div>
    </div>
  `).join("") || "<div class='row'>暂无配额数据</div>";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadDashboard() {
  const [summary, traces, llmCalls, evalTrends, quota] = await Promise.all([
    fetchJson("/api/admin/summary"),
    fetchJson("/api/admin/traces"),
    fetchJson("/api/admin/llm-calls"),
    fetchJson("/api/admin/eval-trends"),
    fetchJson("/api/admin/quota"),
  ]);
  renderMetrics(summary);
  renderTraces(traces.items);
  renderLlmCalls(llmCalls.items);
  renderEvalTrends(evalTrends);
  renderRouteStats(llmCalls.items);
  renderQuota(quota.items);
}

$("#refreshBtn").addEventListener("click", loadDashboard);
loadDashboard().catch((error) => {
  $("#traceDetail").textContent = `加载失败：${error.message}`;
});
