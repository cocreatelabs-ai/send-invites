# Invite App

A simple e-invite web application for managing event invitations and RSVPs.

## Gmail Email Configuration

To enable email notifications for RSVPs, you'll need to set up Gmail with an App Password:

### Step 1: Enable 2-Factor Authentication
1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable "2-Step Verification" if not already enabled
3. This is required for App Passwords

### Step 2: Generate App Password
1. Go to [App Passwords](https://myaccount.google.com/apppasswords)
2. Select "Mail" and your device
3. Google will generate a 16-character password
4. **Save this password** - you won't see it again!

### Step 3: Update Your .env File
```bash
# Email configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-16-character-app-password
SMTP_FROM_NAME=Your Name
SMTP_FROM_EMAIL=your-email@gmail.com
```

### Step 4: Test Email Configuration
After updating your `.env` file, restart the app and try an RSVP to test email delivery.

### Troubleshooting
- **"Less secure app access"** is NOT needed when using App Passwords
- Use the 16-character App Password, NOT your regular Gmail password
- Make sure 2FA is enabled on your Google account
- Check spam folder if emails don't arrive

## Environment Configuration

### Local Development

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and update the BASE_URL if needed:
   ```
   BASE_URL=http://localhost:8000
   ```

3. Optional: Configure email settings in `.env` for RSVP notifications:
   ```
   SMTP_USERNAME=your-email@gmail.com
   SMTP_PASSWORD=your-app-password
   ```

### Railway Deployment

Set these environment variables in your Railway dashboard:

- `BASE_URL`: Your Railway app URL (e.g., `https://your-app-name.railway.app`)
- `SMTP_USERNAME`: Your email for notifications (optional)
- `SMTP_PASSWORD`: Your email app password (optional)
- `SMTP_FROM_NAME`: Display name for emails (optional)

## Running Locally

```bash
uv run python invite_app.py
```

The app will show the URL to visit in the console output.

## Features

- Event invitations with calendar integration
- Anonymous and account-based RSVPs
- Email notifications
- Admin panel for event management
- Social media preview images