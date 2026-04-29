#!/usr/bin/env python
"""Generate a valid Fernet encryption key"""

from cryptography.fernet import Fernet

# Generate a new key
key = Fernet.generate_key()
print(f"\n🔐 Generated Encryption Key:")
print(f"{key.decode()}\n")
print("Add this to your .env file:")
print(f"ENCRYPTION_KEY={key.decode()}\n")