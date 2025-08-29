#!/usr/bin/env python3
"""
Simple e‑invite web application.

This WSGI application implements a very small guestbook and RSVP system
that resembles the basic functionality of an online invitation platform.
Guests can register, log in, view an event invitation, respond to the
RSVP, and leave comments. All state is stored in a local SQLite database.

The app uses only the Python standard library (plus Jinja2 for
templating) to remain installable without internet access. A small
session manager keeps track of logged‑in users via cookies. When
deployed to AWS (for example on an EC2 instance or via Elastic
Beanstalk), you would typically replace the SQLite backend with a more
durable store (like Amazon RDS or DynamoDB), and the in‑memory session
store with a distributed solution (like ElastiCache). Authentication
could also be offloaded to Amazon Cognito.

To run the app locally, execute this file directly:

    python invite_app.py

and then visit http://localhost:8000 in your browser.
"""

import os
import sqlite3
import urllib.parse
import http.cookies
import hashlib
import secrets
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults
from wsgiref.headers import Headers

import jinja2

# -------------------------------------------------------------------
# Configuration

# Determine the directory of this script so we can locate templates and
# static files relative to it. This makes the app portable when copied
# to different environments.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DB_PATH = os.path.join(BASE_DIR, 'database.db')

# Email configuration (you can customize these)
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')  # Your email
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')  # Your app password
SMTP_FROM_NAME = os.environ.get('SMTP_FROM_NAME', 'Event Host')
SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', SMTP_USERNAME)

# Create a Jinja2 environment. Autoescaping ensures that variables
# inserted into templates are HTML‑escaped unless explicitly marked
# safe, which protects against injection attacks.
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
    autoescape=True
)

# In‑memory session store. This dict maps a random session ID to a
# user ID. Because it is not persisted, sessions will be lost when
# the process restarts. Replace with a persistent session mechanism
# (e.g. Redis) in production.
sessions: dict[str, int] = {}

# -------------------------------------------------------------------
# Database initialization

def init_db() -> None:
    """Create the database and default records if they do not exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Create tables
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin BOOLEAN DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        host TEXT,
        datetime TEXT,
        location TEXT,
        registry1 TEXT,
        registry2 TEXT,
        header_image TEXT,
        card_theme TEXT DEFAULT 'ocean'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        user_id INTEGER,
        rsvp TEXT,
        adults_qty INTEGER DEFAULT 1,
        kids_qty INTEGER DEFAULT 0,
        guest_name TEXT,
        guest_email TEXT,
        is_anonymous BOOLEAN DEFAULT 0,
        FOREIGN KEY (event_id) REFERENCES events(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        comment TEXT NOT NULL,
        timestamp REAL NOT NULL,
        FOREIGN KEY (event_id) REFERENCES events(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    conn.commit()
    
    # Add new columns to existing tables if they don't exist (migration)
    try:
        c.execute('ALTER TABLE invites ADD COLUMN adults_qty INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        c.execute('ALTER TABLE invites ADD COLUMN kids_qty INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        c.execute('ALTER TABLE invites ADD COLUMN guest_name TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        c.execute('ALTER TABLE invites ADD COLUMN guest_email TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        c.execute('ALTER TABLE invites ADD COLUMN is_anonymous BOOLEAN DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Make user_id nullable for anonymous RSVPs
    try:
        # SQLite doesn't support ALTER COLUMN directly, so we need to recreate the table
        c.execute('''CREATE TABLE invites_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_id INTEGER,
            rsvp TEXT,
            adults_qty INTEGER DEFAULT 1,
            kids_qty INTEGER DEFAULT 0,
            guest_name TEXT,
            guest_email TEXT,
            is_anonymous BOOLEAN DEFAULT 0,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')
        
        # Copy existing data
        c.execute('''INSERT INTO invites_new (id, event_id, user_id, rsvp, adults_qty, kids_qty, guest_name, guest_email, is_anonymous)
                     SELECT id, event_id, user_id, rsvp, 
                            COALESCE(adults_qty, 1), COALESCE(kids_qty, 0), 
                            guest_name, guest_email, COALESCE(is_anonymous, 0)
                     FROM invites''')
        
        # Drop old table and rename new one
        c.execute('DROP TABLE invites')
        c.execute('ALTER TABLE invites_new RENAME TO invites')
        
    except sqlite3.OperationalError as e:
        # Table might already be migrated or migration failed
        print(f"Migration note: {e}")
        pass
    
    conn.commit()
    
    # Create a default event if none exist. This initial invitation can be
    # customised later via database edits or by extending the application.
    c.execute('SELECT COUNT(*) FROM events')
    count = c.fetchone()[0]
    if count == 0:
        c.execute('''INSERT INTO events (
            title, description, host, datetime, location,
            registry1, registry2, header_image
        ) VALUES (?,?,?,?,?,?,?,?)''', (
            'A little pearl is on the way',
            'Celebrate with us over love, laughter, and lunch as we await our baby\'s arrival.',
            'Rohan',
            '2025-10-04T11:00:00',
            'Beaver Lake Park - Lodge, 25101 SE 24th St, Sammamish, WA 98075',
            'https://www.babylist.com',
            'https://www.amazon.com',
            'header.png'
        ))
        conn.commit()
    conn.close()

# -------------------------------------------------------------------
# Utility functions

def get_user_from_session(environ) -> int | None:
    """Return the user ID associated with the session cookie, if any."""
    cookie_header = environ.get('HTTP_COOKIE', '')
    cookies = http.cookies.SimpleCookie(cookie_header)
    session_cookie = cookies.get('session_id')
    if session_cookie:
        sid = session_cookie.value
        return sessions.get(sid)
    return None

def hash_password(password: str) -> str:
    """Return a hexadecimal SHA‑256 hash of the given password string."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def parse_post(environ) -> dict[str, str]:
    """Parse URL‑encoded POST data from the request body into a dict."""
    try:
        size = int(environ.get('CONTENT_LENGTH', 0) or 0)
    except (ValueError, TypeError):
        size = 0
    body = environ['wsgi.input'].read(size).decode('utf-8')
    params = urllib.parse.parse_qs(body)
    # Flatten values: keep only the first value for each key
    return {k: v[0] for k, v in params.items()}

# -------------------------------------------------------------------
# Email functions

def send_email(to_email: str, to_name: str, subject: str, body: str) -> bool:
    """Send an email notification. Returns True if successful, False otherwise."""
    if not SMTP_USERNAME or not SMTP_PASSWORD or not to_email:
        print(f"Email not sent: Missing configuration or recipient email")
        return False
    
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
        msg['To'] = formataddr((to_name, to_email))
        msg['Subject'] = subject
        
        # Add HTML body
        html_part = MIMEText(body, 'html')
        msg.attach(html_part)
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        
        print(f"Email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False


def send_rsvp_confirmation_emails(event_id: int, guest_name: str, guest_email: str, 
                                 rsvp: str, adults_qty: int, kids_qty: int, is_anonymous: bool = False):
    """Send RSVP confirmation emails to both guest and host."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get event details
    c.execute('''SELECT title, host, datetime, location FROM events WHERE id=?''', (event_id,))
    event = c.fetchone()
    if not event:
        conn.close()
        return
    
    event_title, host_name, event_datetime, location = event
    
    # Get host email (admin user)
    c.execute('''SELECT email FROM users WHERE is_admin=1 LIMIT 1''')
    host_result = c.fetchone()
    host_email = host_result[0] if host_result else None
    
    conn.close()
    
    # Format event date/time for display
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(event_datetime)
        date_display = dt.strftime('%A, %B %-d, %Y')
        time_display = dt.strftime('%-I:%M %p')
    except:
        date_display = event_datetime
        time_display = ""
    
    # Determine RSVP status text
    if rsvp == 'yes':
        status_text = 'attending'
        status_emoji = '✅'
        guest_message = "We're excited to see you there!"
        host_message = f"{guest_name} will be attending your event!"
    else:
        status_text = 'not attending' 
        status_emoji = '❌'
        guest_message = "Thanks for letting us know. You'll be missed!"
        host_message = f"{guest_name} is unable to attend your event."
    
    # Guest confirmation email
    if guest_email:
        guest_subject = f"RSVP Confirmation: {event_title}"
        guest_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Georgia', serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .event-details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea; }}
                .status {{ font-size: 18px; font-weight: bold; color: {'#28a745' if rsvp == 'yes' else '#dc3545'}; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>{status_emoji} RSVP Confirmed</h1>
            </div>
            <div class="content">
                <p>Hi {guest_name},</p>
                <p>This confirms your RSVP for:</p>
                
                <div class="event-details">
                    <h3>{event_title}</h3>
                    <p><strong>Host:</strong> {host_name}</p>
                    <p><strong>Date:</strong> {date_display}</p>
                    <p><strong>Time:</strong> {time_display}</p>
                    <p><strong>Location:</strong> {location}</p>
                </div>
                
                <p class="status">Your RSVP: {status_text.title()} {status_emoji}</p>
                
                {f'<p><strong>Party size:</strong> {adults_qty} adult(s), {kids_qty} kid(s)</p>' if rsvp == 'yes' else ''}
                
                <p>{guest_message}</p>
                
                <p>Best regards,<br>{host_name}</p>
            </div>
        </body>
        </html>
        """
        
        send_email(guest_email, guest_name, guest_subject, guest_body)
    
    # Host notification email  
    if host_email:
        host_subject = f"New RSVP: {guest_name} - {event_title}"
        host_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Georgia', serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .guest-details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #28a745; }}
                .status {{ font-size: 18px; font-weight: bold; color: {'#28a745' if rsvp == 'yes' else '#dc3545'}; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>{status_emoji} New RSVP Received</h1>
            </div>
            <div class="content">
                <p>Hi {host_name},</p>
                <p>{host_message}</p>
                
                <div class="guest-details">
                    <h3>RSVP Details</h3>
                    <p><strong>Guest:</strong> {guest_name}</p>
                    <p><strong>Email:</strong> {guest_email or 'Not provided'}</p>
                    <p class="status">Status: {status_text.title()} {status_emoji}</p>
                    {f'<p><strong>Party size:</strong> {adults_qty} adult(s), {kids_qty} kid(s)</p>' if rsvp == 'yes' else ''}
                    <p><strong>RSVP Type:</strong> {'Anonymous' if is_anonymous else 'Account-based'}</p>
                </div>
                
                <p>You can view all RSVPs in your <a href="http://localhost:8000/admin/event/{event_id}">admin panel</a>.</p>
                
                <p>Event: {event_title}<br>
                Date: {date_display} at {time_display}</p>
            </div>
        </body>
        </html>
        """
        
        send_email(host_email, host_name, host_subject, host_body)


# -------------------------------------------------------------------
# WSGI application

def application(environ, start_response):
    """Handle an incoming HTTP request."""
    setup_testing_defaults(environ)
    path = environ.get('PATH_INFO', '')
    method = environ.get('REQUEST_METHOD', 'GET').upper()

    # Serve static files under /static/ from the static directory.
    if path.startswith('/static/'):
        # Construct the file path relative to the application root.
        rel_path = path.lstrip('/')
        file_path = os.path.join(BASE_DIR, rel_path)
        if os.path.isfile(file_path):
            # Determine simple MIME type based on extension.
            ext = os.path.splitext(file_path)[1].lower()
            mime_types = {
                '.css': 'text/css',
                '.js': 'application/javascript',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml'
            }
            content_type = mime_types.get(ext, 'application/octet-stream')
            with open(file_path, 'rb') as f:
                data = f.read()
            headers = [('Content-Type', content_type), ('Content-Length', str(len(data)))]
            start_response('200 OK', headers)
            return [data]
        else:
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Not Found']

    # Routing for dynamic endpoints
    if path == '/':
        # Redirect directly to the main event page
        start_response('302 Found', [('Location', '/event/1')])
        return [b'']

    if path == '/register':
        if method == 'GET':
            template = env.get_template('register.html')
            body = template.render(title='Register', error=None)
            start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
            return [body.encode('utf-8')]
        elif method == 'POST':
            params = parse_post(environ)
            name = params.get('name', '').strip()
            email = params.get('email', '').strip()
            password = params.get('password', '')
            if not (name and email and password):
                template = env.get_template('register.html')
                body = template.render(title='Register', error='Please fill all fields.')
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                # Check if this is the first user (make them admin)
                c.execute('SELECT COUNT(*) FROM users')
                user_count = c.fetchone()[0]
                is_first_user = user_count == 0
                
                c.execute('INSERT INTO users (name, email, password_hash, is_admin) VALUES (?,?,?,?)',
                          (name, email, hash_password(password), is_first_user))
                user_id = c.lastrowid
                # For demonstration, automatically invite the new user to the default event (ID=1)
                c.execute('INSERT INTO invites (event_id, user_id, rsvp) VALUES (?,?,?)',
                          (1, user_id, None))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close()
                template = env.get_template('register.html')
                body = template.render(title='Register', error='Email already registered.')
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]
            conn.close()
            # Auto‑login after successful registration
            session_id = secrets.token_hex(16)
            sessions[session_id] = user_id
            cookie = http.cookies.SimpleCookie()
            cookie['session_id'] = session_id
            cookie['session_id']['path'] = '/'
            headers = Headers([('Location', '/'), ('Set-Cookie', cookie.output(header=''))])
            start_response('302 Found', headers.items())
            return [b'']

    if path == '/login':
        if method == 'GET':
            template = env.get_template('login.html')
            body = template.render(title='Login', error=None)
            start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
            return [body.encode('utf-8')]
        elif method == 'POST':
            params = parse_post(environ)
            email = params.get('email', '').strip()
            password = params.get('password', '')
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT id, password_hash FROM users WHERE email=?', (email,))
            row = c.fetchone()
            conn.close()
            if row and hash_password(password) == row[1]:
                user_id = row[0]
                session_id = secrets.token_hex(16)
                sessions[session_id] = user_id
                cookie = http.cookies.SimpleCookie()
                cookie['session_id'] = session_id
                cookie['session_id']['path'] = '/'
                headers = Headers([('Location', '/'), ('Set-Cookie', cookie.output(header=''))])
                start_response('302 Found', headers.items())
                return [b'']
            else:
                template = env.get_template('login.html')
                body = template.render(title='Login', error='Invalid email or password.')
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]

    if path == '/logout':
        # Invalidate the session cookie
        cookies = http.cookies.SimpleCookie(environ.get('HTTP_COOKIE', ''))
        session_cookie = cookies.get('session_id')
        headers = Headers([('Location', '/login')])
        if session_cookie:
            sid = session_cookie.value
            sessions.pop(sid, None)
            expired_cookie = http.cookies.SimpleCookie()
            expired_cookie['session_id'] = ''
            expired_cookie['session_id']['path'] = '/'
            expired_cookie['session_id']['expires'] = 'Thu, 01 Jan 1970 00:00:00 GMT'
            headers.add_header('Set-Cookie', expired_cookie.output(header=''))
        start_response('302 Found', headers.items())
        return [b'']

    if path.startswith('/event/'):
        segments = path.strip('/').split('/')
        if len(segments) == 2:
            try:
                event_id = int(segments[1])
            except ValueError:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Event not found']
            # Check if user is logged in (but don't require it)
            user_id = get_user_from_session(environ)
            if method == 'GET':
                # Fetch event details
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('''SELECT title, description, host, datetime, location,
                             registry1, registry2, header_image FROM events WHERE id=?''',
                          (event_id,))
                event = c.fetchone()
                if not event:
                    conn.close()
                    start_response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b'Event not found']
                title, description, host, datetime_str, location, reg1, reg2, header_image = event
                
                # Check for anonymous RSVP success message
                query_string = environ.get('QUERY_STRING', '')
                rsvp_success = None
                if 'rsvp_success=' in query_string:
                    try:
                        rsvp_success = urllib.parse.parse_qs(query_string)['rsvp_success'][0]
                    except:
                        pass
                
                # Handle logged-in users
                if user_id:
                    # Fetch user name and admin status
                    c.execute('SELECT name, is_admin FROM users WHERE id=?', (user_id,))
                    user_row = c.fetchone()
                    user_name = user_row[0] if user_row else 'Guest'
                    is_admin = user_row[1] if user_row else False
                    # Fetch RSVP status and quantities
                    c.execute('SELECT rsvp, adults_qty, kids_qty FROM invites WHERE event_id=? AND user_id=?',
                              (event_id, user_id))
                    rsvp_row = c.fetchone()
                    rsvp_status = rsvp_row[0] if rsvp_row else None
                    adults_qty = rsvp_row[1] if rsvp_row else 1
                    kids_qty = rsvp_row[2] if rsvp_row else 0
                else:
                    # Anonymous user viewing the event
                    user_name = 'Guest'
                    is_admin = False
                    rsvp_status = rsvp_success  # Show success message if they just completed RSVP
                    adults_qty = 1
                    kids_qty = 0
                # Fetch comments
                c.execute('''SELECT comments.comment, users.name, comments.timestamp
                             FROM comments JOIN users ON comments.user_id = users.id
                             WHERE comments.event_id=? ORDER BY comments.timestamp ASC''',
                          (event_id,))
                comments = c.fetchall()
                conn.close()
                # Format date/time for display (timezone naive)
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(datetime_str)
                    date_display = dt.strftime('%A, %B %-d, %Y')
                    time_display = dt.strftime('%-I:%M %p')
                except Exception:
                    date_display = datetime_str
                    time_display = ''
                template = env.get_template('event.html')
                body = template.render(
                    title=title,
                    description=description,
                    host=host,
                    date_display=date_display,
                    time_display=time_display,
                    location=location,
                    registry1=reg1,
                    registry2=reg2,
                    header_image=header_image,
                    user_name=user_name,
                    rsvp_status=rsvp_status,
                    comments=comments,
                    event_id=event_id,
                    is_admin=is_admin,
                    adults_qty=adults_qty,
                    kids_qty=kids_qty
                )
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]
            elif method == 'POST':
                # Handle RSVP or comment submission
                params = parse_post(environ)
                action = params.get('action', '')
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                if action == 'rsvp':
                    response = params.get('response', '')
                    if response in ('yes', 'no'):
                        if response == 'yes':
                            # Get quantities for attending guests
                            adults_qty = int(params.get('adults_qty', 1))
                            kids_qty = int(params.get('kids_qty', 0))
                            c.execute('UPDATE invites SET rsvp=?, adults_qty=?, kids_qty=? WHERE event_id=? AND user_id=?',
                                      (response, adults_qty, kids_qty, event_id, user_id))
                        else:
                            # For "no" response, clear quantities
                            adults_qty = 1
                            kids_qty = 0
                            c.execute('UPDATE invites SET rsvp=?, adults_qty=1, kids_qty=0 WHERE event_id=? AND user_id=?',
                                      (response, event_id, user_id))
                        conn.commit()
                        
                        # Get user details for email confirmation
                        c.execute('SELECT name, email FROM users WHERE id=?', (user_id,))
                        user_result = c.fetchone()
                        if user_result:
                            guest_name, guest_email = user_result
                            # Send confirmation emails
                            send_rsvp_confirmation_emails(event_id, guest_name, guest_email, response, adults_qty, kids_qty, is_anonymous=False)
                elif action == 'comment':
                    comment_text = params.get('comment', '').strip()
                    if comment_text:
                        c.execute('INSERT INTO comments (event_id, user_id, comment, timestamp) VALUES (?,?,?,?)',
                                  (event_id, user_id, comment_text, time.time()))
                        conn.commit()
                conn.close()
                start_response('302 Found', [('Location', f'/event/{event_id}')])
                return [b'']

        # If the path format does not match /event/<id>
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'Event not found']

    if path.startswith('/admin/event/'):
        segments = path.strip('/').split('/')
        if len(segments) == 3:
            try:
                event_id = int(segments[2])
            except ValueError:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Event not found']
            
            # Ensure user is logged in and is admin
            user_id = get_user_from_session(environ)
            if not user_id:
                start_response('302 Found', [('Location', '/login')])
                return [b'']
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT is_admin FROM users WHERE id=?', (user_id,))
            user_row = c.fetchone()
            if not user_row or not user_row[0]:
                conn.close()
                start_response('403 Forbidden', [('Content-Type', 'text/plain')])
                return [b'Admin access required']
            
            if method == 'GET':
                # Fetch event details for editing
                c.execute('''SELECT id, title, description, host, datetime, location,
                             registry1, registry2, header_image, card_theme FROM events WHERE id=?''',
                          (event_id,))
                event_row = c.fetchone()
                if not event_row:
                    conn.close()
                    start_response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b'Event not found']
                
                # Convert to dict for template
                event = {
                    'id': event_row[0],
                    'title': event_row[1],
                    'description': event_row[2] or '',
                    'host': event_row[3] or '',
                    'datetime': event_row[4] or '',
                    'location': event_row[5] or '',
                    'registry1': event_row[6] or '',
                    'registry2': event_row[7] or '',
                    'header_image': event_row[8] or '',
                    'card_theme': event_row[9] or 'ocean'
                }
                
                # Fetch RSVP statistics and guest list (both registered and anonymous)
                c.execute('''SELECT 
                             COALESCE(u.name, i.guest_name) as name,
                             COALESCE(u.email, i.guest_email) as email,
                             i.rsvp, i.adults_qty, i.kids_qty, i.is_anonymous
                             FROM invites i
                             LEFT JOIN users u ON u.id = i.user_id 
                             WHERE i.event_id = ? 
                             ORDER BY name''', (event_id,))
                guest_rows = c.fetchall()
                conn.close()
                
                guests = []
                attending = 0
                not_attending = 0
                no_response = 0
                total_adults = 0
                total_kids = 0
                
                for name, email, rsvp, adults_qty, kids_qty, is_anonymous in guest_rows:
                    guests.append({
                        'name': name or 'Anonymous',
                        'email': email or '',
                        'rsvp': rsvp,
                        'adults_qty': adults_qty or 1,
                        'kids_qty': kids_qty or 0,
                        'is_anonymous': is_anonymous
                    })
                    
                    if rsvp == 'yes':
                        attending += 1
                        total_adults += adults_qty or 1
                        total_kids += kids_qty or 0
                    elif rsvp == 'no':
                        not_attending += 1
                    else:
                        no_response += 1
                
                rsvp_stats = {
                    'attending': attending,
                    'not_attending': not_attending,
                    'no_response': no_response,
                    'total_adults': total_adults,
                    'total_kids': total_kids
                }
                
                template = env.get_template('admin.html')
                body = template.render(
                    title='Admin - Edit Event', 
                    event=event, 
                    event_id=event_id,
                    guests=guests,
                    rsvp_stats=rsvp_stats
                )
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]
                
            elif method == 'POST':
                # Handle event update
                params = parse_post(environ)
                c.execute('''UPDATE events SET 
                             title=?, description=?, host=?, datetime=?, location=?,
                             registry1=?, registry2=?, card_theme=? WHERE id=?''',
                          (params.get('title', ''),
                           params.get('description', ''),
                           params.get('host', ''),
                           params.get('datetime', ''),
                           params.get('location', ''),
                           params.get('registry1', ''),
                           params.get('registry2', ''),
                           params.get('card_theme', 'ocean'),
                           event_id))
                conn.commit()
                conn.close()
                start_response('302 Found', [('Location', f'/event/{event_id}')])
                return [b'']
        
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'Admin page not found']

    if path.startswith('/anonymous-rsvp/'):
        segments = path.strip('/').split('/')
        if len(segments) == 2:
            try:
                event_id = int(segments[1])
            except ValueError:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Event not found']
            
            if method == 'GET':
                # Show anonymous RSVP form
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('''SELECT title, description, host, datetime, location,
                             registry1, registry2, header_image FROM events WHERE id=?''',
                          (event_id,))
                event = c.fetchone()
                conn.close()
                
                if not event:
                    start_response('404 Not Found', [('Content-Type', 'text/plain')])
                    return [b'Event not found']
                
                title, description, host, datetime_str, location, reg1, reg2, header_image = event
                
                # Format date/time for display
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(datetime_str)
                    date_display = dt.strftime('%A, %B %-d, %Y')
                    time_display = dt.strftime('%-I:%M %p')
                except Exception:
                    date_display = datetime_str
                    time_display = ''
                
                template = env.get_template('anonymous_rsvp.html')
                body = template.render(
                    title=title,
                    description=description,
                    host=host,
                    date_display=date_display,
                    time_display=time_display,
                    location=location,
                    registry1=reg1,
                    registry2=reg2,
                    header_image=header_image,
                    event_id=event_id,
                    error=None
                )
                start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                return [body.encode('utf-8')]
                
            elif method == 'POST':
                # Handle anonymous RSVP submission
                params = parse_post(environ)
                guest_name = params.get('guest_name', '').strip()
                guest_email = params.get('guest_email', '').strip()
                rsvp = params.get('rsvp', '')
                adults_qty = int(params.get('adults_qty', 1)) if params.get('adults_qty') else 1
                kids_qty = int(params.get('kids_qty', 0)) if params.get('kids_qty') else 0
                
                if not guest_name or rsvp not in ('yes', 'no'):
                    # Show form with error
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute('''SELECT title, description, host, datetime, location,
                                 registry1, registry2, header_image FROM events WHERE id=?''',
                              (event_id,))
                    event = c.fetchone()
                    conn.close()
                    
                    if event:
                        title, description, host, datetime_str, location, reg1, reg2, header_image = event
                        
                        # Format date/time
                        import datetime
                        try:
                            dt = datetime.datetime.fromisoformat(datetime_str)
                            date_display = dt.strftime('%A, %B %-d, %Y')
                            time_display = dt.strftime('%-I:%M %p')
                        except Exception:
                            date_display = datetime_str
                            time_display = ''
                        
                        template = env.get_template('anonymous_rsvp.html')
                        body = template.render(
                            title=title,
                            description=description,
                            host=host,
                            date_display=date_display,
                            time_display=time_display,
                            location=location,
                            registry1=reg1,
                            registry2=reg2,
                            header_image=header_image,
                            event_id=event_id,
                            error='Please fill in your name and select an RSVP option.'
                        )
                        start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
                        return [body.encode('utf-8')]
                
                # Save anonymous RSVP
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('''INSERT INTO invites (event_id, guest_name, guest_email, rsvp, adults_qty, kids_qty, is_anonymous)
                             VALUES (?, ?, ?, ?, ?, ?, 1)''',
                          (event_id, guest_name, guest_email, rsvp, adults_qty, kids_qty))
                conn.commit()
                conn.close()
                
                # Send confirmation emails
                send_rsvp_confirmation_emails(event_id, guest_name, guest_email, rsvp, adults_qty, kids_qty, is_anonymous=True)
                
                # Redirect back to event page with success message
                start_response('302 Found', [('Location', f'/event/{event_id}?rsvp_success={rsvp}')])
                return [b'']
        
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'Anonymous RSVP not found']

    if path.startswith('/rsvp-thanks/'):
        segments = path.strip('/').split('/')
        if len(segments) == 2:
            try:
                event_id = int(segments[1])
            except ValueError:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Event not found']
            
            # Simple thank you message
            thank_you_html = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Thank You</title>
                <link rel="preconnect" href="https://fonts.googleapis.com">
                <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
                <link rel="stylesheet" href="/static/css/style.css">
            </head>
            <body>
                <div class="container">
                    <div class="event-card">
                        <h2>Thank You!</h2>
                        <p class="description">Your RSVP has been received. We appreciate you letting us know!</p>
                        <p>We're looking forward to celebrating with you.</p>
                    </div>
                </div>
            </body>
            </html>
            '''
            start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
            return [thank_you_html.encode('utf-8')]

    # Unknown path
    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'Not Found']


# -------------------------------------------------------------------
# Main entry point

if __name__ == '__main__':
    # Ensure necessary directories exist
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(os.path.join(STATIC_DIR, 'css'), exist_ok=True)
    os.makedirs(os.path.join(STATIC_DIR, 'images'), exist_ok=True)
    # Initialise the database on startup
    init_db()
    # Determine port from environment or default to 8000
    port = int(os.environ.get('PORT', '8000'))
    with make_server('', port, application) as httpd:
        print(f"Serving on port {port}... (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Shutting down.")