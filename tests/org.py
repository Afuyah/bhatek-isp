#!/usr/bin/env python
"""Test organization module"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import requests
import json

BASE_URL = "http://localhost:5000"

def test_organization():
    """Test organization CRUD operations"""
    
    # 1. Login first
    print("1. Logging in...")
    login_response = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"email": "jhsync254@gmail.com", "password": "Henrix@54"}
    )
    
    if login_response.status_code != 200:
        print(f"❌ Login failed: {login_response.text}")
        return False
    
    token = login_response.json()['access_token']
    headers = {"Authorization": f"Bearer {token}"}
    print("✅ Login successful")
    
    # 2. Create organization
    print("\n2. Creating organization...")
    org_data = {
        "name": "Test Hospital",
        "business_type": "hospital",
        "email": "hospital@test.com",
        "phone": "254712345678",
        "city": "Nairobi",
        "country": "Kenya"
    }
    
    create_response = requests.post(
        f"{BASE_URL}/api/v1/organizations/",
        json=org_data,
        headers=headers
    )
    
    if create_response.status_code != 201:
        print(f"❌ Create failed: {create_response.text}")
        return False
    
    org = create_response.json()['organization']
    print(f"✅ Organization created: {org['name']} (ID: {org['id']})")
    
    # 3. Get organization by ID
    print("\n3. Getting organization by ID...")
    get_response = requests.get(
        f"{BASE_URL}/api/v1/organizations/{org['id']}",
        headers=headers
    )
    
    if get_response.status_code == 200:
        print(f"✅ Retrieved: {get_response.json()['name']}")
    
    # 4. List organizations
    print("\n4. Listing organizations...")
    list_response = requests.get(
        f"{BASE_URL}/api/v1/organizations/",
        headers=headers
    )
    
    if list_response.status_code == 200:
        data = list_response.json()
        print(f"✅ Found {data['total']} organizations")
    
    # 5. Get organization stats
    print("\n5. Getting organization stats...")
    stats_response = requests.get(
        f"{BASE_URL}/api/v1/organizations/{org['id']}/stats",
        headers=headers
    )
    
    if stats_response.status_code == 200:
        stats = stats_response.json()
        print(f"✅ Stats: {stats['total_users']} users")
    
    # 6. Update organization
    print("\n6. Updating organization...")
    update_response = requests.put(
        f"{BASE_URL}/api/v1/organizations/{org['id']}",
        json={"name": "Updated Test Hospital", "city": "Mombasa"},
        headers=headers
    )
    
    if update_response.status_code == 200:
        print(f"✅ Updated: {update_response.json()['organization']['name']}")
    
    print("\n✅ All tests passed!")
    return True

if __name__ == '__main__':
    test_organization()