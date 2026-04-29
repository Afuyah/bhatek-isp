from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import os
import base64
import logging
from typing import Optional, Dict, List, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

class EncryptionService:
    """Service for encrypting sensitive data """
    
    # Key versions for rotation support
    KEY_VERSIONS = {}  # version -> key
    
    def __init__(self, key: bytes = None, key_version: str = 'v1'):
      
        self.key_version = key_version
        
        if key:
            self.key = key
        else:
            self.key = self._load_key_from_env()
        
        self._validate_key(self.key)
        self.cipher = Fernet(self.key)
        
        # Load previous keys for rotation (if configured)
        self._load_previous_keys()
    
    def _load_key_from_env(self) -> bytes:
        """Load encryption key from environment """
        env_key = os.environ.get("ENCRYPTION_KEY")
        
        if not env_key:
            # In production, this should NEVER happen
            if os.environ.get("ENVIRONMENT", "development") == "production":
                raise ValueError(
                    "ENCRYPTION_KEY environment variable is REQUIRED in production. "
                    "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            else:
                # Only for development - log warning
                logger.warning("No ENCRYPTION_KEY set. Generating temporary key. DATA WILL BE LOST ON RESTART!")
                return Fernet.generate_key()
        
        return env_key.encode()
    
    def _validate_key(self, key: bytes):
        """Validate that key is a valid Fernet key"""
        try:
            # Test encrypt/decrypt with a small value
            test_cipher = Fernet(key)
            test_value = b"test"
            encrypted = test_cipher.encrypt(test_value)
            decrypted = test_cipher.decrypt(encrypted)
            
            if decrypted != test_value:
                raise ValueError("Key validation failed: encrypt/decrypt mismatch")
                
        except Exception as e:
            raise ValueError(f"Invalid encryption key: {str(e)}")
    
    def _load_previous_keys(self):
        """Load previous keys for decryption during rotation"""
        previous_keys = os.environ.get("ENCRYPTION_PREVIOUS_KEYS", "")
        if previous_keys:
            for key_str in previous_keys.split(","):
                key_str = key_str.strip()
                if key_str:
                    try:
                        key_bytes = key_str.encode()
                        self._validate_key(key_bytes)
                        # Store with version based on order (simplified)
                        version = f"v{len(self.KEY_VERSIONS) + 1}"
                        self.KEY_VERSIONS[version] = key_bytes
                        logger.info(f"Loaded previous encryption key version: {version}")
                    except Exception as e:
                        logger.error(f"Failed to load previous key: {str(e)}")
    
    def encrypt(self, data: str, key_version: str = None) -> str:
        
        if not data:
            return None
        
        if not isinstance(data, str):
            raise TypeError(f"Expected string, got {type(data)}")
        
        # Use current version if not specified
        version = key_version or self.key_version
        cipher = self._get_cipher_for_version(version)
        
        try:
            encrypted = cipher.encrypt(data.encode())
            # Prefix with version for future decryption
            return f"{version}:{encrypted.decode()}"
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise ValueError(f"Failed to encrypt data: {str(e)}")
    
    def decrypt(self, encrypted_data: str, verify_rotation: bool = True) -> str:
        
        if not encrypted_data:
            return None
        
        try:
            # Parse version prefix if present
            if ':' in encrypted_data and verify_rotation:
                version, actual_data = encrypted_data.split(':', 1)
                if version in self.KEY_VERSIONS or version == self.key_version:
                    cipher = self._get_cipher_for_version(version)
                    decrypted = cipher.decrypt(actual_data.encode())
                    
                    # Re-encrypt with current key for rotation
                    if verify_rotation and version != self.key_version:
                        logger.info(f"Re-encrypting data from version {version} to {self.key_version}")
                        re_encrypted = self.encrypt(decrypted.decode())
                        # Note: Need to update in database - handle at application level
                    
                    return decrypted.decode()
            
            # No version prefix or rotation not requested - try current key
            return self.cipher.decrypt(encrypted_data.encode()).decode()
            
        except InvalidToken:
            logger.error("Invalid token: Data may be corrupted or wrong key")
            raise ValueError("Decryption failed: Invalid token")
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise ValueError(f"Failed to decrypt data: {str(e)}")
    
    def _get_cipher_for_version(self, version: str) -> Fernet:
        """Get cipher for specific key version"""
        if version == self.key_version:
            return self.cipher
        
        if version in self.KEY_VERSIONS:
            return Fernet(self.KEY_VERSIONS[version])
        
        raise ValueError(f"Unknown key version: {version}")
    
    def encrypt_dict(self, data: Dict, fields: List[str], 
                     preserve_original: bool = False) -> Dict:
        
        result = data.copy()
        
        for field in fields:
            if field in result and result[field]:
                encrypted_value = self.encrypt(str(result[field]))
                
                if preserve_original:
                    result[f"{field}_plaintext"] = result[field]
                
                result[f"{field}_encrypted"] = encrypted_value
                
                if not preserve_original:
                    del result[field]
        
        return result
    
    def decrypt_dict(self, data: Dict, fields: List[str],
                     remove_encrypted: bool = True) -> Dict:
        
        result = data.copy()
        
        for field in fields:
            enc_field = f"{field}_encrypted"
            if enc_field in result and result[enc_field]:
                try:
                    decrypted_value = self.decrypt(result[enc_field])
                    result[field] = decrypted_value
                    
                    if remove_encrypted:
                        del result[enc_field]
                except Exception as e:
                    logger.error(f"Failed to decrypt field {field}: {str(e)}")
                    # Keep encrypted field and don't set decrypted value
                    result[field] = None
        
        return result
    
    def rotate_key(self, new_key: bytes, new_version: str):
        
        self._validate_key(new_key)
        self.KEY_VERSIONS[self.key_version] = self.key
        self.key = new_key
        self.key_version = new_version
        self.cipher = Fernet(new_key)
        
        logger.info(f"Key rotated to version {new_version}. Old version {self.key_version} retained for decryption")
    
    @staticmethod
    def generate_key() -> bytes:
        """Generate a new encryption key"""
        return Fernet.generate_key()
    
    @staticmethod
    def generate_key_string() -> str:
        """Generate a new encryption key as string"""
        return Fernet.generate_key().decode()


# Singleton instance
_encryption_service = None

def get_encryption_service() -> EncryptionService:
    """Get singleton encryption service instance"""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service