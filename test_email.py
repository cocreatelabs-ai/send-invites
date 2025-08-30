#!/usr/bin/env python3
"""
Simple email test script to verify SMTP configuration
"""
import os
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

# Load environment variables
from dotenv import load_dotenv

load_dotenv()

# Email configuration
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Test Sender")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", SMTP_USERNAME)


def test_email():
    print("=== Email Configuration Test ===")
    print(f"SMTP Server: '{SMTP_SERVER}'")
    print(f"SMTP Port: {SMTP_PORT}")
    print(f"Username: '{SMTP_USERNAME}'")
    print(f"From Email: '{SMTP_FROM_EMAIL}'")
    print(f"From Name: '{SMTP_FROM_NAME}'")
    print(f"Password Set: {'Yes' if SMTP_PASSWORD else 'No'}")
    print()

    # Test DNS resolution
    print("Testing DNS resolution...")
    try:
        ip = socket.gethostbyname(SMTP_SERVER)
        print(f"✓ DNS resolution successful: {SMTP_SERVER} -> {ip}")
    except Exception as e:
        print(f"✗ DNS resolution failed: {e}")
        return False

    # Test SMTP connection
    print("\nTesting SMTP connection...")
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            print("✓ SMTP connection successful")
            server.starttls()
            print("✓ TLS started successfully")
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            print("✓ Authentication successful")

        print("\n🎉 All email tests passed!")
        return True

    except Exception as e:
        print(f"✗ SMTP test failed: {e}")
        return False


if __name__ == "__main__":
    test_email()
