// BillPilot modern fintech frontend application logic.
// Strictly adheres to existing backend API contracts:
//   - GET  /api/health
//   - POST /api/extract
//   - POST /api/invoices
//   - GET  /api/invoices
//   - GET  /api/invoices/<id>
//   - GET  /api/summary
//   - POST /api/demo/<id>/make-overdue
//   - POST /api/invoices/<id>/reminder-draft
//   - GET  /api/invoices/<id>/reminders
//   - POST /api/invoices/<id>/reminder-send
//
// Security & Compliance:
//   - All dynamic strings and API responses are inserted via textContent / DOM text nodes.
//   - ZERO innerHTML with user, extracted, or API data (preventing XSS).

const $ = (id) => document.getElementById(id);

let sourceText = "";
let aiMode = "ai";
let idempotencyKey = null;
let reminderInvoiceId = null;
let currentReminderTone = null;
let currentReminderAiMode = "ai";
let busy = false;
let isDemoMode = true;
let allInvoices = [];
let currentFilter = "all";

// ---------- Safe DOM Builder & Format Helpers ----------

// Safely creates elements with textContent / textNode children, never innerHTML.
function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class" || key === "className") {
      node.className = value;
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (key === "hidden") {
      if (value) node.setAttribute("hidden", "");
      else node.removeAttribute("hidden");
    } else if (value !== null && value !== undefined && value !== false) {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of children) {
    if (child instanceof Node) {
      node.append(child);
    } else if (child !== null && child !== undefined) {
      node.append(document.createTextNode(String(child)));
    }
  }
  return node;
}

// Strict URL sanitizer: allows only http:// and https:// schemes
function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

function setMsg(id, text, kind = "") {
  const el = $(id);
  if (!el) return;
  el.textContent = text || "";
  el.className = "msg " + kind;
}

// Formats amounts cleanly with commas e.g. $1,200.00
function fmtMoney(total, currency = "USD") {
  if (total === undefined || total === null || total === "") return "$0.00";
  const n = Number(total);
  if (!Number.isFinite(n)) return String(total);
  const formatted = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const curr = String(currency || "USD").toUpperCase();
  if (curr === "USD") return `$${formatted}`;
  return `${formatted} ${curr}`;
}

// Disable a button while its async action runs
async function withBusy(button, fn) {
  if (busy) return;
  busy = true;
  if (button) button.disabled = true;
  try {
    await fn();
  } finally {
    busy = false;
    if (button) button.disabled = false;
  }
}

// ---------- Visual 3-Step Progress Indicator ----------

function setStep(stepNum) {
  const s1 = $("step-1");
  const s2 = $("step-2");
  const s3 = $("step-3");
  if (!s1 || !s2 || !s3) return;

  s1.className = "step-item";
  s2.className = "step-item";
  s3.className = "step-item";

  if (stepNum === 1) {
    s1.classList.add("is-active");
  } else if (stepNum === 2) {
    s1.classList.add("is-complete");
    s2.classList.add("is-active");
  } else if (stepNum === 3) {
    s1.classList.add("is-complete");
    s2.classList.add("is-complete");
    s3.classList.add("is-active");
  }
}

// ---------- Developer Debug Log ----------

function log(method, path, body, status, data) {
  const stamp = new Date().toLocaleTimeString();
  const entry =
    `[${stamp}] ${method} ${path} -> ${status}\n` +
    (body ? "request:  " + JSON.stringify(body) + "\n" : "") +
    "response: " + JSON.stringify(data, null, 2) + "\n\n";
  const pre = $("log");
  if (!pre) return;
  pre.textContent = entry + pre.textContent;
  if (pre.textContent.length > 25000) {
    pre.textContent = pre.textContent.slice(0, 25000);
  }
}

// Central API fetch helper
async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    log(method, path, body, "network error", { error: String(err) });
    throw new Error("Cannot reach the backend. Is `python app.py` running?");
  }
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { raw: text.slice(0, 500) };
  }
  log(method, path, body, res.status, data);
  if (!res.ok) {
    throw new Error(data.error || data.message || `Request failed (HTTP ${res.status})`);
  }
  return data;
}

// ---------- Backend Health Check ----------

async function loadHealth() {
  const pill = $("health");
  if (!pill) return;
  try {
    const data = await api("GET", "/api/health");
    isDemoMode = data.demo_mode ?? true;
    const ai = data.ai_provider || "active";
    pill.className = "health-pill is-live";
    pill.replaceChildren(
      h("span", { class: "pulse-dot" }),
      h("span", { class: "health-text" }, `Sandbox Live • AI: ${ai}`)
    );
  } catch {
    pill.className = "health-pill is-preview";
    pill.replaceChildren(
      h("span", { class: "warning-dot" }),
      h("span", { class: "health-text" }, "Preview mode (backend unreachable)")
    );
  }
}

// ---------- Step 1: Describe & Extract ----------

async function onExtract() {
  const text = $("sentence").value.trim();
  if (!text) {
    setMsg("extract-msg", "Type a sentence first or select a sample prompt.", "error");
    return;
  }
  setMsg("extract-msg", "Extracting invoice details with AI...", "info");
  try {
    const data = await api("POST", "/api/extract", { text });
    const extraction = data.extraction || data;
    const errors = data.errors || data.validation_errors || extraction.errors || [];
    sourceText = text;
    fillPreview(extraction, errors);
    setMsg("extract-msg", "Preview ready. Review and adjust fields below.", "ok");
    setStep(2);
  } catch (err) {
    setMsg("extract-msg", err.message, "error");
  }
}

// ---------- Step 2: Preview & Confirm ----------

function fillPreview(ex, errors = []) {
  aiMode = ex.ai_mode || "ai";
  idempotencyKey = (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : String(Date.now() + "-" + Math.random().toString(36).slice(2, 9));

  $("p-name").value = ex.client_name || "";
  $("p-email").value = ex.client_email || "";
  $("p-currency").value = ex.currency || "USD";
  $("p-due").value = ex.due_date || "";
  $("p-note").value = ex.note || "";

  // Dynamic AI badge label
  const aiBadge = $("ai-mode-badge");
  if (aiBadge) {
    aiBadge.textContent = aiMode === "fallback" ? "Template Parser" : "AI Draft";
  }

  // Email missing warning badge
  updateEmailWarning();

  // Populate line items
  $("items").replaceChildren();
  const items = Array.isArray(ex.items) && ex.items.length ? ex.items : [{}];
  items.forEach(addItemRow);
  updateTotal();

  // Notices area
  const notices = $("preview-notices");
  notices.replaceChildren();
  if (aiMode === "fallback") {
    notices.append(h("div", { class: "notice warn" }, "Deterministic parser used (AI mode fallback)."));
  }
  if (Array.isArray(ex.missing_fields) && ex.missing_fields.length) {
    notices.append(h("div", { class: "notice warn" }, "Missing fields: " + ex.missing_fields.join(", ")));
  }
  for (const e of errors) {
    notices.append(h("div", { class: "notice err" }, typeof e === "string" ? e : JSON.stringify(e)));
  }
  if (typeof ex.confidence === "number") {
    notices.append(h("div", { class: "notice info" }, "Extraction confidence: " + Math.round(ex.confidence * 100) + "%"));
  }

  setMsg("create-msg", "");
  $("preview").hidden = false;
  $("preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateEmailWarning() {
  const warning = $("email-warning");
  if (!warning) return;
  const email = ($("p-email").value || "").trim();
  const isValid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
  if (!email || !isValid) {
    warning.hidden = false;
    warning.textContent = !email ? "Required for PayPal" : "Invalid email format";
  } else {
    warning.hidden = true;
  }
}

function addItemRow(item = {}) {
  const row = h(
    "div",
    { class: "item-row" },
    h("input", { class: "i-name", placeholder: "Item name", value: item.name ?? "" }),
    h("input", { class: "i-qty", type: "number", min: "1", step: "1", value: item.quantity ?? 1 }),
    h("input", { class: "i-price", type: "number", min: "0", step: "0.01", value: item.unit_price ?? "" }),
    h("button", {
      type: "button",
      class: "btn-remove-item",
      title: "Remove item",
      onclick: () => {
        row.remove();
        updateTotal();
      }
    }, "×")
  );
  row.addEventListener("input", updateTotal);
  $("items").append(row);
}

function readItems() {
  return [...document.querySelectorAll("#items .item-row")].map((row) => ({
    name: row.querySelector(".i-name").value.trim(),
    quantity: parseInt(row.querySelector(".i-qty").value, 10),
    unit_price: parseFloat(row.querySelector(".i-price").value),
  }));
}

function updateTotal() {
  const items = readItems();
  const total = items.reduce((sum, it) => {
    const q = Number.isFinite(it.quantity) ? it.quantity : 0;
    const p = Number.isFinite(it.unit_price) ? it.unit_price : 0;
    return sum + q * p;
  }, 0);
  const currency = ($("p-currency").value || "USD").trim().toUpperCase();
  $("p-total").textContent = fmtMoney(total, currency);
}

function collectPayload() {
  const items = readItems();
  if (!items.length) throw new Error("Add at least one item.");
  for (const it of items) {
    if (!it.name) throw new Error("Every line item requires a name.");
    if (!Number.isInteger(it.quantity) || it.quantity < 1) throw new Error("Quantity must be a whole number (1 or more).");
    if (!(it.unit_price > 0)) throw new Error("Unit price must be greater than 0.");
  }
  const name = $("p-name").value.trim();
  const email = $("p-email").value.trim();
  if (!name) throw new Error("Client name is required.");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error("Enter a valid client email address.");

  return {
    client_name: name,
    client_email: email,
    items,
    currency: $("p-currency").value.trim().toUpperCase() || "USD",
    due_date: $("p-due").value || null,
    note: $("p-note").value.trim() || null,
    source_text: sourceText,
    ai_mode: aiMode,
    idempotency_key: idempotencyKey,
  };
}

async function onCreate() {
  let payload;
  try {
    payload = collectPayload();
  } catch (err) {
    setMsg("create-msg", err.message, "error");
    return;
  }
  setStep(3);
  setMsg("create-msg", "Creating invoice and dispatching via PayPal...", "info");
  try {
    const data = await api("POST", "/api/invoices", payload);
    const inv = data.invoice || data;
    const invNum = inv.invoice_number || ("#" + inv.id);
    setMsg("create-msg", `Successfully created and sent invoice ${invNum}!`, "ok");
    $("preview").hidden = true;
    $("sentence").value = "";
    setStep(1);
    await loadInvoices();
  } catch (err) {
    setStep(2);
    setMsg("create-msg", err.message, "error");
  }
}

// ---------- Right Column: Invoices & Filter Pills ----------

function statusBadge(status) {
  const s = String(status || "").trim().toLowerCase();
  if (s === "unpaid") {
    return h("span", { class: "badge badge-unpaid" },
      h("span", { class: "badge-shape" }),
      "Unpaid"
    );
  }
  if (s === "overdue") {
    return h("span", { class: "badge badge-overdue" },
      h("span", { class: "badge-shape" }),
      "Overdue"
    );
  }
  if (s === "paid") {
    return h("span", { class: "badge badge-paid" },
      h("span", { class: "badge-shape" }),
      "Paid"
    );
  }
  return h("span", { class: "badge badge-other" }, status || "Unknown");
}

async function loadInvoices() {
  setMsg("list-msg", "Refreshing invoices...", "info");
  try {
    const data = await api("GET", "/api/invoices");
    allInvoices = Array.isArray(data) ? data : data.invoices || [];
    updateFilterCounts();
    renderFilteredInvoices();
    setMsg("list-msg", `${allInvoices.length} total invoice(s).`, "ok");
  } catch (err) {
    setMsg("list-msg", err.message, "error");
  }
  loadSummary();
}

function updateFilterCounts() {
  let unpaidCount = 0;
  let overdueCount = 0;
  let paidCount = 0;

  for (const inv of allInvoices) {
    const s = String(inv.status || "").toLowerCase();
    if (s === "unpaid") unpaidCount++;
    else if (s === "overdue") overdueCount++;
    else if (s === "paid") paidCount++;
  }

  $("count-all").textContent = String(allInvoices.length);
  $("count-unpaid").textContent = String(unpaidCount);
  $("count-overdue").textContent = String(overdueCount);
  $("count-paid").textContent = String(paidCount);
}

function renderFilteredInvoices() {
  const body = $("invoice-rows");
  if (!body) return;
  body.replaceChildren();

  const filtered = allInvoices.filter((inv) => {
    if (currentFilter === "all") return true;
    const s = String(inv.status || "").toLowerCase();
    return s === currentFilter;
  });

  if (filtered.length === 0) {
    body.append(
      h("tr", {},
        h("td", { colspan: "6", class: "table-empty-row" }, "No invoices found for this filter.")
      )
    );
    return;
  }

  for (const inv of filtered) {
    const status = inv.status || inv.last_status || "";
    const lower = String(status).toLowerCase();
    const payUrl = safeUrl(inv.recipient_url || inv.payer_url);

    const actions = h("div", { class: "row-actions" });

    // Pay link opens payer_url in new tab
    if (payUrl) {
      actions.append(
        h("a", {
          class: "btn-pay-link",
          href: payUrl,
          target: "_blank",
          rel: "noopener noreferrer",
          title: "Open PayPal payment link"
        }, "Pay link ↗")
      );
    }

    // Action buttons according to specification:
    // On Unpaid / Overdue rows: "Pay link", gold "Send reminder" button, and "Make overdue (demo)" button
    // On Paid rows: "Pay link" only (no reminder or make-overdue buttons)
    if (lower === "unpaid" || lower === "overdue") {
      actions.append(
        h("button", {
          type: "button",
          class: "btn-row-gold",
          onclick: () => openReminder(inv.id)
        }, "Send reminder")
      );

      if (isDemoMode) {
        actions.append(
          h("button", {
            type: "button",
            class: "btn-row-demo",
            title: "Simulate overdue date for testing reminder workflows",
            onclick: () => simulateOverdue(inv.id)
          }, "Make overdue (demo)")
        );
      }
    }

    body.append(
      h("tr", {},
        // Client name + email
        h("td", {},
          h("div", { class: "client-name-cell" }, inv.client_name || "—"),
          inv.client_email ? h("div", { class: "client-email-sub" }, inv.client_email) : null
        ),
        // Invoice number
        h("td", {},
          h("span", { class: "invoice-num-cell" }, inv.invoice_number || ("#" + inv.id))
        ),
        // Due date
        h("td", {}, inv.due_date || "Upon receipt"),
        // Amount formatted ($X,XXX.XX)
        h("td", {},
          h("span", { class: "invoice-amount-cell" }, fmtMoney(inv.total, inv.currency))
        ),
        // Status badge
        h("td", {}, statusBadge(status)),
        // Action buttons
        h("td", {}, actions)
      )
    );
  }
}

// ---------- Top Summary Card: 4-Column KPI Strip ----------

async function loadSummary() {
  const billedEl = $("kpi-billed");
  const collectedEl = $("kpi-collected");
  const outstandingEl = $("kpi-outstanding");
  const overdueEl = $("kpi-overdue");
  const fallbackBox = $("summary");

  try {
    const data = await api("GET", "/api/summary");
    if (billedEl) billedEl.textContent = fmtMoney(data.total_billed, "USD");
    if (collectedEl) collectedEl.textContent = fmtMoney(data.total_collected, "USD");
    if (outstandingEl) outstandingEl.textContent = fmtMoney(data.total_outstanding, "USD");
    if (overdueEl) {
      const oc = data.overdue_count ?? 0;
      overdueEl.textContent = `${oc} invoice${oc === 1 ? "" : "s"}`;
    }

    // Keep hidden fallback container populated for backward-compatibility
    if (fallbackBox) {
      fallbackBox.replaceChildren();
      for (const [key, value] of Object.entries(data)) {
        fallbackBox.append(h("div", { class: "card" }, key.replace(/_/g, " "), h("strong", {}, value)));
      }
    }
  } catch {
    // Graceful fallback if summary endpoint fails
  }
}

async function simulateOverdue(id) {
  setMsg("list-msg", `Setting invoice #${id} overdue (demo simulation)...`, "info");
  try {
    await api("POST", `/api/demo/${id}/make-overdue`);
    await loadInvoices();
    setMsg("list-msg", `Invoice #${id} is now simulated as overdue.`, "ok");
  } catch (err) {
    setMsg("list-msg", err.message, "error");
  }
}

// ---------- Step 4: Reminder Drawer / Modal ----------

function openReminderModal() {
  const modal = $("reminder-modal");
  const card = $("reminder");
  if (modal) modal.hidden = false;
  if (card) card.hidden = false;
}

function closeReminderModal() {
  const modal = $("reminder-modal");
  const card = $("reminder");
  if (modal) modal.hidden = true;
  if (card) card.hidden = true;
  reminderInvoiceId = null;
}

async function openReminder(id) {
  reminderInvoiceId = id;
  $("r-id").textContent = "#" + id;
  $("r-subject").value = "";
  $("r-note").value = "";
  $("r-meta").textContent = "Loading AI reminder draft...";
  setMsg("reminder-msg", "");
  openReminderModal();
  await onDraftReminder();
  await loadReminderHistory(id);
}

async function loadReminderHistory(id) {
  try {
    const data = await api("GET", `/api/invoices/${id}/reminders`);
    const list = data.reminders || [];
    if (list.length > 0) {
      const existing = $("r-meta").textContent;
      const historyStr = `${list.length} reminder(s) previously sent`;
      $("r-meta").textContent = existing ? `${existing} • ${historyStr}` : historyStr;
    }
  } catch {
    // optional metadata
  }
}

async function onDraftReminder() {
  const tone = $("r-tone").value;
  setMsg("reminder-msg", "Drafting contextual reminder...", "info");
  try {
    const data = await api("POST", `/api/invoices/${reminderInvoiceId}/reminder-draft`, tone ? { tone } : {});
    currentReminderTone = data.tone || tone || null;
    currentReminderAiMode = data.ai_mode || "ai";
    $("r-subject").value = data.subject || "";
    $("r-note").value = data.note || "";
    const bits = [];
    if (data.tone) bits.push("Tone: " + data.tone);
    if (data.days_overdue !== undefined) bits.push("Days overdue: " + data.days_overdue);
    if (data.ai_mode) bits.push("Mode: " + data.ai_mode + (data.ai_mode === "fallback" ? " (template)" : ""));
    $("r-meta").textContent = bits.join(" • ");
    setMsg("reminder-msg", "Draft generated. Review and approve before sending.", "ok");
  } catch (err) {
    setMsg("reminder-msg", err.message, "error");
  }
}

async function onSendReminder() {
  const subject = $("r-subject").value.trim();
  const note = $("r-note").value.trim();
  if (!subject || !note) {
    setMsg("reminder-msg", "Subject and message are required.", "error");
    return;
  }
  if (!confirm("Send this payment reminder to the client via PayPal?")) return;
  setMsg("reminder-msg", "Sending via PayPal...", "info");
  try {
    await api("POST", `/api/invoices/${reminderInvoiceId}/reminder-send`, {
      subject,
      note,
      tone: currentReminderTone,
      ai_mode: currentReminderAiMode,
    });
    setMsg("reminder-msg", "Payment reminder sent successfully via PayPal!", "ok");
    await loadReminderHistory(reminderInvoiceId);
    await loadInvoices();
  } catch (err) {
    setMsg("reminder-msg", err.message, "error");
  }
}

// ---------- Event Listeners & Wiring ----------

$("btn-extract").addEventListener("click", (e) => withBusy(e.target, onExtract));
$("btn-create").addEventListener("click", (e) => withBusy(e.target, onCreate));
$("btn-refresh").addEventListener("click", (e) => withBusy(e.target, loadInvoices));
$("btn-draft").addEventListener("click", (e) => withBusy(e.target, onDraftReminder));
$("btn-send-reminder").addEventListener("click", (e) => withBusy(e.target, onSendReminder));

$("btn-add-item").addEventListener("click", () => {
  addItemRow();
  updateTotal();
});

$("btn-cancel").addEventListener("click", () => {
  $("preview").hidden = true;
  setStep(1);
  setMsg("create-msg", "");
});

$("btn-close-reminder").addEventListener("click", closeReminderModal);
const btnCancelRem = $("btn-cancel-reminder");
if (btnCancelRem) btnCancelRem.addEventListener("click", closeReminderModal);

// Close modal when clicking on backdrop
const remModal = $("reminder-modal");
if (remModal) {
  remModal.addEventListener("click", (e) => {
    if (e.target === remModal) closeReminderModal();
  });
}

// Close modal on Escape key
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && remModal && !remModal.hidden) {
    closeReminderModal();
  }
});

$("btn-clear-log").addEventListener("click", (e) => {
  e.stopPropagation(); // avoid toggling details element
  $("log").textContent = "";
});

$("p-currency").addEventListener("input", updateTotal);
$("p-email").addEventListener("input", updateEmailWarning);

// Sample prompt buttons
document.querySelectorAll("[data-example]").forEach((btn) => {
  btn.addEventListener("click", () => {
    $("sentence").value = btn.dataset.example;
    $("sentence").focus();
  });
});

// Status filter pills
document.querySelectorAll(".filter-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".filter-pill").forEach((p) => {
      p.classList.remove("is-active");
      p.setAttribute("aria-selected", "false");
    });
    pill.classList.add("is-active");
    pill.setAttribute("aria-selected", "true");
    currentFilter = pill.dataset.filter || "all";
    renderFilteredInvoices();
  });
});

// Initial boot
loadHealth();
loadInvoices();
