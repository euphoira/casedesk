"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const state = { config: null, tickets: [], analyses: new Map(), selected: null, ticket: null, analysis: null, audit: [], filter: "all", query: "", tab: "analysis", busy: new Set(), version: 0, contexts: [], search: null };
const labels = { billing: "账单与退款", shipping: "配送问题", account: "账户安全", other: "其他问题", open: "待处理", escalated: "已升级", closed: "已关闭", refund: "模拟退款", coupon: "模拟优惠券", request_info: "请求补充信息", escalate: "升级人工专员", pending: "待人工审批", approved: "已批准", rejected: "已拒绝", low: "低", medium: "中", high: "高" };
const eventNames = { ticket_created: "创建工单", ticket_updated: "更新工单状态与版本", analysis_started: "开始分析工单", knowledge_retrieved: "检索政策知识库", model_call: "调用语言模型", tool_result: "获取工具结果", analysis_completed: "完成分析与政策校验", analysis_failed: "分析失败", proposal_created: "生成待审批提案", proposal_approved: "人工批准提案", proposal_rejected: "人工拒绝提案", effect_created: "写入本地模拟操作", analysis_recovered: "恢复中断分析", approval_replayed: "返回已执行的幂等结果" };
Object.assign(eventNames, { policy_evaluated: "完成确定性政策校验", draft_ready: "回复草稿与提案已就绪", analysis_interrupted: "分析被中断", approval_policy_checked: "审批时重新核验政策", simulation_applied: "保存本地模拟操作", approval_denied: "审批请求被拦截" });
const names = { "demo-alice": "Alice · 演示客户", "demo-bob": "Bob · 演示客户", "demo-carol": "Carol · 演示客户", "demo-dan": "Dan · 演示客户" };
names["demo-erin"] = "Erin · 演示客户";
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
const e = escapeHtml;
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const label = value => labels[value] || value || "—";
const money = cents => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(Number(cents || 0) / 100);
const time = value => value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const person = ticket => names[ticket.account_id] || ticket.account_id;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  let body;
  try { body = await response.json(); } catch { throw new Error("服务返回了无法读取的内容，请确认本地服务正常运行。"); }
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "请求校验失败，请检查输入。";
    const error = new Error(detail); error.status = response.status; throw error;
  }
  return body;
}
async function optionalAnalysis(id) {
  try { return await api(`/api/tickets/${encodeURIComponent(id)}/analysis`); }
  catch (error) { if (error.status === 404) return null; throw error; }
}
function showError(error) { $("#global-error").textContent = error.message || "请求失败，请重试。"; $("#global-error").hidden = false; }
function clearError() { $("#global-error").hidden = true; }
let toastTimer;
function toast(message) { clearTimeout(toastTimer); $("#toast").textContent = message; $("#toast").hidden = false; toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500); }

function ticketState(ticket) {
  const analysis = state.analyses.get(ticket.id), proposal = analysis?.proposal;
  if (proposal?.status === "pending" && proposal.ticket_revision === ticket.revision && proposal.policy.allowed) return { text: "待人工审批", kind: "pending" };
  if (proposal?.status === "approved" || proposal?.status === "rejected" || ticket.status === "closed") return { text: proposal ? label(proposal.status) : "已关闭", kind: "done" };
  if (proposal?.status === "pending" && proposal.ticket_revision !== ticket.revision) return { text: "提案已过期", kind: "blocked" };
  if (proposal && !proposal.policy.allowed) return { text: "规则已拦截", kind: "blocked" };
  return { text: label(ticket.status), kind: "" };
}
function renderList() {
  $("#stat-total").textContent = state.tickets.length;
  $("#nav-count").textContent = state.tickets.length;
  $("#stat-pending").textContent = state.tickets.filter(ticket => ticketState(ticket).kind === "pending").length;
  $("#stat-done").textContent = state.tickets.filter(ticket => ticketState(ticket).kind === "done").length;
  const tickets = state.tickets.filter(ticket => {
    const matches = `${ticket.id} ${ticket.subject} ${ticket.body} ${person(ticket)}`.toLocaleLowerCase().includes(state.query.toLocaleLowerCase());
    return matches && (state.filter === "all" || ticketState(ticket).kind === state.filter);
  });
  $("#ticket-count").textContent = tickets.length;
  $("#ticket-list").innerHTML = tickets.length ? tickets.map(ticket => {
    const status = ticketState(ticket);
    return `<button type="button" class="ticket ${ticket.id === state.selected ? "selected" : ""}" data-ticket="${e(ticket.id)}" aria-pressed="${ticket.id === state.selected}"><span class="ticket-row"><span class="ticket-code">${e(ticket.id)}</span><span class="ticket-time">${e(time(ticket.created_at))}</span></span><h3>${e(ticket.subject)}</h3><p>${e(ticket.body)}</p><span class="ticket-row"><span class="ticket-person"><span class="tiny-avatar">${e(person(ticket).slice(0, 1).toUpperCase())}</span>${e(person(ticket).split(" · ")[0])}</span><span class="chip ${status.kind}">${e(status.text)}</span></span></button>`;
  }).join("") : '<div class="list-empty">没有符合条件的工单</div>';
}
function renderConfig() {
  const demo = state.config.mode === "demo";
  $("#mode-badge").textContent = demo ? "OFFLINE DEMO · 离线演示" : "LIVE MODEL · 本地模拟操作";
  $("#demo-notice").innerHTML = `<span class="notice-dot"></span><p><strong>${demo ? "离线演示模式" : "真实模型模式"}</strong>${demo ? "使用确定性演示规则，不调用大模型。" : `模型 ${e(state.config.model)} 已启用。`}所有审批操作均为本地模拟，不会发送消息或发起真实退款。</p>`;
  $("#about-mode").textContent = `${demo ? "当前使用离线确定性演示规则。" : `当前使用模型 ${state.config.model}。`}知识库版本：${state.config.corpus_revision}；政策版本：${state.config.policy_revision}。操作只写入本地模拟记录。`;
}
function evidenceMarkup(chunks, citations = []) {
  if (!chunks?.length) return '<div class="list-empty">暂无检索证据</div>';
  return chunks.map((chunk, index) => {
    const citation = citations.find(item => item.chunk_id === chunk.id);
    return `<div class="evidence-item"><span class="evidence-number">${index + 1}</span><div class="evidence-body"><h5>${e(chunk.title)} ${citation ? '<span class="chip done">已引用</span>' : ""}</h5><p>${e(citation?.quote || chunk.text)}</p><div class="evidence-ref">${e(chunk.id)} · ${e(chunk.revision)}${Number.isFinite(chunk.score) ? ` · BM25 ${Number(chunk.score).toFixed(2)}` : ""}</div></div></div>`;
  }).join("");
}
function proposalMarkup(analysis, ticket) {
  const proposal = analysis.proposal;
  if (!proposal) return '<div class="status-note">当前没有可审批提案。请查看分析状态或重新生成建议。</div>';
  const stale = proposal.status === "pending" && (proposal.ticket_revision !== ticket.revision || proposal.policy.policy_revision !== state.config?.policy_revision);
  if (proposal.status !== "pending") return `<div class="status-note">${icon("check")} 此提案${proposal.status === "approved" ? "已获人工批准，已保存本地模拟记录" : "已被人工拒绝"}。${proposal.status === "approved" ? "未发送消息、未执行真实退款。" : "未执行任何操作。"}</div>`;
  const parameters = proposal.policy.normalized_parameters || {};
  const amount = parameters.amount_cents != null ? ` · ${money(parameters.amount_cents)}` : "";
  return `${stale ? '<div class="alert"><strong>待审批提案已过期</strong>工单版本或政策已发生变化。请重新分析，获取与当前状态一致的建议。</div>' : ""}<div class="approval-card"><div class="approval-copy"><h4>${e(label(proposal.action))}${e(amount)}</h4><p>${stale ? `提案 v${proposal.ticket_revision} / 工单 v${ticket.revision}` : proposal.policy.allowed ? "规则校验通过，等待你的人工确认" : "业务规则未通过，无法批准此操作"} · 仅本地模拟</p></div><div class="approval-actions"><button class="button danger" type="button" data-action="reject" ${stale || state.busy.has(ticket.id) ? "disabled" : ""}>拒绝提案</button><button class="button primary" type="button" data-action="approve" ${stale || !proposal.policy.allowed || state.busy.has(ticket.id) ? "disabled" : ""}>${icon("check")}批准模拟操作</button></div></div>`;
}
function analysisMarkup() {
  const analysis = state.analysis, ticket = state.ticket;
  if (state.busy.has(ticket.id) && !analysis) return '<div class="analysis-placeholder"><h4>正在检索证据并生成建议…</h4><p>分析完成后仍需人工审批，不会直接执行操作。</p></div>';
  if (!analysis) return '<div class="analysis-placeholder"><h4>准备好上下文，再给出好答案。</h4><p>点击「生成处理建议」，开始检索政策与核对订单事实。</p></div>';
  if (analysis.status === "failed") return `<div class="alert error"><strong>本次分析未完成</strong>${e(analysis.error || "请重试分析。")}</div><div class="status-note">未创建可审批提案。你可以查看操作审计了解已完成的步骤。</div>`;
  if (analysis.status === "running") return '<div class="analysis-placeholder"><h4>分析正在运行</h4><p>稍后刷新工单查看最新结果。</p></div>';
  const policy = analysis.proposal?.policy;
  return `<div class="workflow" aria-label="处理流程"><div class="workflow-step"><b>1</b>检索证据</div><div class="workflow-step"><b>2</b>规则校验</div><div class="workflow-step"><b>3</b>生成草稿</div><div class="workflow-step ${analysis.proposal?.status === "pending" ? "current" : ""}"><b>4</b>${analysis.proposal?.status === "pending" ? "人工审批" : "已留痕"}</div></div><div class="analysis-stack">
    <section class="analysis-card evidence-card"><div class="card-heading"><h4>${icon("file")}检索依据</h4><small>${analysis.citations?.length || 0} 条核验引用 / ${analysis.retrieved?.length || 0} 条检索结果</small></div><div class="evidence-list">${evidenceMarkup(analysis.retrieved, analysis.citations)}</div><form class="search-evidence" id="evidence-form"><input name="query" id="evidence-query" aria-label="搜索知识库" placeholder="继续检索，例如：退款 30 天" maxlength="500" required><button class="button secondary" type="submit">${icon("search")}检索</button></form><div class="search-results" id="search-results">${state.search ? `<p class="search-title">独立检索结果 · ${e(state.search.revision)}（不会改写当前提案）</p>${evidenceMarkup(state.search.chunks)}` : ""}</div></section>
    <section class="analysis-card"><div class="card-heading"><h4>${icon("shield")}确定性政策校验</h4><small>由业务代码判定</small></div><div class="policy-body"><div class="policy-verdict ${policy?.allowed ? "" : "blocked"}">${icon(policy?.allowed ? "check" : "shield")}${policy?.allowed ? "符合政策，可进入人工审批" : "规则未通过，执行已拦截"}</div><p>${e(policy?.reason || "没有操作提案。")}</p>${policy ? `<ul><li>建议操作：${e(label(analysis.proposal.action))}</li><li>政策版本：${e(policy.policy_revision)}</li>${policy.normalized_parameters?.amount_cents != null ? `<li>服务端核定金额：${e(money(policy.normalized_parameters.amount_cents))}</li>` : ""}</ul>` : ""}</div></section>
    <section class="analysis-card"><div class="card-heading"><h4>${icon("spark")}客户回复草稿</h4><span class="chip agent">未发送 · 置信度${e(label(analysis.confidence))}</span></div><div class="draft-body">${e(analysis.draft)}</div><div class="draft-foot">${icon("shield")}草稿仅供人工审核，不会自动发送给客户</div></section>
    ${proposalMarkup(analysis, ticket)}
    ${analysis.limitations?.length ? `<div class="status-note">${analysis.limitations.map(e).join(" ")}</div>` : ""}
  </div>`;
}
function auditMarkup() {
  if (!state.audit.length) return '<div class="list-empty">暂无操作记录。生成建议后，检索与审批步骤会记录在这里。</div>';
  return `<ol class="audit-list">${state.audit.slice().reverse().map(event => `<li class="audit-event"><span class="audit-icon">${icon("check")}</span><div><h4>${e(eventNames[event.event] || event.event)}</h4><time>${e(time(event.created_at))} · ${e(event.event)}</time><details><summary>查看记录详情</summary><pre class="audit-data">${e(JSON.stringify(event.data, null, 2))}</pre></details></div></li>`).join("")}</ol>`;
}
function renderDetail() {
  const ticket = state.ticket;
  if (!ticket) return;
  const analysis = state.analysis, busy = state.busy.has(ticket.id);
  $("#detail-pane").innerHTML = `<header class="detail-header"><div class="detail-meta"><span class="ticket-code">${e(ticket.id)}</span><span>·</span><span>${e(label(ticket.category))}</span><span class="chip">${e(label(ticket.status))}</span><span>v${ticket.revision}</span></div><h2>${e(ticket.subject)}</h2><div class="customer-meta"><span class="customer-name"><span class="tiny-avatar">${e(person(ticket).slice(0, 1).toUpperCase())}</span>${e(person(ticket))}</span><span>${e(ticket.account.tier)} 账户</span><span>${ticket.account.verified ? "✓ 已验证" : "待完成验证"}</span><span>${e(time(ticket.created_at))}</span></div></header><div class="detail-content"><h3 class="section-label">客户的来信</h3><div class="customer-message">${e(ticket.body)}</div><div class="order-strip"><span>订单<strong>${e(ticket.order_id)}</strong></span><span>实付<strong>${e(money(ticket.order.amount_cents))}</strong></span><span>购买天数<strong>${ticket.order.days_since_purchase} 天</strong></span><span>送达<strong>${ticket.order.delivered ? "已送达" : "未送达"}</strong></span><span>退款<strong>${ticket.order.refunded ? "已模拟退款" : "未退款"}</strong></span></div><div class="ticket-status-control"><label for="ticket-status">工单状态</label><select id="ticket-status" ${busy ? "disabled" : ""}><option value="open" ${ticket.status === "open" ? "selected" : ""}>待处理</option><option value="escalated" ${ticket.status === "escalated" ? "selected" : ""}>已升级</option><option value="closed" ${ticket.status === "closed" ? "selected" : ""}>已关闭</option></select><button class="text-button" data-action="update-status" type="button" ${busy ? "disabled" : ""}>保存状态</button><small>保存将更新版本，使待审批提案过期</small></div><div class="agent-toolbar"><div class="agent-title"><span class="agent-symbol">${icon("spark")}</span><div><h3>Agent 处理建议</h3><p>${state.config?.mode === "demo" ? "离线规则演示" : e(state.config?.model || "Agent")} ${analysis ? `· ${analysis.model_calls || 0} 次模型调用` : "· 从证据开始"}</p></div></div><button class="button accent" data-action="analyze" type="button" ${busy ? "disabled" : ""}>${busy ? '<span class="spinner"></span>正在处理' : `${icon("spark")}${analysis ? "重新分析" : "生成处理建议"}`}</button></div><div class="tabs" role="tablist" aria-label="处理详情"><button type="button" class="tab ${state.tab === "analysis" ? "active" : ""}" role="tab" aria-selected="${state.tab === "analysis"}" data-tab="analysis">处理建议</button><button type="button" class="tab ${state.tab === "audit" ? "active" : ""}" role="tab" aria-selected="${state.tab === "audit"}" data-tab="audit">操作审计 <span>${state.audit.length}</span></button></div><div role="tabpanel">${state.tab === "analysis" ? analysisMarkup() : auditMarkup()}</div></div>`;
}

async function selectTicket(id, keepTab = false) {
  const version = ++state.version;
  const changed = state.selected !== id;
  state.selected = id;
  if (!keepTab) { state.tab = "analysis"; state.search = null; }
  if (changed) { state.ticket = null; $("#detail-pane").innerHTML = '<div class="empty-detail"><span class="empty-symbol">' + icon("inbox") + '</span><p>正在读取工单上下文…</p></div>'; }
  renderList();
  try {
    const [ticket, analysis, audit] = await Promise.all([api(`/api/tickets/${encodeURIComponent(id)}`), optionalAnalysis(id), api(`/api/tickets/${encodeURIComponent(id)}/audit`)]);
    state.analyses.set(id, analysis);
    state.tickets = state.tickets.map(item => item.id === id ? ticket : item);
    if (version !== state.version) return;
    state.ticket = ticket; state.analysis = analysis; state.audit = audit;
    renderList(); renderDetail();
  } catch (error) { if (version === state.version) showError(error); }
}
async function refresh() {
  clearError(); $("#refresh-button").disabled = true;
  try {
    const [config, tickets] = await Promise.all([api("/api/config"), api("/api/tickets")]);
    state.config = config; state.tickets = tickets; renderConfig(); renderList();
    // Bound concurrency for a full inbox refresh, including user-created tickets.
    for (let index = 0; index < tickets.length; index += 8) {
      await Promise.all(tickets.slice(index, index + 8).map(async ticket => { state.analyses.set(ticket.id, await optionalAnalysis(ticket.id)); }));
    }
    renderList();
    if (tickets.length) await selectTicket(tickets.some(ticket => ticket.id === state.selected) ? state.selected : tickets[0].id, true);
  } catch (error) { showError(error); }
  finally { $("#refresh-button").disabled = false; }
}
function approvalKey(id) {
  const key = `casedesk-approval:${id}`;
  let existing;
  try { existing = sessionStorage.getItem(key); } catch { /* In-memory fallback below. */ }
  if (existing) return existing;
  if (!approvalKey.cache) approvalKey.cache = new Map();
  if (!approvalKey.cache.has(id)) approvalKey.cache.set(id, `ui-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`);
  existing = approvalKey.cache.get(id);
  try { sessionStorage.setItem(key, existing); } catch { /* Keep the same key in memory. */ }
  return existing;
}
async function performAction(action) {
  const ticket = state.ticket, proposal = state.analysis?.proposal;
  if (!ticket || state.busy.has(ticket.id)) return;
  const status = $("#ticket-status")?.value;
  if (action === "update-status" && status === ticket.status) { toast("工单状态没有变化"); return; }
  clearError(); state.busy.add(ticket.id); renderDetail();
  try {
    if (action === "analyze") {
      const analysis = await api(`/api/tickets/${encodeURIComponent(ticket.id)}/analyze`, { method: "POST" });
      state.analyses.set(ticket.id, analysis);
      toast(analysis.status === "failed" ? "分析未完成，请查看错误详情" : "处理建议已生成，请核对证据与规则结果");
    } else if (action === "approve" && proposal) {
      const result = await api(`/api/proposals/${encodeURIComponent(proposal.id)}/approve`, { method: "POST", body: JSON.stringify({ ticket_revision: proposal.ticket_revision, idempotency_key: approvalKey(proposal.id) }) });
      toast(result.replayed ? "已返回此前保存的操作结果，未重复执行" : "已批准并保存本地模拟记录，未发生真实退款或消息发送");
    } else if (action === "reject" && proposal) {
      await api(`/api/proposals/${encodeURIComponent(proposal.id)}/reject`, { method: "POST", body: JSON.stringify({ ticket_revision: proposal.ticket_revision }) });
      toast("已拒绝提案，未执行任何操作");
    } else if (action === "update-status") {
      await api(`/api/tickets/${encodeURIComponent(ticket.id)}`, { method: "PATCH", body: JSON.stringify({ expected_revision: ticket.revision, status }) });
      toast("工单状态与版本已更新，请重新分析待审批提案");
    }
  } catch (error) { showError(error); }
  finally {
    state.busy.delete(ticket.id);
    if (state.selected === ticket.id) await selectTicket(ticket.id, true);
    else { state.tickets = await api("/api/tickets").catch(() => state.tickets); renderList(); }
  }
}

function setupIntake() {
  $(".page-heading").insertAdjacentHTML("beforeend", '<button class="button primary new-ticket-button" id="new-ticket-button" type="button">＋ 新建工单</button>');
  document.body.insertAdjacentHTML("beforeend", `<dialog id="new-ticket-dialog"><button class="dialog-close" id="close-new-ticket" type="button" aria-label="关闭">×</button><div class="eyebrow">NEW SUPPORT REQUEST</div><h2>把新问题交给工作台。</h2><form id="new-ticket-form" class="intake-form"><label>工单标题<input name="subject" minlength="3" maxlength="160" placeholder="例如：希望退回重复购买的订单" required></label><label>问题分类<select name="category"><option value="billing">账单与退款</option><option value="shipping">配送问题</option><option value="account">账户安全</option><option value="other">其他问题</option></select></label><label>演示账户 / 订单<select name="context_id" id="context-select" required></select></label><div class="context-facts" id="context-facts"></div><label>客户问题<textarea name="body" rows="4" minlength="10" maxlength="4000" placeholder="详细描述需要处理的问题，至少 10 个字符" required></textarea></label><div class="alert error" id="intake-error" role="alert" hidden></div><p class="dialog-note">使用合成演示账户。订单金额、验证状态等事实由服务端提供，不能通过工单文字修改。</p><button class="button primary" type="submit" id="create-ticket-submit">创建工单</button></form></dialog>`);
  $("#new-ticket-button").addEventListener("click", async () => {
    $("#intake-error").hidden = true; $("#new-ticket-dialog").showModal(); $("#create-ticket-submit").disabled = true;
    try {
      state.contexts = await api("/api/contexts");
      $("#context-select").innerHTML = state.contexts.map(context => `<option value="${e(context.id)}">${e(context.label)}</option>`).join("");
      renderContext(); $("#create-ticket-submit").disabled = false;
    } catch (error) { $("#intake-error").hidden = false; $("#intake-error").textContent = error.message; }
  });
  $("#close-new-ticket").addEventListener("click", () => $("#new-ticket-dialog").close());
  $("#context-select").addEventListener("change", renderContext);
  $("#new-ticket-form").addEventListener("submit", async event => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    $("#create-ticket-submit").disabled = true; $("#intake-error").hidden = true;
    try {
      const body = Object.fromEntries(new FormData(form));
      const ticket = await api("/api/tickets", { method: "POST", body: JSON.stringify(body) });
      $("#new-ticket-dialog").close(); form.reset(); state.tickets.unshift(ticket); state.filter = "all"; state.query = ""; $("#ticket-search").value = "";
      document.querySelectorAll(".filter").forEach(button => { const active = button.dataset.filter === "all"; button.classList.toggle("active", active); button.setAttribute("aria-pressed", active); });
      await selectTicket(ticket.id); toast("新工单已创建，可以生成处理建议");
    } catch (error) { $("#intake-error").hidden = false; $("#intake-error").textContent = error.message; }
    finally { $("#create-ticket-submit").disabled = false; }
  });
}
function renderContext() {
  const context = state.contexts.find(item => item.id === $("#context-select").value);
  $("#context-facts").textContent = context ? `实付 ${money(context.order.amount_cents)} · 购买 ${context.order.days_since_purchase} 天 · ${context.account.verified ? "账户已验证" : "账户未验证"} · ${context.order.delivered ? "已送达" : "未送达"}` : "";
}

$("#ticket-list").addEventListener("click", event => { const button = event.target.closest("[data-ticket]"); if (button) selectTicket(button.dataset.ticket); });
$("#ticket-search").addEventListener("input", event => { state.query = event.target.value; renderList(); });
document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(item => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", active); });
  renderList();
}));
$("#detail-pane").addEventListener("click", event => {
  const tab = event.target.closest("[data-tab]"); if (tab) { state.tab = tab.dataset.tab; renderDetail(); return; }
  const action = event.target.closest("[data-action]"); if (action && !action.disabled) performAction(action.dataset.action);
});
$("#detail-pane").addEventListener("submit", async event => {
  if (event.target.id !== "evidence-form") return; event.preventDefault();
  const query = $("#evidence-query").value.trim(); if (!query) return;
  const id = state.selected; const submit = $("button", event.target); submit.disabled = true;
  try { const result = await api(`/api/search?q=${encodeURIComponent(query)}`); if (state.selected === id) { state.search = result; $("#search-results").innerHTML = `<p class="search-title">独立检索结果 · ${e(result.revision)}（不会改写当前提案）</p>${evidenceMarkup(result.chunks)}`; } }
  catch (error) { showError(error); }
  finally { submit.disabled = false; }
});
$("#refresh-button").addEventListener("click", refresh);
$("#about-button").addEventListener("click", () => $("#about-dialog").showModal());
$("#close-about").addEventListener("click", () => $("#about-dialog").close());
$("#inbox-nav").addEventListener("click", () => $(".desk").scrollIntoView({ behavior: "smooth" }));
setupIntake();
refresh();
