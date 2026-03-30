# MahZeh?! — Scan. Translate. Understand.

Hebrew documents made clear for every oleh.

Upload any Hebrew PDF — get an instant English translation, plain-language summary, and action items.

## Quick Start

```bash
pip install pypdf python-dotenv
cp .env.example .env        # add your ANTHROPIC_API_KEY
python3 server.py
```

Open http://localhost:8080 and upload a Hebrew PDF.

## Features

- **PDF Upload** — drag-and-drop or click to upload any Hebrew PDF
- **AI Translation** — full English translation via Claude API
- **Document Classification** — automatically identifies document type (government, medical, financial, legal, etc.)
- **Smart Summary** — plain-English explanation of what the document means
- **Action Items** — deadlines, payments, and next steps extracted automatically
- **Google Sign-in** — save your document history (Supabase auth)
- **WhatsApp Bot** — send a photo, get a summary back (Twilio integration)

## Full Setup

See [SETUP.md](SETUP.md) for the complete guide including Supabase, Google Auth, WhatsApp, and deployment.

## Tech Stack

- Python 3 (stdlib HTTP server — zero framework dependencies)
- Claude API (Anthropic) for translation and analysis
- pypdf for PDF text extraction
- Supabase for auth + database
- Twilio for WhatsApp integration
