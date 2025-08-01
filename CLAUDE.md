# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python application that processes unread Gmail emails, categorizes them using Claude AI, and generates summaries by category. The app uses Gmail API for email access and Anthropic's Claude API for intelligent categorization and summarization.

## Setup and Dependencies

- **Python Version**: 3.13+ (specified in pyproject.toml)
- **Virtual Environment**: Uses `.venv/` directory with Python virtual environment
- **Dependencies**: Managed via `pyproject.toml`, installed with `pip install -e .`

Key dependencies:
- `google-auth*` packages for Gmail API authentication
- `anthropic` for Claude AI API
- `python-dotenv` for environment variable management
- `gitingest` (purpose unclear from main code)

## Environment Configuration

Required environment variables in `.env`:
- `ANTHROPIC_API_KEY`: Claude API key for email categorization and summarization

Authentication files:
- `credentials.json`: Google OAuth client credentials (for Gmail API)
- `token.json`: Generated OAuth token (auto-created after first auth)

## Common Commands

Run the main application:
```bash
python main.py
```

Install dependencies:
```bash
pip install -e .
```

## Architecture

### Core Components

**main.py** - Single-file application with these key functions:

1. **Gmail Integration** (`main.py:18-40`)
   - `get_gmail_service()`: Handles OAuth authentication and token management
   - `get_unread_emails()`: Fetches unread emails via Gmail API
   - `mark_emails_as_read()`: Batch marks emails as read

2. **Email Processing** (`main.py:90-131`)
   - `extract_email_content()`: Parses email headers and body content (handles both plain text and HTML)
   - Recursively extracts content from multipart messages

3. **AI-Powered Categorization** (`main.py:133-183`)
   - `categorize_email()`: Uses Claude to categorize emails into predefined categories:
     - social_events, culture_arts, professional_tech, fashion
     - individual_recruitment, job_postings, other
   - Includes detailed categorization logic with recruitment vs. job posting distinction

4. **Summarization** (`main.py:185-266`)
   - `summarize_category()`: Generates category-specific summaries using Claude
   - Different prompt templates for recruitment vs. job postings vs. events
   - Limits email content to 2500 chars per email for summarization

### Data Flow

1. Authenticate with Gmail API
2. Fetch unread emails (max 50 by default)
3. Extract and clean email content
4. Categorize each email using Claude API
5. Group emails by category
6. Generate AI summaries for each category
7. Display results and mark emails as read

### Email Categories

The application uses a sophisticated categorization system with specific handling for:
- **Individual Recruitment**: Direct recruiter outreach, interview invitations
- **Job Postings**: Automated job alerts, digest emails from job boards
- **Events**: Social, cultural, professional, fashion events
- **Other**: Catch-all category