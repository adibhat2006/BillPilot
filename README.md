# BillPilot

BillPilot is an AI billing assistant for freelancers built for the "Build What's Next with PayPal and AI" hackathon.

It extracts structured invoice details from a single natural language sentence, presents a preview for user confirmation, and issues sandbox invoices via the PayPal Invoicing API v2. It also drafts and sends polite payment reminders for overdue invoices.

## Quick Start

### 1. Environment Setup

Create and activate a Python 3.11+ virtual environment:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Unix/macOS:
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

### 3. Run the App

```bash
python app.py
```

Open `http://localhost:5000` in your web browser.

## Testing

```bash
python -m pytest -q
```

## License

MIT License. See [LICENSE](LICENSE) for details.
