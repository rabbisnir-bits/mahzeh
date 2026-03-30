"""
MahZeh?! — Full Backend Server
Scan. Translate. Understand.

Features:
- PDF upload + Hebrew text extraction
- Claude API for translation/classification/summarization
- Supabase auth (JWT verification)
- Document history storage (Supabase DB)
- WhatsApp bot webhook (Twilio)

To run:
  pip install pypdf supabase twilio python-dotenv
  cp .env.example .env   # fill in your keys
  python3 server.py
"""

import http.server
import json
import os
import sys
import io
import base64
import tempfile
import traceback
import hashlib
import hmac
import time
import re
import urllib.request
import urllib.error
import ssl
from urllib.parse import urlparse, parse_qs, unquote

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ========================================
# Configuration
# ========================================
CONFIG = {
    'ANTHROPIC_API_KEY': os.environ.get('ANTHROPIC_API_KEY', ''),
    'SUPABASE_URL': os.environ.get('SUPABASE_URL', ''),
    'SUPABASE_ANON_KEY': os.environ.get('SUPABASE_ANON_KEY', ''),
    'SUPABASE_SERVICE_KEY': os.environ.get('SUPABASE_SERVICE_KEY', ''),
    'TWILIO_ACCOUNT_SID': os.environ.get('TWILIO_ACCOUNT_SID', ''),
    'TWILIO_AUTH_TOKEN': os.environ.get('TWILIO_AUTH_TOKEN', ''),
    'TWILIO_WHATSAPP_NUMBER': os.environ.get('TWILIO_WHATSAPP_NUMBER', ''),
    'WHATSAPP_VERIFY_TOKEN': os.environ.get('WHATSAPP_VERIFY_TOKEN', 'mahzeh-verify'),
    'PORT': int(os.environ.get('PORT', 8080)),
}

# ========================================
# PDF Text Extraction
# ========================================
def extract_text_from_pdf(pdf_bytes):
    """Extract text from PDF using available libraries."""
    text = ""

    # Try pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n\n"
        if text.strip():
            return text.strip(), "pypdf"
    except Exception as e:
        print(f"  pypdf failed: {e}")

    # Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
        if text.strip():
            return text.strip(), "pdfplumber"
    except Exception as e:
        print(f"  pdfplumber failed: {e}")

    # Try pdftotext CLI
    try:
        import subprocess
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(pdf_bytes)
            tmp = f.name
        result = subprocess.run(['pdftotext', '-layout', tmp, '-'], capture_output=True, text=True, timeout=30)
        os.unlink(tmp)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), "pdftotext"
    except Exception:
        pass

    return text.strip() if text.strip() else None, "none"


# ========================================
# Image OCR (for WhatsApp photos)
# ========================================
def extract_text_from_image(image_bytes):
    """Extract Hebrew text from an image using available OCR."""

    # Try Google Cloud Vision
    try:
        from google.cloud import vision
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.text_detection(image=image, image_context={"language_hints": ["he"]})
        if response.text_annotations:
            return response.text_annotations[0].description, "google_vision"
    except Exception as e:
        print(f"  Google Vision failed: {e}")

    # Try Tesseract
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang='heb')
        if text.strip():
            return text.strip(), "tesseract"
    except Exception as e:
        print(f"  Tesseract failed: {e}")

    return None, "none"


# ========================================
# Claude API
# ========================================
# ---- PASS 1: Read PDF + Classify (reads the PDF images, extracts text, classifies) ----
CLASSIFY_PROMPT = """You are MahZeh, an AI that helps English-speaking immigrants in Israel understand Hebrew documents.

Read this Hebrew document carefully. You have TWO jobs in this response:

JOB 1 — Extract ALL Hebrew text from every page. Copy the Hebrew characters exactly as written. For non-text pages (drawings, diagrams), write "[PAGE X: architectural drawing/site plan — description]".

JOB 2 — Classify the document.

Return a JSON object with these fields:

{
  "hebrew_text": "The complete Hebrew text from all pages, separated by \\n--- PAGE X ---\\n markers. Copy the Hebrew EXACTLY as written — every word, every number, every clause. This is critical for accurate translation later.",
  "doc_type": "government" | "medical" | "financial" | "legal" | "military" | "education" | "employment" | "home_services" | "real_estate" | "other",
  "doc_subtype": "Specific type in English with Hebrew transliteration, e.g. 'Irrevocable Power of Attorney (Yipui Ko'ach Bilti Chozer)'",
  "issuing_body": "Who issued this — use the ACTUAL name from the document, transliterated accurately",
  "urgency": "low" | "medium" | "high",
  "summary": "3-5 sentences explaining what this document IS and what it MEANS. If the PDF contains multiple sub-documents, list each one. Be specific — include names, ID numbers, addresses, dates. Write for someone who just made aliyah.",
  "key_details": [{"label": "...", "value": "..."}],
  "action_items": [{"type": "deadline|payment|document|contact|visit|info", "title": "...", "description": "...", "due_date": null, "amount_nis": null}],
  "confidence": 0-100,
  "pages_detected": "e.g. '8 pages: power of attorney, site plan, board protocol, tax form 7009, cooperation agreement'"
}

CRITICAL:
- The hebrew_text field is the MOST IMPORTANT. Copy every Hebrew word from every page. Do not summarize or skip.
- For company names on stamps/logos, read the ENGLISH text on the stamp if visible (e.g. "FERNCROFT LTD." not "Fern Boff").
- ID numbers must be copied exactly as printed.
- Do not invent or guess. If unclear, write "[unclear]".

Respond ONLY with valid JSON."""


# ---- PASS 2: Translate (dedicated, thorough) ----
TRANSLATE_PROMPT = """You are a professional Hebrew document translator. Your translations are used by immigrants who depend on accuracy to understand their rights and obligations. A wrong name, number, or term could cost someone money or legal trouble.

Translate this Hebrew document into the TARGET LANGUAGE specified in the user's message. If no target language is specified, translate into English. Follow these rules:

1. TRANSLATE EVERY PAGE, EVERY CLAUSE, EVERY LINE. Do not skip or summarize.
2. Preserve the document's structure: headers, numbered clauses (1, 1.1, 1.2, etc.), sections, signature blocks.
3. Transliterate key Hebrew legal/bureaucratic terms in parentheses on first use:
   - "irrevocable power of attorney (yipui ko'ach bilti chozer)"
   - "cooperation agreement (heskem shituf)"
   - "land registry (tabu)"
   - "plot (chelka)", "block (gush)"
4. NAMES AND ENTITIES — be extremely careful:
   - Transliterate Hebrew names letter-by-letter (e.g. שניר דוד ביטון = "Snir David Bitton").
   - For company names that appear in stamps/logos/letterheads, read the ENGLISH text printed on the stamp itself if visible. Example: if a stamp says "FERNCROFT LTD." in English, use that exact spelling — do not guess from blurry Hebrew.
   - Cross-reference: if the same entity appears multiple times, use the SAME spelling every time.
   - ID numbers must be copied exactly as printed.
5. For forms: translate field labels and any filled-in values. Note checkboxes as [checked] or [unchecked].
6. For architectural drawings: describe what the drawing shows (plot boundaries, measurements, etc.).
7. If a word or section is truly illegible, write [illegible] — do NOT make something up.
8. Separate each page/document with a clear header like "--- PAGE 2: Board Protocol ---"
9. NUMBERS AND MEASUREMENTS: Copy exactly as written. Areas in sq.m., currency in NIS. Do not round or estimate.
10. After translating, do a consistency check: are all names, ID numbers, and addresses spelled the same way throughout?

This is the MOST IMPORTANT part of the analysis. People make legal and financial decisions based on your translation. Be precise."""


def _build_pdf_content(pdf_bytes, user_text):
    """Build API message content with PDF or text."""
    if pdf_bytes:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode('utf-8')
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64
                }
            },
            {
                "type": "text",
                "text": user_text
            }
        ]
    return None


def _call_claude(system_prompt, user_content, api_key, max_tokens=8000):
    """Core Claude API call."""

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}]
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'anthropic-beta': 'pdfs-2024-09-25'
        },
        method='POST'
    )

    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=300) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        return data.get('content', [{}])[0].get('text', '').strip()


def call_claude_api(hebrew_text, api_key, pdf_bytes=None, target_lang='English'):
    """Two-pass document analysis: Pass 1 reads PDF + classifies, Pass 2 translates extracted text."""

    lang_instruction = f"Translate into {target_lang}." if target_lang != 'English' else ""

    if pdf_bytes:
        content_classify = _build_pdf_content(pdf_bytes, f"Read this Hebrew document. Extract ALL Hebrew text and classify it. Write the summary and action items in {target_lang}. Return JSON only.")
    else:
        content_classify = f"Read this Hebrew document. Extract ALL Hebrew text and classify it. Write the summary and action items in {target_lang}. Return JSON only.\n\n{hebrew_text}"

    # PASS 1: Read PDF + extract Hebrew text + classify
    print("  Pass 1: Reading PDF and extracting Hebrew text...")
    try:
        raw = _call_claude(CLASSIFY_PROMPT, content_classify, api_key, max_tokens=12000)
        # Strip markdown wrapping
        if raw.startswith('```json'): raw = raw[7:]
        if raw.startswith('```'): raw = raw[3:]
        if raw.endswith('```'): raw = raw[:-3]
        result = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"Classification returned invalid JSON: {str(e)[:100]}"
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f"  Claude API HTTP {e.code}: {error_body[:500]}")
        if e.code == 401:
            return None, "Invalid API key. Please check your ANTHROPIC_API_KEY."
        return None, f"Claude API error ({e.code}): {error_body[:200]}"
    except Exception as e:
        return None, f"Classification failed: {str(e)}"

    print(f"  Pass 1 done: {result.get('doc_type')} / {result.get('doc_subtype')}")

    # Get the extracted Hebrew text from Pass 1
    extracted_hebrew = result.pop('hebrew_text', '')
    if extracted_hebrew:
        print(f"  Extracted {len(extracted_hebrew)} chars of Hebrew text")
    else:
        print("  WARNING: No Hebrew text extracted, falling back to PDF for translation")

    # PASS 2: Translate the EXTRACTED TEXT (not the PDF again)
    print(f"  Pass 2: Translating Hebrew text into {target_lang} (this takes a minute)...")
    try:
        if extracted_hebrew:
            translate_content = f"Translate this Hebrew document into {target_lang}. Every page, every clause, every line.\n\n{extracted_hebrew}"
        elif pdf_bytes:
            translate_content = _build_pdf_content(pdf_bytes, f"Translate this entire Hebrew document into {target_lang}. Every page, every clause.")
        else:
            translate_content = f"Translate this entire Hebrew document into {target_lang}. Every page, every clause.\n\n{hebrew_text}"

        translation = _call_claude(TRANSLATE_PROMPT, translate_content, api_key, max_tokens=16000)
        result['translation'] = translation
    except Exception as e:
        print(f"  Translation failed: {e}")
        result['translation'] = f"Translation failed: {str(e)}. The classification and summary above are still accurate."

    print(f"  Pass 2 done: {len(result.get('translation',''))} chars translated")
    return result, None


def call_claude_api_short(hebrew_text, api_key):
    """Claude API call optimized for WhatsApp (shorter response)."""
    import urllib.request
    import ssl

    prompt = f"""Analyze this Hebrew document text and respond in this EXACT format (plain text, not JSON):

📄 DOCUMENT TYPE: [type]
📍 FROM: [issuing body]

📝 SUMMARY:
[2-3 sentence explanation of what this document is and what it means]

⚡ WHAT TO DO:
[Numbered list of action items with any deadlines]

💰 KEY AMOUNTS: [any NIS amounts mentioned]
📅 KEY DATES: [any important dates]

Keep it concise — this goes to WhatsApp.

Hebrew text:
{hebrew_text}"""

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('content', [{}])[0].get('text', 'Error processing document'), None
    except Exception as e:
        return None, str(e)


# ========================================
# Supabase Integration
# ========================================
def supabase_request(method, path, data=None, service_key=False):
    """Make a request to Supabase REST API."""
    import urllib.request

    url = f"{CONFIG['SUPABASE_URL']}/rest/v1/{path}"
    key = CONFIG['SUPABASE_SERVICE_KEY'] if service_key else CONFIG['SUPABASE_ANON_KEY']

    headers = {
        'apikey': key,
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }

    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8')), None
    except Exception as e:
        return None, str(e)


def verify_supabase_jwt(token):
    """Verify a Supabase JWT and extract user info."""
    import urllib.request

    url = f"{CONFIG['SUPABASE_URL']}/auth/v1/user"
    headers = {
        'apikey': CONFIG['SUPABASE_ANON_KEY'],
        'Authorization': f'Bearer {token}'
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            user = json.loads(resp.read().decode('utf-8'))
            return user, None
    except Exception as e:
        return None, str(e)


def save_document(user_id, filename, result, text_length, ocr_engine):
    """Save a processed document to Supabase."""
    if not CONFIG['SUPABASE_URL']:
        return None, "Supabase not configured"

    data = {
        'user_id': user_id,
        'filename': filename,
        'doc_type': result.get('doc_type'),
        'doc_subtype': result.get('doc_subtype'),
        'issuing_body': result.get('issuing_body'),
        'urgency': result.get('urgency'),
        'summary': result.get('summary'),
        'translation': result.get('translation'),
        'key_details': json.dumps(result.get('key_details', [])),
        'action_items': json.dumps(result.get('action_items', [])),
        'confidence': result.get('confidence'),
        'text_length': text_length,
        'ocr_engine': ocr_engine
    }

    return supabase_request('POST', 'documents', data, service_key=True)


def get_user_documents(user_id):
    """Get a user's document history."""
    if not CONFIG['SUPABASE_URL']:
        return [], None
    path = f"documents?user_id=eq.{user_id}&order=created_at.desc&limit=50"
    return supabase_request('GET', path, service_key=True)


# ========================================
# WhatsApp Bot (Twilio)
# ========================================
def handle_whatsapp_message(form_data):
    """Process an incoming WhatsApp message."""
    from_number = form_data.get('From', [''])[0]
    body = form_data.get('Body', [''])[0]
    num_media = int(form_data.get('NumMedia', ['0'])[0])
    media_url = form_data.get('MediaUrl0', [''])[0]
    media_type = form_data.get('MediaContentType0', [''])[0]

    print(f"  WhatsApp from {from_number}: {body[:50]}... Media: {num_media}")

    # Welcome / help message
    if body.strip().lower() in ['hi', 'hello', 'help', 'start', 'שלום', 'התחל']:
        return format_whatsapp_reply(
            "👋 Welcome to MahZeh!\n\n"
            "Send me a *photo* of any Hebrew document and I'll:\n"
            "📝 Translate it to English\n"
            "📋 Explain what it means\n"
            "⚡ Tell you what to do next\n\n"
            "Just snap a photo and send it!"
        )

    # No image attached
    if num_media == 0:
        return format_whatsapp_reply(
            "📸 Please send a *photo* of your Hebrew document.\n\n"
            "Just take a picture of the letter, bill, or form and send it here!"
        )

    # Process the image
    try:
        # Download the image from Twilio
        image_bytes = download_twilio_media(media_url)
        if not image_bytes:
            return format_whatsapp_reply("❌ Sorry, I couldn't download that image. Please try again.")

        # Check if it's a PDF
        if 'pdf' in media_type.lower():
            text, engine = extract_text_from_pdf(image_bytes)
        else:
            text, engine = extract_text_from_image(image_bytes)

        if not text:
            return format_whatsapp_reply(
                "❌ I couldn't read the text in that image.\n\n"
                "Tips for better results:\n"
                "• Make sure the document is well-lit\n"
                "• Hold your phone steady and close\n"
                "• Try to capture the full page\n"
                "• Avoid shadows and glare"
            )

        # Process with Claude
        api_key = CONFIG['ANTHROPIC_API_KEY']
        if not api_key:
            return format_whatsapp_reply("⚠️ Server configuration error. Please try the web app at mahzeh.app")

        summary, error = call_claude_api_short(text, api_key)
        if error:
            return format_whatsapp_reply(f"❌ Analysis error: {error[:100]}")

        # Send the result
        reply = f"🔍 *MahZeh Analysis*\n\n{summary}\n\n---\n💡 For full translation, use the web app: mahzeh.app"
        return format_whatsapp_reply(reply)

    except Exception as e:
        traceback.print_exc()
        return format_whatsapp_reply(f"❌ Sorry, something went wrong. Please try again.\n\nError: {str(e)[:100]}")


def download_twilio_media(url):
    """Download media from Twilio with authentication."""
    import urllib.request
    import base64

    credentials = base64.b64encode(
        f"{CONFIG['TWILIO_ACCOUNT_SID']}:{CONFIG['TWILIO_AUTH_TOKEN']}".encode()
    ).decode()

    req = urllib.request.Request(url, headers={'Authorization': f'Basic {credentials}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        print(f"  Failed to download Twilio media: {e}")
        return None


def format_whatsapp_reply(message):
    """Format a TwiML response for Twilio."""
    # Escape XML characters
    message = message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'


def send_whatsapp_message(to_number, message):
    """Send a WhatsApp message via Twilio REST API."""
    import urllib.request
    import urllib.parse

    url = f"https://api.twilio.com/2010-04-01/Accounts/{CONFIG['TWILIO_ACCOUNT_SID']}/Messages.json"

    data = urllib.parse.urlencode({
        'From': CONFIG['TWILIO_WHATSAPP_NUMBER'],
        'To': to_number,
        'Body': message
    }).encode()

    import base64
    credentials = base64.b64encode(
        f"{CONFIG['TWILIO_ACCOUNT_SID']}:{CONFIG['TWILIO_AUTH_TOKEN']}".encode()
    ).decode()

    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Basic {credentials}',
        'Content-Type': 'application/x-www-form-urlencoded'
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8')), None
    except Exception as e:
        return None, str(e)


# ========================================
# HTTP Server
# ========================================
class MahZehHandler(http.server.SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/health':
            self.send_json({
                "status": "ok",
                "api_key_set": bool(CONFIG['ANTHROPIC_API_KEY']),
                "supabase_configured": bool(CONFIG['SUPABASE_URL']),
                "whatsapp_configured": bool(CONFIG['TWILIO_ACCOUNT_SID'])
            })
        elif parsed.path == '/api/documents':
            self.handle_get_documents()
        elif parsed.path == '/api/whatsapp' and parse_qs(parsed.query).get('hub.mode'):
            # WhatsApp webhook verification (Meta)
            params = parse_qs(parsed.query)
            if params.get('hub.verify_token', [''])[0] == CONFIG['WHATSAPP_VERIFY_TOKEN']:
                challenge = params.get('hub.challenge', [''])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(challenge.encode())
            else:
                self.send_error(403)
        else:
            self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/process':
            self.handle_process()
        elif parsed.path == '/api/report':
            self.handle_report()
        elif parsed.path == '/api/whatsapp':
            self.handle_whatsapp_webhook()
        else:
            self.send_error(404)

    def handle_report(self):
        """Log a user-submitted issue report."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length).decode('utf-8'))

            # Save to reports log file
            report_file = os.path.join(APP_DIR, 'reports.jsonl')
            with open(report_file, 'a', encoding='utf-8') as f:
                body['received_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(json.dumps(body, ensure_ascii=False) + '\n')

            print(f"  ⚠ Issue reported: {body.get('type')} — {body.get('details','')[:80]}")
            self.send_json({"success": True})
        except Exception as e:
            print(f"  Report error: {e}")
            self.send_json({"success": True})  # Don't show error to user

    def handle_process(self):
        """Process PDF upload through the MahZeh pipeline."""
        try:
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self.send_json({"error": "Expected multipart/form-data"}, 400)
                return

            boundary = content_type.split('boundary=')[1].strip()
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            pdf_bytes, filename = self.extract_file_from_multipart(body, boundary)
            if not pdf_bytes:
                self.send_json({"error": "No PDF file found"}, 400)
                return

            # Extract target language from form
            target_lang = self.extract_field_from_multipart(body, boundary, 'language') or 'English'
            print(f"  Processing: {filename} ({len(pdf_bytes)} bytes) → {target_lang}")

            # Claude API
            api_key = CONFIG['ANTHROPIC_API_KEY']
            if not api_key:
                self.send_json({"error": "ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=sk-ant-..."}, 500)
                return

            # Strategy: Try sending PDF directly to Claude (best quality).
            # Fall back to local text extraction if PDF is too large (>25MB).
            text = None
            engine = "claude_pdf"

            if len(pdf_bytes) > 25 * 1024 * 1024:
                # Too large for direct PDF — try local extraction
                print("  PDF too large for direct upload, trying local extraction...")
                text, engine = extract_text_from_pdf(pdf_bytes)
                if not text:
                    self.send_json({"error": "PDF is too large and local text extraction failed."}, 422)
                    return
                print(f"  Extracted {len(text)} chars via {engine}")

            print("  Calling Claude API...")
            if text:
                result, error = call_claude_api(text, api_key, target_lang=target_lang)
            else:
                result, error = call_claude_api(None, api_key, pdf_bytes=pdf_bytes, target_lang=target_lang)

            if error:
                self.send_json({"error": f"Claude API: {error}"}, 500)
                return

            text_length = len(text) if text else len(pdf_bytes)
            print(f"  Result: {result.get('doc_type')} / {result.get('doc_subtype')}")

            # Save to Supabase if user is authenticated
            user_id = None
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer ') and CONFIG['SUPABASE_URL']:
                token = auth_header[7:]
                user, err = verify_supabase_jwt(token)
                if user and not err:
                    user_id = user.get('id')
                    save_document(user_id, filename or 'upload.pdf', result, text_length, engine)
                    print(f"  Saved to DB for user {user_id[:8]}...")

            self.send_json({
                "success": True,
                "ocr_engine": engine,
                "text_length": text_length,
                "saved": bool(user_id),
                "result": result
            })

        except Exception as e:
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def handle_get_documents(self):
        """Get document history for authenticated user."""
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            self.send_json({"error": "Not authenticated"}, 401)
            return

        if not CONFIG['SUPABASE_URL']:
            self.send_json({"documents": []})
            return

        token = auth_header[7:]
        user, err = verify_supabase_jwt(token)
        if not user:
            self.send_json({"error": "Invalid token"}, 401)
            return

        docs, err = get_user_documents(user['id'])
        self.send_json({"documents": docs or []})

    def handle_whatsapp_webhook(self):
        """Handle incoming WhatsApp messages via Twilio."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')

            # Parse URL-encoded form data from Twilio
            form_data = parse_qs(body)

            print(f"  WhatsApp webhook received")
            reply_xml = handle_whatsapp_message(form_data)

            self.send_response(200)
            self.send_header('Content-Type', 'text/xml')
            self.end_headers()
            self.wfile.write(reply_xml.encode('utf-8'))

        except Exception as e:
            traceback.print_exc()
            self.send_response(500)
            self.end_headers()

    def extract_field_from_multipart(self, body, boundary, field_name):
        """Extract a text field value from multipart data."""
        boundary_bytes = boundary.encode()
        parts = body.split(b'--' + boundary_bytes)
        for part in parts:
            if field_name.encode() in part and b'filename=' not in part:
                name_match = re.search(b'name="([^"]*)"', part)
                if name_match and name_match.group(1).decode() == field_name:
                    header_end = part.find(b'\r\n\r\n')
                    if header_end == -1:
                        header_end = part.find(b'\n\n')
                        value = part[header_end + 2:] if header_end != -1 else b''
                    else:
                        value = part[header_end + 4:]
                    value = value.strip().rstrip(b'-').strip()
                    return value.decode('utf-8', errors='replace')
        return None

    def extract_file_from_multipart(self, body, boundary):
        """Extract file bytes and filename from multipart data."""
        boundary_bytes = boundary.encode()
        parts = body.split(b'--' + boundary_bytes)
        for part in parts:
            if b'filename=' in part and b'Content-Type' in part:
                # Extract filename
                filename = 'upload.pdf'
                fname_match = re.search(b'filename="([^"]*)"', part)
                if fname_match:
                    filename = fname_match.group(1).decode('utf-8', errors='replace')

                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    header_end = part.find(b'\n\n')
                    file_data = part[header_end + 2:] if header_end != -1 else None
                else:
                    file_data = part[header_end + 4:]

                if file_data:
                    if file_data.endswith(b'\r\n'): file_data = file_data[:-2]
                    elif file_data.endswith(b'--\r\n'): file_data = file_data[:-4]
                    return file_data, filename

        return None, None

    def serve_static(self, path):
        """Serve static frontend files."""
        if path == '/' or path == '': path = '/index.html'
        file_path = os.path.join(APP_DIR, 'static', path.lstrip('/'))
        if os.path.isfile(file_path):
            self.send_response(200)
            ct = 'text/html' if path.endswith('.html') else 'application/javascript' if path.endswith('.js') else 'text/css' if path.endswith('.css') else 'application/octet-stream'
            self.send_header('Content-Type', f'{ct}; charset=utf-8')
            self._cors_headers()
            self.end_headers()
            with open(file_path, 'rb') as f:
                self.wfile.write(f.read())
        else:
            # SPA fallback
            index = os.path.join(APP_DIR, 'static', 'index.html')
            if os.path.isfile(index):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self._cors_headers()
                self.end_headers()
                with open(index, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def log_message(self, format, *args):
        print(f"[MahZeh] {args[0]}")


# ========================================
# Main
# ========================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))

if __name__ == '__main__':
    c = CONFIG
    print(f"""
    ╔══════════════════════════════════════════════╗
    ║           MahZeh?! Server v2.0               ║
    ║     Scan. Translate. Understand.             ║
    ╠══════════════════════════════════════════════╣
    ║  Server:     http://localhost:{c['PORT']}            ║
    ║  Claude API: {'✓ Key set (' + c['ANTHROPIC_API_KEY'][:10] + '...)' if c['ANTHROPIC_API_KEY'] else '✗ NOT SET — run: export ANTHROPIC_API_KEY=sk-ant-...':36s}║
    ║  Supabase:   {'✓ Connected' if c['SUPABASE_URL'] else '○ Not configured (runs without auth)':36s}║
    ║  WhatsApp:   {'✓ Ready' if c['TWILIO_ACCOUNT_SID'] else '○ Not configured':36s}║
    ╚══════════════════════════════════════════════╝
    """)

    if not c['ANTHROPIC_API_KEY']:
        print("  ⚠  Set ANTHROPIC_API_KEY in .env to enable document processing")
    if not c['SUPABASE_URL']:
        print("  ℹ  No Supabase config — app works without auth (no document history)")
    if not c['TWILIO_ACCOUNT_SID']:
        print("  ℹ  No Twilio config — WhatsApp bot disabled")
    print()

    server = http.server.HTTPServer(('0.0.0.0', c['PORT']), MahZehHandler)
    print(f"  Listening on port {c['PORT']}...\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()
