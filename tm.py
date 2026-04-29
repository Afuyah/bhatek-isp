#!/usr/bin/env python
"""Script to create a super admin user"""

import sys
import os
import argparse
from getpass import getpass

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.core.database.session import db
from app.models import User
from app.core.logging.logger import logger


def create_super_admin(email=None, password=None, phone=None, first_name=None, last_name=None):
    """Create a super admin user (without organization)"""
    
    app = create_app()
    
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            print(f"\n⚠️  User with email {email} already exists!")
            return False
        
        # Get user input if not provided
        if not email:
            email = input("Email address: ")
        
        if not password:
            password = getpass("Password: ")
            confirm_password = getpass("Confirm password: ")
            if password != confirm_password:
                print("❌ Passwords do not match!")
                return False
        
        if not phone:
            phone = input("Phone number (e.g., 254712345678): ")
        
        if not first_name:
            first_name = input("First name (optional): ") or None
        
        if not last_name:
            last_name = input("Last name (optional): ") or None
        
        # Validate inputs
        if len(password) < 8:
            print("❌ Password must be at least 8 characters")
            return False
        
        # Create super admin user (without organization)
        try:
            user = User(
                email=email,
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                role="super_admin",
                is_super_admin=True,
                is_active=True,
                permissions=["*"],
                organization_id=None  # Super admin doesn't need an organization
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            print(f"\n✅ Super admin created successfully!")
            print(f"   Email: {user.email}")
            print(f"   Phone: {user.phone}")
            print(f"   Name: {user.first_name or ''} {user.last_name or ''}")
            print(f"   User ID: {user.id}")
            print(f"   Role: {user.role}")
            print(f"   Super Admin: {user.is_super_admin}")
            
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Error creating super admin: {str(e)}")
            logger.error(f"Failed to create super admin: {e}", exc_info=True)
            return False


def create_super_admin_with_organization(email=None, password=None, phone=None, 
                                         org_name=None, org_slug=None, 
                                         first_name=None, last_name=None):
    """Create a super admin with an associated organization"""
    
    app = create_app()
    
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            print(f"\n⚠️  User with email {email} already exists!")
            return False
        
        # Get user input if not provided
        if not email:
            email = input("Email address: ")
        
        if not password:
            password = getpass("Password: ")
            confirm_password = getpass("Confirm password: ")
            if password != confirm_password:
                print("❌ Passwords do not match!")
                return False
        
        if not phone:
            phone = input("Phone number (e.g., 254712345678): ")
        
        if not org_name:
            org_name = input("Organization name: ")
        
        if not org_slug:
            org_slug = input("Organization slug (e.g., my-org): ")
            org_slug = org_slug.lower().replace(' ', '-')
        
        if not first_name:
            first_name = input("First name (optional): ") or None
        
        if not last_name:
            last_name = input("Last name (optional): ") or None
        
        # Validate
        if len(password) < 8:
            print("❌ Password must be at least 8 characters")
            return False
        
        try:
            from app.models import Organization, OrganizationUser
            
            # Create organization first
            organization = Organization(
                name=org_name,
                slug=org_slug,
                business_type="isp",
                email=email,
                phone=phone,
                status="active",
                subscription_tier="enterprise",
                subscription_status="active",
                currency="KES",
                timezone="Africa/Nairobi"
            )
            db.session.add(organization)
            db.session.flush()  # Get the organization ID
            
            # Create super admin user
            user = User(
                email=email,
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                organization_id=organization.id,  # Link to organization
                role="org_admin",
                is_super_admin=True,  # Still a super admin
                is_active=True,
                permissions=["*"]
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.flush()  # Get the user ID
            
            # Create organization user relationship
            org_user = OrganizationUser(
                organization_id=organization.id,
                user_id=user.id,
                role="org_admin",
                is_primary=True,
                invited_by=user.id
            )
            db.session.add(org_user)
            
            db.session.commit()
            
            print(f"\n✅ Super admin and organization created successfully!")
            print(f"\n📋 Organization Details:")
            print(f"   Name: {organization.name}")
            print(f"   Slug: {organization.slug}")
            print(f"   ID: {organization.id}")
            print(f"\n👤 Admin User Details:")
            print(f"   Email: {user.email}")
            print(f"   Phone: {user.phone}")
            print(f"   Name: {user.first_name or ''} {user.last_name or ''}")
            print(f"   User ID: {user.id}")
            print(f"   Role: {user.role}")
            
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Error creating super admin: {str(e)}")
            logger.error(f"Failed to create super admin: {e}", exc_info=True)
            return False


def main():
    parser = argparse.ArgumentParser(description='Create super admin user')
    parser.add_argument('--email', type=str, help='Admin email address')
    parser.add_argument('--password', type=str, help='Admin password')
    parser.add_argument('--phone', type=str, help='Admin phone number')
    parser.add_argument('--first-name', type=str, help='Admin first name')
    parser.add_argument('--last-name', type=str, help='Admin last name')
    parser.add_argument('--with-org', action='store_true', help='Create organization as well')
    parser.add_argument('--org-name', type=str, help='Organization name (if --with-org)')
    parser.add_argument('--org-slug', type=str, help='Organization slug (if --with-org)')
    
    args = parser.parse_args()
    
    print("\n" + "="*50)
    print("ISP Management Platform - Super Admin Creator")
    print("="*50 + "\n")
    
    if args.with_org:
        create_super_admin_with_organization(
            email=args.email,
            password=args.password,
            phone=args.phone,
            org_name=args.org_name,
            org_slug=args.org_slug,
            first_name=args.first_name,
            last_name=args.last_name
        )
    else:
        create_super_admin(
            email=args.email,
            password=args.password,
            phone=args.phone,
            first_name=args.first_name,
            last_name=args.last_name
        )


if __name__ == '__main__':
    main()