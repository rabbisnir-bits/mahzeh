# MahZeh?! — Setup Guide

Get MahZeh running in under 30 minutes.

## Step 1: Claude API Key (5 min)

1. Go to https://console.anthropic.com
2. Create an account if you don't have one
3. Go to API Keys → Create Key
4. Copy the key (starts with `sk-ant-...`)

## Step 2: Supabase Setup (10 min)

1. Go to https://supabase.com → New Project
2. Name it "mahzeh", pick a region close to Israel (eu-central-1 is good)
3. Wait for it to provision (~2 min)

**Get your keys:**
- Go to Settings → API
- Copy: `Project URL` → this is your SUPABASE_URL
- Copy: `anon public` key → this is your SUPABASE_ANON_KEY
- Copy: `service_role` key → this is your SUPABASE_SERVICE_KEY

**Create the database table:**
- Go to SQL Editor
- Paste the contents of `supabase-setup.sql` and run it

**Enable Google Auth:**
- Go to Authentication → Providers → Google
- Enable it
- You'll need a Google Cloud OAuth client ID:
  - Go to https://console.cloud.google.com
  - Create a project (or use existing)
  - APIs & Services → Credentials → Create OAuth Client ID
  - Application type: Web application
  - Authorized redirect URI: `https://YOUR-PROJECT.supabase.co/auth/v1/callback`
  - Copy the Client ID and Client Secret into Supabase

## Step 3: WhatsApp Bot via Twilio (10 min)

1. Go to https://www.twilio.com → Sign up
2. Get your Account SID and Auth Token from the Console Dashboard
3. Go to Messaging → Try it Out → Send a WhatsApp message
4. Twilio gives you a sandbox number (format: `whatsapp:+14155238886`)
5. Follow the instructions to join the sandbox (send "join <keyword>" to the number)

**Set up the webhook:**
- Go to Messaging → Settings → WhatsApp Sandbox Settings
- Set "When a message comes in" URL to: `https://your-server.com/api/whatsapp`
- Method: POST

**For production (your own number):**
- Apply for a WhatsApp Business number through Twilio
- This takes 1-2 business days for approval

## Step 4: Configure & Run

```bash
# Clone/copy the mahzeh-app folder
cd mahzeh-app

# Install dependencies
pip install pypdf python-dotenv

# Optional (for better PDF extraction):
pip install pdfplumber

# Optional (for WhatsApp image OCR):
pip install pytesseract Pillow
# Also install Tesseract: apt install tesseract-ocr tesseract-ocr-heb

# Copy and fill in your config
cp .env.example .env
nano .env    # fill in all your keys

# Run
python3 server.py
```

Open http://localhost:8080 — you should see MahZeh!

## Step 5: Deploy to Production

### Option A: Railway (easiest)
1. Push your code to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Add all your .env variables in the Railway dashboard
4. Railway gives you a public URL

### Option B: Render
1. Push to GitHub
2. Go to https://render.com → New Web Service
3. Set start command: `python3 server.py`
4. Add environment variables
5. Deploy

### Option C: VPS (DigitalOcean, etc.)
```bash
ssh your-server
git clone your-repo
cd mahzeh-app
pip install pypdf python-dotenv
cp .env.example .env && nano .env
# Run with systemd or screen:
screen -S mahzeh python3 server.py
```

## Quick Test

1. Open http://localhost:8080
2. Upload any Hebrew PDF
3. Wait ~10 seconds
4. You should see: classification, summary, key details, action items, and full translation

## File Structure

```
mahzeh-app/
├── server.py           # Backend: HTTP server + Claude API + Supabase + WhatsApp
├── static/
│   └── index.html      # Frontend: upload UI + auth + results + history
├── supabase-setup.sql  # Database schema (run in Supabase SQL Editor)
├── .env.example        # Config template
├── SETUP.md            # This file
└── README.md           # Quick start
```
