# BillPilot ✈️🧾

**AI Billing Assistant for Freelancers**  
*Built for the "Build What's Next with PayPal and AI" Online Hackathon*

BillPilot transforms a single conversational sentence—such as *"Invoice Ravi $500 for logo design, due Friday"*—into a validated invoice preview and sends a real sandbox invoice via the **PayPal Invoicing API v2**. For overdue invoices, BillPilot contextually drafts polite payment reminders across customizable tones for user approval.

---

## 🌟 Key Highlights

- **PayPal Sandbox Invoicing API v2**: Creates draft invoices, sends them, generates payer checkout URLs, reads live payment statuses, and sends reminder emails directly from PayPal's infrastructure.
- **AI Extraction & Tone-Aware Reminders**: Extracts line items, quantities, rates, and relative dates using Gemini or Anthropic, with an offline deterministic regex and template fallback.
- **Strict Human-in-the-Loop**: Nothing is sent or created in PayPal without explicit user review and confirmation.
- **Robust Financial Guardrails**: Server-side validation rejects invalid emails, past due dates, zero or negative rates, and amounts exceeding \$50,000.
- **Idempotency Protection**: Browser-generated UUID keys prevent duplicate invoices if "Create & Send" is clicked repeatedly.
- **Live Status Tracking**: Automatically reflects `Paid`, `Unpaid`, `Overdue`, `Draft`, and `Cancelled` states from PayPal, computing overdue status locally.

---

## 🏗️ Architecture

```text
       Minimal Test UI (Vanilla HTML5 / CSS / ES6)
                           │  JSON over REST
                           ▼
                  Flask Application (app.py)
            ┌──────────────┬──────────────┬──────────────┐
            ▼              ▼              ▼              ▼
     ai_client.py    services.py   paypal_client.py    db.py
     (Gemini/Claude/  (Validation,  (OAuth2 Token,     (SQLite
      Fallback)        Status Map)   Invoices, Remind)  Storage)
                           │              │
                           │              ▼
                           └────► PayPal Sandbox REST API
                                  (api-m.sandbox.paypal.com)
```

---

## 🚀 Quick Start (Under 5 Minutes)

### 1. Prerequisites
- **Python 3.11+** installed
- A **PayPal Developer Sandbox** account ([developer.paypal.com](https://developer.paypal.com))

### 2. Clone & Virtual Environment

```bash
git clone https://github.com/your-username/BillPilot.git
cd BillPilot

# Create and activate virtual environment
python -m venv .venv

# Windows:
.\.venv\Scripts\activate

# macOS / Linux:
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your PayPal sandbox credentials:

```ini
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com

# AI Provider: 'gemini', 'anthropic', or 'none' (deterministic fallback)
AI_PROVIDER=none
AI_API_KEY=
AI_MODEL=

DEFAULT_CURRENCY=USD
ALLOWED_CURRENCIES=USD
MAX_AMOUNT=50000.0
DEMO_MODE=true
SANDBOX_PAYER_EMAIL=your-us-buyer-sandbox-email@personal.example.com
```

> **Important Sandbox Note:** PayPal sandbox accounts created under an Indian developer profile cannot send invoices to India-registered personal accounts due to RBI domestic transaction regulations. In your PayPal Developer Dashboard (**Testing Tools → Sandbox Accounts → Create Account**), create a **Personal Account** with Country/Region set to **United States** to use as your test buyer.

### 5. Run the Application

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your web browser.

---

## 🧪 Testing

Run the full pytest suite (offline validation, API mocks, and status mapping):

```bash
python -m pytest -v
```

Run the live PayPal Sandbox connectivity smoke test:

```bash
python scripts/paypal_smoke.py
```

Optional: Seed the local database with realistic test invoices:

```bash
python scripts/seed_demo.py
```

---

## 🎬 3-Minute Live Demo Flow

1. **Extract Invoice from Sentence**:
   - On the web UI, click **Example 1** (*"Invoice Ravi $500 for logo design, due Friday"*).
   - Click **Extract**. The AI extracts Client: `Ravi`, Amount: `500.00 USD`, Due: upcoming Friday, and flags missing `client_email`.
2. **Review & Confirm**:
   - Enter your US sandbox personal buyer email into the **Client email** field.
   - Click **Create & Send via PayPal**.
   - A real invoice is generated and sent via PayPal. The invoice appears in the dashboard table with status **Unpaid**.
3. **Pay in Sandbox**:
   - Right-click **Pay link** on the invoice row and open in an **Incognito / Private Window**.
   - Log in using your sandbox personal buyer credentials and click **Pay Now**.
4. **Live Status Refresh**:
   - Return to BillPilot and click **Refresh**.
   - The invoice badge dynamically turns to green **`Paid`**, and summary cards update collected totals.
5. **Simulate Overdue & AI Reminder**:
   - For an unpaid invoice, click **Simulate overdue** (demo override backdating due date).
   - Badge turns to red **`Overdue`**.
   - Click **Reminder**: preview the auto-drafted reminder (`firm` tone).
   - Switch tone to `final` and click **Draft reminder**.
   - Click **Send via PayPal (after your approval)**. PayPal's remind endpoint dispatches the notification!

---

## 📡 API Contract Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service health and configuration state (no secrets). |
| `POST` | `/api/extract` | Converts billing sentence into structured preview JSON. |
| `POST` | `/api/invoices` | Creates and sends PayPal invoice with idempotency protection. |
| `GET` | `/api/invoices` | Lists invoices with live status sync from PayPal. |
| `GET` | `/api/invoices/<id>` | Fetches single invoice with live status. |
| `POST` | `/api/demo/<id>/make-overdue` | Demo override to simulate overdue status. |
| `POST` | `/api/invoices/<id>/reminder-draft` | Generates reminder copy in chosen tone (`friendly`, `firm`, `final`). |
| `POST` | `/api/invoices/<id>/reminder-send` | Dispatches reminder via PayPal's remind endpoint. |
| `GET` | `/api/invoices/<id>/reminders` | Retrieves reminder audit history for an invoice. |
| `GET` | `/api/summary` | Dashboard summary cards (billed, collected, outstanding, overdue). |

---

## 🛡️ Safety & Guardrails

- **Zero-Trust Model Parsing**: Model output is treated strictly as untrusted input. Regex and mathematical bounds enforce positive integer quantities, positive unit prices $\le \$50,000$, and valid ISO dates.
- **Prompt Injection Defense**: Invoices sentences are wrapped in strict delimiters and processed as raw text data; instructions attempting to override amounts or leak variables are neutralized.
- **Secrets Management**: Credentials reside exclusively in gitignored `.env`. Access tokens are cached in memory and masked in logs.
- **Safe HTML**: The user interface exclusively binds dynamic content via `textContent` and `document.createTextNode` (zero `innerHTML` vulnerabilities).

---

## 📄 Limitations & Disclosures

- **PayPal Sandbox Only**: Hardcoded to `https://api-m.sandbox.paypal.com`. No live money or live credentials are used.
- **Currency**: PayPal Invoicing API v2 supports standard cross-border currencies (USD default). Domestic INR invoicing is restricted on PayPal India sandbox accounts.
- **Minimal UI**: Plain HTML/CSS test harness designed specifically for reliable evaluation and judging.

---

## ⚖️ License

MIT License. See [LICENSE](LICENSE) for full details.
