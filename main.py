import os
import base64
import re
from typing import List, Dict
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Gmail scope - just read Gmail
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Authenticate and return Gmail service"""
    creds = None
    
    # Load existing token if it exists
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If no valid credentials, authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh expired token
            creds.refresh(Request())
        else:
            # Run OAuth flow
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save credentials for next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return build('gmail', 'v1', credentials=creds)

def get_unread_emails(service, max_results=50) -> List[Dict]:
    """Fetch unread emails from Gmail"""
    try:
        # Search for unread messages
        results = service.users().messages().list(
            userId='me', 
            q='is:unread',
            maxResults=max_results
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for message in messages:
            # Get full message details
            msg = service.users().messages().get(
                userId='me', 
                id=message['id'],
                format='full'
            ).execute()
            
            emails.append(msg)
            
        return emails
    except Exception as e:
        print(f"Error fetching emails: {e}")
        return []

def extract_email_content(email_msg: Dict) -> Dict:
    """Extract subject, sender, and body content from email"""
    headers = email_msg['payload'].get('headers', [])
    
    # Extract headers
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
    
    # Extract body content
    body = ""
    
    def extract_body_recursive(payload):
        nonlocal body
        
        if 'parts' in payload:
            for part in payload['parts']:
                extract_body_recursive(part)
        else:
            if payload.get('mimeType') == 'text/plain':
                data = payload.get('body', {}).get('data')
                if data:
                    decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    body += decoded + "\n"
            elif payload.get('mimeType') == 'text/html':
                data = payload.get('body', {}).get('data')
                if data:
                    decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    # Simple HTML tag removal
                    clean_text = re.sub(r'<[^>]+>', '', decoded)
                    body += clean_text + "\n"
    
    extract_body_recursive(email_msg['payload'])
    
    # Clean up the body text
    body = re.sub(r'\n\s*\n', '\n\n', body.strip())
    
    return {
        'subject': subject,
        'sender': sender,
        'body': body,
        'id': email_msg['id']
    }

def summarize_emails_batch(all_emails_text: str) -> str:
    """Send all emails to Claude for batch summarization"""
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    prompt = f"""Please provide a comprehensive summary of all these unread emails. For each email, give a brief summary, and then provide an overall summary at the end highlighting the most important items and any action items.

{all_emails_text}

Please organize your response as:
1. Individual email summaries (1-2 sentences each)
2. Overall summary with key themes and action items"""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Error summarizing emails: {e}"

def main():
    """Main function to process unread emails"""
    print("Fetching unread emails...")
    
    # Get Gmail service
    service = get_gmail_service()
    
    # Get profile info
    profile = service.users().getProfile(userId='me').execute()
    print(f"Connected to: {profile['emailAddress']}")
    
    # Fetch unread emails
    emails = get_unread_emails(service)
    print(f"Found {len(emails)} unread emails")
    
    if not emails:
        print("No unread emails found.")
        return
    
    # Collect all emails into a single string
    all_emails_text = ""
    
    for i, email in enumerate(emails, 1):
        content = extract_email_content(email)
        
        # Add email to combined text with clear delineation
        email_section = f"""
=== EMAIL {i} ===
Subject: {content['subject']}
From: {content['sender']}

Content:
{content['body'][:3000]}  # Limit each email to avoid token limits

"""
        all_emails_text += email_section
    
    # Trim total content if too long (Claude has token limits)
    if len(all_emails_text) > 50000:  # Rough character limit
        all_emails_text = all_emails_text[:50000] + "\n\n[Content truncated due to length...]"
    
    print(f"\nGenerating comprehensive summary for all {len(emails)} emails...")
    
    # Get single summary for all emails
    summary = summarize_emails_batch(all_emails_text)
    
    print("\n" + "="*60)
    print("COMPREHENSIVE EMAIL SUMMARY")
    print("="*60)
    print(summary)
    print("="*60)

if __name__ == "__main__":
    main()