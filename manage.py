#!/usr/bin/env python
import os
import sys
import click
from flask.cli import FlaskGroup
from app import create_app
from app.core.database.session import db

app = create_app()
cli = FlaskGroup(create_app=create_app)

@cli.command("create_admin")
def create_admin():
    """Create admin user"""
    from app.modules.auth.service import AuthService
    from app.core.security.encryption import EncryptionService
    
    email = input("Email: ")
    password = input("Password: ")
    phone = input("Phone: ")
    
    auth_service = AuthService()
    user = auth_service.create_super_admin(email, password, phone)
    print(f"Admin user created: {user.email}")

@cli.command("seed_data")
def seed_data():
    """Seed initial data"""
    from scripts.seed_data import seed_all
    seed_all()
    print("Data seeded successfully")

@cli.command("send-test-sms")
@click.argument("phone")
@click.option("--message", default="Bhatek ISP test message.", help="Body to send.")
def send_test_sms(phone, message):
    """Send one SMS through the configured provider, for verification."""
    from app.integrations.sms import send_sms
    from app.integrations.sms.provider import SMSService
    from flask import current_app

    with app.app_context():
        provider_name = (current_app.config.get("SMS_PROVIDER") or "brevo").lower()
        result = send_sms("cli-test", phone, message)
        if result.get("success"):
            print(f"OK  provider={result.get('provider', provider_name)} "
                  f"to={result.get('phone')} message_id={result.get('message_id')}")
        else:
            print(f"FAILED provider={provider_name}: {result.get('error')}")
            raise SystemExit(1)


if __name__ == '__main__':
    cli()
