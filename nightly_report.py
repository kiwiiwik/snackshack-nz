#!/usr/bin/env python3
"""
Standalone nightly report script.

Run this on a schedule (cron, Azure WebJob, Azure Timer Function, etc.)
to email the daily report to all super admins.

Usage:
    python nightly_report.py

Requires the same environment variables as the main app (DB_*, SMTP_*).
"""
import sys
import os

# Ensure the app directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from routes import send_nightly_report

if __name__ == '__main__':
    success = send_nightly_report(app)
    if success:
        print("Nightly report sent successfully.")
    else:
        print("Failed to send nightly report. Check SMTP config and super admin emails.")
        sys.exit(1)
