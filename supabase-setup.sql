-- =============================================
-- MahZeh?! — Supabase Database Setup
-- =============================================
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor)
-- This creates the documents table and security policies

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  filename TEXT,
  doc_type TEXT,
  doc_subtype TEXT,
  issuing_body TEXT,
  urgency TEXT,
  summary TEXT,
  translation TEXT,
  key_details JSONB DEFAULT '[]',
  action_items JSONB DEFAULT '[]',
  confidence INTEGER,
  text_length INTEGER,
  ocr_engine TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for fast user queries
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);

-- Row Level Security: users can only see their own documents
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- Policy: users can read their own documents
CREATE POLICY "Users can read own documents"
  ON documents FOR SELECT
  USING (auth.uid() = user_id);

-- Policy: service role can insert (server-side)
CREATE POLICY "Service role can insert documents"
  ON documents FOR INSERT
  WITH CHECK (true);

-- Policy: service role can read all (for server-side queries)
CREATE POLICY "Service role can read all"
  ON documents FOR SELECT
  USING (true);

-- Grant access
GRANT ALL ON documents TO authenticated;
GRANT ALL ON documents TO service_role;
