import os
import base64
import re
import json
from datetime import datetime
from typing import List, Dict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
import anthropic
from dotenv import load_dotenv
import markdown

# Load environment variables
load_dotenv()

# Gmail scope - read, modify, and send Gmail messages
SCOPES = ['https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/gmail.send']

def get_gmail_service():
    """Authenticate and return Gmail service"""
    creds = None
    
    # Try to load from environment variable first (for cloud deployment)
    token_json = os.getenv('GOOGLE_TOKEN_JSON')
    if token_json:
        try:
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error parsing GOOGLE_TOKEN_JSON environment variable: {e}")
    
    # Fallback to local file (for development)
    if not creds and os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If no valid credentials found, fail
    if not creds:
        raise Exception("No valid credentials available. Set GOOGLE_TOKEN_JSON environment variable or provide token.json file.")
    
    # Handle token refresh
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                print("Refreshing expired token...")
                creds.refresh(Request())
                print("Token refreshed successfully.")
                
                # Update local token.json if it exists (for development)
                if os.path.exists('token.json'):
                    with open('token.json', 'w') as token:
                        token.write(creds.to_json())
                        
            except RefreshError as e:
                print(f"Token refresh failed: {e}")
                
                # If we're in a cloud environment, can't do interactive auth
                if os.getenv('RENDER') or os.getenv('DEPLOYMENT'):
                    raise Exception(f"Token refresh failed: {e}. Manual re-authentication required.")
                
                # Otherwise, fall through to interactive auth below
                print("Attempting interactive re-authentication...")
                creds = None  # Force interactive auth
        
        # If we still don't have valid creds, try interactive auth
        if not creds or not creds.valid:
            # No refresh token available or refresh failed - need interactive auth
            if os.getenv('RENDER') or os.getenv('DEPLOYMENT'):
                # In cloud environment, can't do interactive auth
                raise Exception("No valid credentials and running in headless environment. Manual re-authentication required.")
            else:
                # Local development - run interactive OAuth flow
                print("Running interactive OAuth flow...")
                credentials_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
                if credentials_json:
                    credentials_data = json.loads(credentials_json)
                    flow = InstalledAppFlow.from_client_config(credentials_data, SCOPES)
                else:
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
5. individual_recruitment - Job recruitment emails from recruiters, hiring managers, or individuals at companies. This includes:
   - Job opportunity emails from specific companies
   - Interview invitations or scheduling
   - Recruiter outreach about specific roles
   - "We're hiring" messages for particular positions
   - Follow-up emails about job applications
   - Coffee chat requests from recruiters or hiring managers
   - Any email primarily focused on recruiting you for a specific job/role
6. job_postings - Automated job notifications and job board summaries. This includes:
   - Job alerts from LinkedIn, Indeed, Glassdoor, etc.
   - Weekly/daily job digest emails
   - Job board notifications with multiple job listings
   - Automated "jobs matching your criteria" emails
   - Job recommendation emails from job sites
   - Any email that's primarily a list or summary of multiple job openings
7. other - Anything that doesn't clearly fit the above categories

Email:
Subject: {email_content['subject']}
From: {email_content['sender']}
Content: {email_content['body'][:1000]}

Respond with ONLY the category name (e.g., "social_events" or "job_postings")."""

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        category = response.content[0].text.strip().lower()
        
        # Validate category
        valid_categories = ['social_events', 'culture_arts', 'professional_tech', 'fashion', 'individual_recruitment', 'job_postings', 'other']
        if category in valid_categories:
            return category
        else:
            return 'other'
    except Exception as e:
        print(f"Error categorizing email: {e}")
        return 'other'

def send_summary_email(service, summary_content: str, total_emails: int):
    """Send the newsletter summary via email"""
    sender_email = "me"  # Use authenticated Gmail account
    recipient_email = "matthewcline1028@gmail.com"
    
    # Create message
    message = MIMEMultipart()
    message['to'] = recipient_email
    message['from'] = sender_email
    message['subject'] = f"Newsletter Summary - {datetime.now().strftime('%Y-%m-%d')} ({total_emails} emails)"
    
    # Add body with markdown formatting
    markdown_body = f"""# Newsletter Summary
**{datetime.now().strftime('%B %d, %Y')}**

Processed **{total_emails} unread emails** and organized them by category:

{summary_content}

---
*Generated automatically by Newsletter Summarizer*
"""
    
    # Convert markdown to HTML
    html_body = markdown.markdown(markdown_body)
    
    message.attach(MIMEText(html_body, 'html'))
    
    # Encode message
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
    
    try:
        # Send email
        service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        print(f"Summary email sent successfully to {recipient_email}")
        
    except Exception as e:
        print(f"Error sending summary email: {e}")

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
        'individual_recruitment': 'Recruiter',
        'job_postings': 'Job Postings',
        'other': 'Other'
    }
    
    display_name = category_display_names.get(category_name, category_name.title())
    
    # Create different prompts based on category type
    if category_name == 'individual_recruitment':
        prompt = f"""Please create a comprehensive summary for these recruitment/job opportunity emails using markdown formatting.

Focus on:
- Company names and recruiting contact information
- Job titles and roles being offered
- Key requirements or qualifications mentioned
- Application deadlines or next steps
- Interview requests or meeting opportunities
- Salary ranges if mentioned
- Location (remote/on-site/hybrid) and office locations

Format your response using markdown with:
- **Bold** for company names and job titles
- Bullet points for requirements and key details
- Clear organization by opportunity
- Concise, scannable format

{category_text}"""
    elif category_name == 'job_postings':
        prompt = f"""Please create a comprehensive summary for these job posting/job board notification emails using markdown formatting.

Focus on:
- Job titles and company names
- Key requirements, skills, or qualifications mentioned
- Salary ranges or compensation details if provided
- Work location details (remote/on-site/hybrid/city)
- Job types (full-time, part-time, contract, internship)
- Application deadlines or posting dates
- Notable benefits or perks mentioned
- Industry or job category trends you notice

Format your response using markdown with:
- **Bold** for job titles and company names
- Bullet points for requirements and key details
- Group similar roles together
- Highlight high-priority opportunities
- Use clear, scannable formatting

{category_text}"""
    else:
        prompt = f"""Please create a comprehensive summary for these {display_name.lower()} newsletters/emails using markdown formatting.

Focus on:
- Event names, dates, and times (be specific about dates/times when mentioned)
- Locations and venues
- Key highlights or featured content
- Important links or registration information
- Any deadlines or time-sensitive information

Format your response using markdown with:
- **Bold** for event names and key dates
- Bullet points for event details
- Clear organization by event or theme
- Highlight time-sensitive information
- Use scannable formatting for quick decision-making

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
        print(f"  → Categorized as: {category}")
        
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
        'individual_recruitment': 'Recruiter Outreach',
        'job_postings': 'Job Postings',
        'other': 'Other'
    }
    
    # Build complete summary content for email
    summary_content = ""
    
    for category, emails_in_cat in categories.items():
        if not emails_in_cat:  # Skip empty categories
            continue
            
        display_name = category_display_names.get(category, category.title())
        print(f"Generating {display_name.lower()} summary...")
        
        summary = summarize_category(category, emails_in_cat)
        
        # Add to email content with markdown formatting
        summary_content += f"\n## {display_name}\n"
        summary_content += f"*{len(emails_in_cat)} emails*\n\n"
        summary_content += summary + "\n"
    
    # Send summary email
    if summary_content.strip():
        print("\nSending summary email...")
        send_summary_email(service, summary_content, len(emails))
    else:
        print("No summaries generated - no email sent.")
    
    # Mark all processed emails as read
    print(f"\nMarking {len(email_ids)} processed emails as read...")
    mark_emails_as_read(service, email_ids)

if __name__ == "__main__":
    main()