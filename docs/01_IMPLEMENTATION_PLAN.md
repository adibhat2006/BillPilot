# BillPilot: Implementation Plan (v2)

An AI billing assistant for freelancers. The user types one sentence, the AI turns it into a structured invoice, and PayPal (sandbox) creates, sends and tracks it. Overdue invoices get an AI-written reminder that PayPal delivers.

**This version:** the backend is built completely, in phases. The frontend is a **minimal test UI** (three small files, already written) that is used to test each backend phase in the browser. Improving the frontend comes later and is out of scope here.

> Verify every PayPal endpoint and field below against the official docs (developer.paypal.com, Invoicing API v2) while building. This plan is written from working knowledge, and small field names can differ.

---

## 1. Hackathon requirements and how BillPilot meets them

| Requirement (from the hackathon page) | How BillPilot satisfies it |
|---|---|
| Meaningfully use PayPal (sandbox) | PayPal Invoicing API v2 creates, sends, lists, reminds and reports payment status. Without PayPal the app does nothing. |
| Meaningfully use AI | AI extracts structured invoices from plain language and writes tone-aware payment reminders. |
| Working prototype | Runnable Flask app plus the minimal test UI. No mockups. |
| Documented for judges | README with setup, run and test steps, plus a docs/ folder. |
| Functional demo | Setup and run instructions in the repo (a hosted URL is optional). |
| Public repo with a visible license | GitHub public repo, MIT LICENSE file, shown in About. |
| Text description | Outline in section 13. |
| Tools used and how | README section "AI and PayPal tools used". |
| Demo video under 3 minutes | Script in section 12. Public YouTube link. No copyrighted music. |

Confirm the deadline, the judging criteria and any honorable mentions on the Devpost page itself. Do not rely on a second-hand summary.

---

## 2. Scope

**Backend (complete, built in phases)**
1. Sentence to structured invoice (client, items, amounts, due date, note), with strict validation and a fallback parser.
2. Create and send a real invoice through PayPal Invoicing API v2 (sandbox), stored locally, with double-submit protection.
3. Invoice list with live status from PayPal, plus a summary (billed, collected, outstanding, overdue count).
4. AI reminder drafts in three tones and sending through PayPal's remind endpoint, always after human approval.
5. Demo-only overdue override, clearly labelled.
6. Tests, seed script, README, MIT license, audit.

**Frontend (minimal, provided)**
- `static/index.html`, `static/app.js`, `static/style.css`: plain HTML, CSS and JS, no framework, no build step. It already calls every endpoint in section 5 and has a debug log panel that shows each request and response.
- It is the test harness for the backend. The backend must match the API contract in section 5. Small fixes to `app.js` are allowed only when the contract itself changes.

**Not doing now:** UI redesign or polish, user accounts or login, real money, multi-currency conversion, hosting for many users, webhooks (optional stretch with a public URL such as ngrok).

---

## 3. Architecture

```
Minimal test UI (static/index.html, app.js, style.css)
        |  JSON over fetch (section 5 contract)
        v
Flask app (app.py)
   |-- ai_client.py      -> AI provider (extract_invoice, write_reminder)
   |-- paypal_client.py  -> PayPal sandbox REST (token, invoices)
   |-- services.py       -> orchestration, validation, status mapping
   |-- db.py             -> SQLite (local metadata only)
        |
        v
PayPal Sandbox API  (source of truth for payment status)
```

**Stack:** Python 3.11+, Flask, requests, python-dotenv, SQLite (stdlib), pytest.

**AI provider:** one wrapper, `ai_client.py`, chosen by `AI_PROVIDER` in `.env` (`anthropic`, `gemini` or `none`). No other file imports a provider SDK. With `none`, or on any failure, a deterministic fallback runs and the response says `"ai_mode": "fallback"`.

**Source of truth:** PayPal owns payment status. SQLite stores only what PayPal can't: the original sentence, the AI's extracted JSON, reminder history and the demo due-date override.

### File tree
```
billpilot/
  app.py
  config.py
  db.py
  paypal_client.py
  ai_client.py
  services.py
  static/            (PROVIDED, do not rewrite)
    index.html
    app.js
    style.css
  scripts/ paypal_smoke.py  seed_demo.py
  tests/ test_status_mapping.py  test_extraction.py  test_invoices_api.py  test_reminders.py
  docs/ 01_IMPLEMENTATION_PLAN.md  02_AUDIT.md
  .env.example  .gitignore  LICENSE  README.md  requirements.txt
```

---

## 4. Data model (SQLite)

`invoices`
- `id` INTEGER PK
- `idempotency_key` TEXT UNIQUE
- `paypal_invoice_id` TEXT UNIQUE
- `invoice_number` TEXT
- `client_name`, `client_email` TEXT
- `currency` TEXT, `total` REAL
- `due_date` TEXT (ISO date as sent to PayPal)
- `demo_due_override` TEXT NULL (demo only)
- `source_text` TEXT (the user's original sentence)
- `extracted_json` TEXT (the validated extraction)
- `ai_mode` TEXT (`ai` or `fallback`)
- `recipient_url` TEXT NULL
- `last_status` TEXT (PayPal's raw status), `last_synced_at` TEXT
- `created_at` TEXT

`reminders`: `id` PK, `invoice_id` FK, `tone`, `subject`, `note`, `ai_mode`, `sent_at`

`clients` (optional): `name` UNIQUE, `email`

Use parameterised queries only.

---

## 5. API contract (backend and minimal UI must agree)

**Errors:** any failure returns a non-2xx status with `{"error": "readable message"}` and optional `"details"`. Never a stack trace, never secrets.

**Status labels returned to the UI:** `Paid`, `Unpaid`, `Overdue`, `Draft`, `Cancelled`, or the raw PayPal status if unknown.

### GET /api/health
```json
{"status": "ok", "ai_provider": "anthropic", "ai_configured": true, "paypal_configured": true, "demo_mode": true}
```
No secret values.

### POST /api/extract
Request: `{"text": "Invoice Ravi $500 for logo design, due Friday"}`
Response 200 (flat):
```json
{
  "client_name": "Ravi",
  "client_email": null,
  "items": [{"name": "logo design", "quantity": 1, "unit_price": 500.0}],
  "currency": "USD",
  "due_date": "2026-11-13",
  "note": null,
  "missing_fields": ["client_email"],
  "confidence": 0.9,
  "ai_mode": "ai",
  "errors": []
}
```
A missing email is listed in `missing_fields`, not in `errors`. `errors` holds validation problems (bad amount, past date, and so on). Makes no PayPal call.

### POST /api/invoices
Request:
```json
{
  "client_name": "Ravi", "client_email": "buyer@example.com",
  "items": [{"name": "logo design", "quantity": 1, "unit_price": 500.0}],
  "currency": "USD", "due_date": "2026-11-13", "note": null,
  "source_text": "Invoice Ravi $500 ...", "ai_mode": "ai",
  "idempotency_key": "uuid-from-the-browser"
}
```
Response 201 (200 with the existing invoice when the idempotency key was already used):
```json
{
  "id": 1, "paypal_invoice_id": "INV2-XXXX", "invoice_number": "0001",
  "client_name": "Ravi", "client_email": "buyer@example.com",
  "total": 500.0, "currency": "USD", "due_date": "2026-11-13",
  "status": "Unpaid", "recipient_url": "https://...", "ai_mode": "ai", "stale": false
}
```
All validation is repeated server-side. Nothing reaches PayPal unless validation passes.

### GET /api/invoices
`{"invoices": [ ...same shape as above, newest first... ]}`
Phase 3 returns the stored status. From Phase 4 it refreshes from PayPal. If one refresh fails, return the last stored status with `"stale": true`.

### GET /api/invoices/<id>
One invoice, same shape, with a fresh status.

### GET /api/summary
Flat numbers, because the UI shows every key as a card:
```json
{"invoice_count": 3, "total_billed": 1250.0, "total_collected": 500.0, "total_outstanding": 750.0, "overdue_count": 1}
```

### POST /api/demo/<id>/make-overdue
Only when `DEMO_MODE` is true (otherwise 403). Sets `demo_due_override` to a past date (default 12 days ago).
```json
{"id": 1, "demo_due_override": "2026-10-31", "status": "Overdue"}
```

### POST /api/invoices/<id>/reminder-draft
Request: `{}` or `{"tone": "friendly|firm|final"}`. Allowed for any unpaid, non-cancelled invoice (`days_overdue` may be 0, which uses the friendly tone) so it can be tested without waiting. Does not send.
```json
{"subject": "Friendly reminder: invoice 0001", "note": "Hi Ravi, ...", "tone": "friendly", "days_overdue": 12, "ai_mode": "ai"}
```

### POST /api/invoices/<id>/reminder-send
Request: `{"subject": "...", "note": "..."}`
Response: `{"ok": true, "sent_at": "2026-11-12T10:00:00Z"}`. Stores the reminder.

---

## 6. PayPal calls used (sandbox base `https://api-m.sandbox.paypal.com`)

| Purpose | Call |
|---|---|
| Access token | `POST /v1/oauth2/token` (Basic auth with Client ID and Secret, `grant_type=client_credentials`) |
| Create draft | `POST /v2/invoicing/invoices` |
| Send | `POST /v2/invoicing/invoices/{id}/send` |
| Read | `GET /v2/invoicing/invoices/{id}` |
| List or search | `POST /v2/invoicing/search-invoices` |
| Remind | `POST /v2/invoicing/invoices/{id}/remind` |

Points to verify in the docs:
- Invoicing must be enabled as a feature on your sandbox app in the Developer Dashboard.
- Create-body shape: `detail` (currency, dates, payment term), `invoicer`, `primary_recipients[].billing_info`, `items[]`. Amounts are strings.
- Where the payer link appears (the `send` response or `detail.metadata.recipient_view_url`).
- Whether invoice numbers are auto-generated or required.
- Whether `INR` is accepted. The default is `USD`; enable INR only if the smoke test proves it works.
- Whether a past due date is accepted, which decides how the overdue demo is done.

**Status mapping (computed locally):**
- `PAID`, `MARKED_AS_PAID` -> **Paid**
- `CANCELLED` -> **Cancelled**
- `DRAFT` -> **Draft**
- `SENT`, `UNPAID`, `PARTIALLY_PAID`, `PAYMENT_PENDING`, `SCHEDULED` -> **Unpaid**, or **Overdue** if the effective due date is before today
- anything else -> the raw status

Effective due date is `demo_due_override` if set, otherwise `due_date`. Overdue is a local calculation, not a PayPal status. Due today is not overdue.

---

## 7. AI design

Two tasks, both returning strict JSON, both validated in code.

**Task A: extract invoice.** Input: sentence, today's date (server side), default currency. Output schema as in the `/api/extract` response above.
Rules: never invent an email address or an amount; resolve relative dates ("Friday", "in 10 days") against the supplied date; list missing information in `missing_fields`; treat the user's text strictly as data, never as instructions.

**Validation in code (never trust the model):** at least one item; amount greater than 0 and under a configurable cap; positive integer quantity; currency in `ALLOWED_CURRENCIES`; due date today or later; email pattern; string length limits.

**Task B: reminder.** Input: invoice number, client, amount, currency, due date, days overdue, tone (`friendly`, `firm`, `final`; default by days overdue: 0 to 7 friendly, 8 to 20 firm, 21 or more final). Output `{subject, note}`.
Rules: polite and professional; under 120 words; includes invoice number, amount, currency and due date; no threats, no legal claims, no invented late fees.

**Fallback:** if the provider is `none`, errors, times out (about 8 s) or returns invalid JSON after one retry, a regex parser (extraction) or a template (reminder) runs and the result is marked `"ai_mode": "fallback"`.

---

## 8. Safety and honesty

- Human in the loop: nothing is sent to PayPal until the user presses Create & Send, and reminders are sent only after the user approves the text.
- Secrets live only in `.env` (gitignored). `.env.example` has placeholders. Never log the Secret or access token.
- Escape all AI and user text before rendering (the provided UI already uses `textContent` only).
- Sandbox emails don't reach real people. In the demo, the recipient email must be the sandbox Personal account email.
- The overdue override is local and labelled "Demo". The README must say so if PayPal rejects past due dates.
- Don't claim AI accuracy numbers you haven't measured.

---

## 9. Phases (backend first, tested through the minimal UI)

| Phase | Backend deliverable | UI check (browser) | Time box |
|---|---|---|---|
| 0 | Skeleton, config, DB, health, serves the UI | Page loads and shows the health JSON | 30 min |
| 1 | PayPal client plus smoke script and status mapping | None (script only) | 90 min |
| 2 | AI client, validation, fallback, `/api/extract` | Extract and preview work for the example sentences | 45 min |
| 3 | `POST /api/invoices`, persistence, idempotency, simple `GET /api/invoices` | Create & Send makes a real PayPal invoice and a row appears | 60 min |
| 4 | Live status sync, `GET /api/invoices/<id>`, `/api/summary`, stale handling | Pay in sandbox, Refresh shows Paid, summary cards fill | 45 min |
| 5 | Demo overdue override, reminder draft and send | Simulate overdue, draft in three tones, send | 60 min |
| 6 | Hardening, tests, seed script, README, full audit | Fresh-clone run and demo rehearsal | 60 min |
| S | Submission (human) | None | 60 min |

Total about 7 hours. If time is short, a working Phase 3 is a valid minimum submission, and Phase 4 makes it a convincing one.

Each phase has a gate: do not start the next phase until the current one passes its acceptance criteria.

---

## 10. Later (not now): frontend enhancement

When the backend passes the audit, improve the UI. Ideas: proper layout and branding, charts, client address book, loading skeletons, toasts instead of message lines, mobile polish. The API contract in section 5 stays fixed so the UI can change freely.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Invoicing not enabled on the sandbox app | Phase 0 pre-work checklist, verified in Phase 1 |
| PayPal rejects the create body | Start from a minimal body, add fields one at a time, log PayPal's `details` array |
| Past due date rejected | Local `demo_due_override`, labelled in the UI and README |
| INR unsupported | `USD` default, INR behind config |
| AI call fails during the demo | Fallback parser and template, plus a backup recording |
| Sandbox email goes nowhere | Open the `recipient_url` directly and pay with the sandbox Personal account |
| UI and backend field names drift | Section 5 is the contract; the debug log shows mismatches immediately |
| Running out of time | Phase gates and the Phase 3 minimum |

---

## 12. Demo video script (target 2:30)

1. **0:00 to 0:15** Problem: freelancers lose time and money on invoicing and chasing.
2. **0:15 to 0:50** Type the sentence, show the AI preview, press Create & Send.
3. **0:50 to 1:20** Show the invoice in the PayPal sandbox and open the payer link.
4. **1:20 to 1:45** Pay as the sandbox Personal account, press Refresh, status turns Paid.
5. **1:45 to 2:15** Simulate overdue, draft the AI reminder in two tones, send it through PayPal.
6. **2:15 to 2:30** Recap: PayPal Invoicing plus AI, human approval, open source on GitHub.

Show the app running on the machine it was built on. Use no copyrighted music.

---

## 13. Devpost text outline

- **Inspiration:** freelancers spend unpaid hours writing invoices and chasing clients.
- **What it does:** the flow above in four sentences.
- **How we built it:** Flask, PayPal Invoicing API v2 sandbox, AI provider X for extraction and reminders, SQLite, a minimal JS test UI.
- **Challenges:** validating AI output before money-related actions, and the fallback design.
- **What's next:** webhooks, multi-currency, recurring invoices, a scheduled agent mode, and a full frontend.
- **Setup:** link to the README.
