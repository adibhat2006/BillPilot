// BillPilot basic test UI.
// Talks to the Flask API from docs/01_IMPLEMENTATION_PLAN.md section 5.
// All dynamic text is inserted with textContent / createTextNode, never innerHTML.

const $ = (id) => document.getElementById(id);

let sourceText = "";
let aiMode = "ai";
let idempotencyKey = null;
let reminderInvoiceId = null;
let busy = false;

// ---------- small helpers ----------

// Build a DOM element safely. Children are added as text nodes.
function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    node.append(child instanceof Node ? child : document.createTextNode(String(child ?? "")));
  }
  return node;
}

// Only allow normal web links (blocks javascript: and other schemes).
function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

function setMsg(id, text, kind = "") {
  const el = $(id);
  el.textContent = text || "";
  el.className = "msg " + kind;
}

function fmtMoney(total, currency) {
  if (total === undefined || total === null || total === "") return "";
  const n = Number(total);
  return (Number.isFinite(n) ? n.toFixed(2) : String(total)) + (currency ? " " + currency : "");
}

// Disable a button while its action runs, so double clicks do nothing.
async function withBusy(button, fn) {
  if (busy) return;
  busy = true;
  button.disabled = true;
  try {
    await fn();
  } finally {
    busy = false;
    button.disabled = false;
  }
}

// ---------- debug log ----------

function log(method, path, body, status, data) {
  const stamp = new Date().toLocaleTimeString();
  const entry =
    `[${stamp}] ${method} ${path} -> ${status}\n` +
    (body ? "request:  " + JSON.stringify(body) + "\n" : "") +
    "response: " + JSON.stringify(data, null, 2) + "\n\n";
  const pre = $("log");
  pre.textContent = entry + pre.textContent;
  if (pre.textContent.length > 20000) pre.textContent = pre.textContent.slice(0, 20000);
}

// One place for all backend calls.
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

// ---------- health ----------

async function loadHealth() {
  try {
    const data = await api("GET", "/api/health");
    $("health").textContent = "Backend: " + JSON.stringify(data);
  } catch (err) {
    $("health").textContent = "Backend not reachable: " + err.message;
  }
}

// ---------- step 1: extract ----------

async function onExtract() {
  const text = $("sentence").value.trim();
  if (!text) {
    setMsg("extract-msg", "Type a sentence first.", "error");
    return;
  }
  setMsg("extract-msg", "Extracting...");
  try {
    const data = await api("POST", "/api/extract", { text });
    const extraction = data.extraction || data;
    const errors = data.errors || data.validation_errors || extraction.errors || [];
    sourceText = text;
    fillPreview(extraction, errors);
    setMsg("extract-msg", "Preview ready below.", "ok");
  } catch (err) {
    setMsg("extract-msg", err.message, "error");
  }
}

// ---------- step 2: preview ----------

function fillPreview(ex, errors) {
  aiMode = ex.ai_mode || "ai";
  idempotencyKey = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());

  $("p-name").value = ex.client_name || "";
  $("p-email").value = ex.client_email || "";
  $("p-currency").value = ex.currency || "USD";
  $("p-due").value = ex.due_date || "";
  $("p-note").value = ex.note || "";

  $("items").replaceChildren();
  const items = Array.isArray(ex.items) && ex.items.length ? ex.items : [{}];
  items.forEach(addItemRow);
  updateTotal();

  const notices = $("preview-notices");
  notices.replaceChildren();
  if (aiMode === "fallback") {
    notices.append(h("div", { class: "notice warn" }, "Fallback parser used (AI was not used for this result)."));
  }
  if (Array.isArray(ex.missing_fields) && ex.missing_fields.length) {
    notices.append(h("div", { class: "notice warn" }, "Missing: " + ex.missing_fields.join(", ")));
  }
  for (const e of errors) {
    notices.append(h("div", { class: "notice err" }, typeof e === "string" ? e : JSON.stringify(e)));
  }
  if (typeof ex.confidence === "number") {
    notices.append(h("div", { class: "muted" }, "AI confidence: " + ex.confidence));
  }

  setMsg("create-msg", "");
  $("preview").hidden = false;
}

function addItemRow(item = {}) {
  const row = h(
    "div",
    { class: "item-row" },
    h("input", { class: "i-name", placeholder: "Item", value: item.name ?? "" }),
    h("input", { class: "i-qty", type: "number", min: "1", step: "1", value: item.quantity ?? 1 }),
    h("input", { class: "i-price", type: "number", min: "0", step: "0.01", value: item.unit_price ?? "" }),
    h("button", { type: "button", title: "Remove", onclick: () => { row.remove(); updateTotal(); } }, "x")
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
  const total = readItems().reduce((sum, it) => {
    const q = Number.isFinite(it.quantity) ? it.quantity : 0;
    const p = Number.isFinite(it.unit_price) ? it.unit_price : 0;
    return sum + q * p;
  }, 0);
  $("p-total").textContent = total.toFixed(2) + " " + ($("p-currency").value || "");
}

// Light checks only. The backend does the real validation.
function collectPayload() {
  const items = readItems();
  if (!items.length) throw new Error("Add at least one item.");
  for (const it of items) {
    if (!it.name) throw new Error("Every item needs a name.");
    if (!Number.isInteger(it.quantity) || it.quantity < 1) throw new Error("Quantity must be a whole number, 1 or more.");
    if (!(it.unit_price > 0)) throw new Error("Unit price must be greater than 0.");
  }
  const name = $("p-name").value.trim();
  const email = $("p-email").value.trim();
  if (!name) throw new Error("Client name is required.");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error("Enter a valid client email.");

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
  setMsg("create-msg", "Creating and sending the invoice via PayPal...");
  try {
    const data = await api("POST", "/api/invoices", payload);
    const inv = data.invoice || data;
    setMsg("create-msg", "Created invoice " + (inv.invoice_number || inv.id || "") + ". See the table below.", "ok");
    $("preview").hidden = true;
    await loadInvoices();
  } catch (err) {
    setMsg("create-msg", err.message, "error");
  }
}

// ---------- step 3: dashboard ----------

function statusClass(status) {
  const s = String(status || "").toLowerCase();
  if (["paid", "unpaid", "overdue", "draft", "cancelled"].includes(s)) return "s-" + s;
  return "s-other";
}

async function loadInvoices() {
  setMsg("list-msg", "Loading...");
  try {
    const data = await api("GET", "/api/invoices");
    const list = Array.isArray(data) ? data : data.invoices || [];
    renderInvoices(list);
    setMsg("list-msg", list.length + " invoice(s).", "ok");
  } catch (err) {
    setMsg("list-msg", err.message, "error");
  }
  loadSummary();
}

function renderInvoices(list) {
  const body = $("invoice-rows");
  body.replaceChildren();
  for (const inv of list) {
    const status = inv.status || inv.last_status || "";
    const payUrl = safeUrl(inv.recipient_url || inv.payer_url);
    const lower = String(status).toLowerCase();

    const actions = h("div", { class: "actions" });
    if (payUrl) actions.append(h("a", { class: "btnlink", href: payUrl, target: "_blank", rel: "noopener noreferrer" }, "Pay link"));
    if (lower !== "paid" && lower !== "cancelled") {
      actions.append(h("button", { type: "button", class: "secondary", onclick: () => simulateOverdue(inv.id) }, "Simulate overdue"));
      actions.append(h("button", { type: "button", onclick: () => openReminder(inv.id) }, "Reminder"));
    }

    body.append(
      h("tr", {},
        h("td", {}, inv.id),
        h("td", {}, inv.invoice_number),
        h("td", {}, inv.client_name),
        h("td", {}, fmtMoney(inv.total, inv.currency)),
        h("td", {}, inv.due_date),
        h("td", {}, h("span", { class: "badge " + statusClass(status) }, status || "unknown")),
        h("td", {}, actions)
      )
    );
  }
}

async function loadSummary() {
  const box = $("summary");
  try {
    const data = await api("GET", "/api/summary");
    box.replaceChildren();
    for (const [key, value] of Object.entries(data)) {
      box.append(h("div", { class: "card" }, key.replace(/_/g, " "), h("strong", {}, value)));
    }
  } catch {
    box.replaceChildren(); // summary is optional while testing
  }
}

async function simulateOverdue(id) {
  setMsg("list-msg", "Marking invoice " + id + " overdue (demo only)...");
  try {
    await api("POST", `/api/demo/${id}/make-overdue`);
    await loadInvoices();
  } catch (err) {
    setMsg("list-msg", err.message, "error");
  }
}

// ---------- step 4: reminders ----------

function openReminder(id) {
  reminderInvoiceId = id;
  $("r-id").textContent = "#" + id;
  $("r-subject").value = "";
  $("r-note").value = "";
  $("r-meta").textContent = "";
  setMsg("reminder-msg", "");
  $("reminder").hidden = false;
  $("reminder").scrollIntoView({ behavior: "smooth" });
}

async function onDraftReminder() {
  const tone = $("r-tone").value;
  setMsg("reminder-msg", "Drafting...");
  try {
    const data = await api("POST", `/api/invoices/${reminderInvoiceId}/reminder-draft`, tone ? { tone } : {});
    $("r-subject").value = data.subject || "";
    $("r-note").value = data.note || "";
    const bits = [];
    if (data.tone) bits.push("tone: " + data.tone);
    if (data.days_overdue !== undefined) bits.push("days overdue: " + data.days_overdue);
    if (data.ai_mode) bits.push("mode: " + data.ai_mode + (data.ai_mode === "fallback" ? " (template used)" : ""));
    $("r-meta").textContent = bits.join(" | ");
    setMsg("reminder-msg", "Draft ready. Edit it if you like, then send.", "ok");
  } catch (err) {
    setMsg("reminder-msg", err.message, "error");
  }
}

async function onSendReminder() {
  const subject = $("r-subject").value.trim();
  const note = $("r-note").value.trim();
  if (!subject || !note) {
    setMsg("reminder-msg", "Draft a reminder first (subject and message are required).", "error");
    return;
  }
  if (!confirm("Send this reminder to the client through PayPal?")) return;
  setMsg("reminder-msg", "Sending via PayPal...");
  try {
    await api("POST", `/api/invoices/${reminderInvoiceId}/reminder-send`, { subject, note });
    setMsg("reminder-msg", "Reminder sent.", "ok");
  } catch (err) {
    setMsg("reminder-msg", err.message, "error");
  }
}

// ---------- wiring ----------

$("btn-extract").addEventListener("click", (e) => withBusy(e.target, onExtract));
$("btn-create").addEventListener("click", (e) => withBusy(e.target, onCreate));
$("btn-refresh").addEventListener("click", (e) => withBusy(e.target, loadInvoices));
$("btn-draft").addEventListener("click", (e) => withBusy(e.target, onDraftReminder));
$("btn-send-reminder").addEventListener("click", (e) => withBusy(e.target, onSendReminder));
$("btn-add-item").addEventListener("click", () => { addItemRow(); updateTotal(); });
$("btn-cancel").addEventListener("click", () => { $("preview").hidden = true; });
$("btn-close-reminder").addEventListener("click", () => { $("reminder").hidden = true; });
$("btn-clear-log").addEventListener("click", () => { $("log").textContent = ""; });
$("p-currency").addEventListener("input", updateTotal);

document.querySelectorAll("[data-example]").forEach((btn) => {
  btn.addEventListener("click", () => { $("sentence").value = btn.dataset.example; });
});

loadHealth();
loadInvoices();
