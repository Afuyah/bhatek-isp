#!/usr/bin/env python
import os
import sys
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

if __name__ == '__main__':
    cli()