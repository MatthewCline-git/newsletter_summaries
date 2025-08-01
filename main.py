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

# Gmail scope - read and modify Gmail messages
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

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

def mark_emails_as_read(service, email_ids: List[str]):
    """Mark a list of emails as read by removing the UNREAD label"""
    try:
        if not email_ids:
            return
            
        # Gmail API allows batch operations
        service.users().messages().batchModify(
            userId='me',
            body={
                'ids': email_ids,
                'removeLabelIds': ['UNREAD']
            }
        ).execute()
        
        print(f"Marked {len(email_ids)} emails as read.")
        
    except Exception as e:
        print(f"Error marking emails as read: {e}")

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

def categorize_email(email_content: Dict) -> str:
    """Categorize a single email into one of the predefined topics"""
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    prompt = f"""Please categorize this email into ONE of these categories based on its content:

1. social_events - Social gatherings, parties, meetups, casual events
2. culture_arts - Cultural events, art shows, museums, theater, music, exhibitions
3. professional_tech - Professional networking, tech events, conferences, workshops, career-related
4. fashion - Fashion events, style, clothing, beauty, fashion shows
5. other - Anything that doesn't clearly fit the above categories

Email:
Subject: {email_content['subject']}
From: {email_content['sender']}
Content: {email_content['body'][:1000]}

Respond with ONLY the category name (e.g., "social_events" or "culture_arts")."""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        category = response.content[0].text.strip().lower()
        
        # Validate category
        valid_categories = ['social_events', 'culture_arts', 'professional_tech', 'fashion', 'other']
        if category in valid_categories:
            return category
        else:
            return 'other'
    except Exception as e:
        print(f"Error categorizing email: {e}")
        return 'other'

def summarize_category(category_name: str, emails_in_category: List[Dict]) -> str:
    """Create a comprehensive summary for emails in a specific category"""
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    # Build the emails text for this category
    category_text = ""
    for i, email_content in enumerate(emails_in_category, 1):
        email_section = f"""
--- Email {i} ---
Subject: {email_content['subject']}
From: {email_content['sender']}
Content: {email_content['body'][:2500]}
"""
        category_text += email_section
    
    category_display_names = {
        'social_events': 'Social Events',
        'culture_arts': 'Culture & Arts Events', 
        'professional_tech': 'Professional & Tech Events',
        'fashion': 'Fashion Events',
        'other': 'Other'
    }
    
    display_name = category_display_names.get(category_name, category_name.title())
    
    prompt = f"""Please create a comprehensive summary for these {display_name.lower()} newsletters/emails. 

Focus on:
- Event names, dates, and times (be specific about dates/times when mentioned)
- Locations and venues
- Key highlights or featured content
- Important links or registration information
- Any deadlines or time-sensitive information

Format your response as a single cohesive summary that someone could use to decide which events to attend. Please just include the event-level information as described. No need to add an overall explanation. 

{category_text}"""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Error summarizing {category_name} emails: {e}"

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
    
    # Extract content and categorize emails
    print("Categorizing emails by topic...")
    categories = {}
    email_ids = []  # Track email IDs for marking as read
    
    for i, email in enumerate(emails, 1):
        print(f"Processing email {i}/{len(emails)}...")
        content = extract_email_content(email)
        
        # Track email ID
        email_ids.append(content['id'])
        
        # Categorize the email
        category = categorize_email(content)
        
        # Add to category group
        if category not in categories:
            categories[category] = []
        categories[category].append(content)
    
    # Display categorization results
    print(f"\nEmails categorized:")
    for category, emails_in_cat in categories.items():
        print(f"  {category}: {len(emails_in_cat)} emails")
    
    # Generate summaries for each category
    print("\nGenerating category summaries...")
    
    category_display_names = {
        'social_events': 'Social Events',
        'culture_arts': 'Culture & Arts Events', 
        'professional_tech': 'Professional & Tech Events',
        'fashion': 'Fashion Events',
        'other': 'Other'
    }
    
    for category, emails_in_cat in categories.items():
        if not emails_in_cat:  # Skip empty categories
            continue
            
        display_name = category_display_names.get(category, category.title())
        
        print(f"\n" + "="*80)
        print(f"{display_name.upper()} SUMMARY ({len(emails_in_cat)} emails)")
        print("="*80)
        
        summary = summarize_category(category, emails_in_cat)
        print(summary)
        print("="*80)
    
    # Mark all processed emails as read
    print(f"\nMarking {len(email_ids)} processed emails as read...")
    mark_emails_as_read(service, email_ids)

if __name__ == "__main__":
    main()