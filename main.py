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

def summarize_with_claude(email_content: Dict) -> str:
    """Send email content to Claude for summarization"""
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    prompt = f"""Please provide a concise summary of this email:

Subject: {email_content['subject']}
From: {email_content['sender']}

Content:
{email_content['body'][:4000]}  # Limit content to avoid token limits

Please summarize the key points, important information, and any action items in 2-3 sentences."""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Error summarizing email: {e}"

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
    
    # Process each email
    for i, email in enumerate(emails, 1):
        print(f"\n--- Email {i}/{len(emails)} ---")
        
        # Extract content
        content = extract_email_content(email)
        print(f"Subject: {content['subject']}")
        print(f"From: {content['sender']}")
        
        # Summarize with Claude
        print("Generating summary...")
        summary = summarize_with_claude(content)
        print(f"Summary: {summary}")
        print("-" * 50)

if __name__ == "__main__":
    main()