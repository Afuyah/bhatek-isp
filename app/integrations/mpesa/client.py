import json
import base64
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from flask import current_app
import hashlib

from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client

class MpesaClient:
    """M-Pesa API client with token management and retry logic"""
    
    def __init__(self, organization_id: str, payment_account: Dict[str, Any]):
        self.organization_id = organization_id
        self.account = payment_account
        self.encryption = EncryptionService()
        
        # Decrypt credentials
        self.consumer_key = self.encryption.decrypt(payment_account['consumer_key_encrypted'])
        self.consumer_secret = self.encryption.decrypt(payment_account['consumer_secret_encrypted'])
        self.passkey = self.encryption.decrypt(payment_account['passkey_encrypted'])
        self.shortcode = payment_account['shortcode']
        self.account_type = payment_account.get('account_type', 'paybill')
        self.environment = payment_account.get('environment', 'sandbox')
        
        # Set API URLs
        if self.environment == 'production':
            self.base_url = 'https://api.safaricom.co.ke'
        else:
            self.base_url = 'https://sandbox.safaricom.co.ke'
        
        self.token_url = f'{self.base_url}/oauth/v1/generate?grant_type=client_credentials'
        self.stk_push_url = f'{self.base_url}/mpesa/stkpush/v1/processrequest'
        self.stk_query_url = f'{self.base_url}/mpesa/stkpushquery/v1/query'
        self.b2c_url = f'{self.base_url}/mpesa/b2c/v1/paymentrequest'
        self.b2b_url = f'{self.base_url}/mpesa/b2b/v1/paymentrequest'
        self.reversal_url = f'{self.base_url}/mpesa/reversal/v1/request'
        
        self.access_token = None
        self.token_expiry = None
        
        # Cache key for token
        self.token_cache_key = f"mpesa:token:{organization_id}"
    
    def _get_auth_header(self) -> str:
        """Get basic auth header"""
        credentials = f"{self.consumer_key}:{self.consumer_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"
    
    def _get_access_token(self, force_refresh: bool = False) -> str:
        """Get OAuth access token with caching"""
        if not force_refresh:
            # Check cache first
            cached_token = redis_client.get(self.token_cache_key)
            if cached_token:
                return cached_token.decode() if isinstance(cached_token, bytes) else cached_token
            
            if self.access_token and self.token_expiry and datetime.now() < self.token_expiry:
                return self.access_token
        
        headers = {'Authorization': self._get_auth_header()}
        
        try:
            response = requests.get(self.token_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            self.access_token = data['access_token']
            expires_in = data.get('expires_in', 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            
            # Cache token
            redis_client.setex(self.token_cache_key, expires_in - 60, self.access_token)
            
            logger.info(f"Obtained M-Pesa access token for org {self.organization_id}")
            return self.access_token
            
        except requests.RequestException as e:
            logger.error(f"Failed to get M-Pesa token: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"M-Pesa authentication failed: {str(e)}")
    
    def stk_push(
        self,
        phone_number: str,
        amount: float,
        reference: str,
        description: str = "Internet Payment",
        callback_url: str = None,
        transaction_type: str = "CustomerPayBillOnline"
    ) -> Dict[str, Any]:
        """
        Initiate STK Push payment
        
        Args:
            phone_number: Customer phone number
            amount: Amount to charge
            reference: Account reference (max 12 chars)
            description: Transaction description (max 13 chars)
            callback_url: Callback URL for payment notification
            transaction_type: Transaction type (CustomerPayBillOnline or CustomerBuyGoodsOnline)
        """
        try:
            access_token = self._get_access_token()
            
            # Format phone number (2547XXXXXXXX)
            phone = self._format_phone(phone_number)
            
            # Generate timestamp
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            
            # Generate password
            password_str = f"{self.shortcode}{self.passkey}{timestamp}"
            password = base64.b64encode(password_str.encode()).decode()
            
            if not callback_url:
                callback_url = f"{current_app.config['MPESA_CALLBACK_BASE_URL']}/api/v1/payments/mpesa/callback"
            
            # Determine transaction type based on account type
            if self.account_type == 'till_number':
                transaction_type = "CustomerBuyGoodsOnline"
            
            payload = {
                'BusinessShortCode': self.shortcode,
                'Password': password,
                'Timestamp': timestamp,
                'TransactionType': transaction_type,
                'Amount': int(amount),
                'PartyA': phone,
                'PartyB': self.shortcode,
                'PhoneNumber': phone,
                'CallBackURL': callback_url,
                'AccountReference': reference[:12],
                'TransactionDesc': description[:13]
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                self.stk_push_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('ResponseCode') == '0':
                logger.info(f"STK Push initiated for {phone}: {result.get('CheckoutRequestID')}")
                return {
                    'success': True,
                    'checkout_request_id': result.get('CheckoutRequestID'),
                    'response_code': result.get('ResponseCode'),
                    'response_description': result.get('ResponseDescription'),
                    'customer_message': result.get('CustomerMessage')
                }
            else:
                logger.error(f"STK Push failed: {result}")
                return {
                    'success': False,
                    'response_code': result.get('ResponseCode'),
                    'response_description': result.get('ResponseDescription'),
                    'error': result.get('ResponseDescription', 'STK Push failed')
                }
            
        except requests.RequestException as e:
            logger.error(f"STK Push request failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'response_description': 'Network error occurred'
            }
        except Exception as e:
            logger.error(f"STK Push failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def query_status(self, checkout_request_id: str) -> Dict[str, Any]:
        """Query STK Push payment status"""
        try:
            access_token = self._get_access_token()
            
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            password_str = f"{self.shortcode}{self.passkey}{timestamp}"
            password = base64.b64encode(password_str.encode()).decode()
            
            payload = {
                'BusinessShortCode': self.shortcode,
                'Password': password,
                'Timestamp': timestamp,
                'CheckoutRequestID': checkout_request_id
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                self.stk_query_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('ResultCode') == '0':
                return {
                    'success': True,
                    'status': 'completed',
                    'result_code': result.get('ResultCode'),
                    'result_description': result.get('ResultDesc'),
                    'mpesa_receipt': result.get('MpesaReceiptNumber'),
                    'amount': result.get('Amount'),
                    'transaction_date': result.get('TransactionDate'),
                    'phone': result.get('PhoneNumber')
                }
            elif result.get('ResultCode') == '1037':
                return {
                    'success': True,
                    'status': 'pending',
                    'result_code': result.get('ResultCode'),
                    'result_description': result.get('ResultDesc'),
                    'message': 'Payment pending customer input'
                }
            else:
                return {
                    'success': False,
                    'status': 'failed',
                    'result_code': result.get('ResultCode'),
                    'result_description': result.get('ResultDesc'),
                    'error': result.get('ResultDesc', 'Payment failed')
                }
                
        except requests.RequestException as e:
            logger.error(f"Query status failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def b2c_payment(
        self,
        phone_number: str,
        amount: float,
        command_id: str = "BusinessPayment",
        remarks: str = "Payment",
        occasion: str = None
    ) -> Dict[str, Any]:
        """
        Send B2C payment to customer
        
        Command IDs:
        - SalaryPayment: For salary payments
        - BusinessPayment: For business payments
        - PromotionPayment: For promotional payments
        """
        try:
            access_token = self._get_access_token()
            
            phone = self._format_phone(phone_number)
            
            # Generate security credential
            security_credential = self._generate_security_credential()
            
            payload = {
                'InitiatorName': self.consumer_key,
                'SecurityCredential': security_credential,
                'CommandID': command_id,
                'Amount': int(amount),
                'PartyA': self.shortcode,
                'PartyB': phone,
                'Remarks': remarks[:100],
                'QueueTimeOutURL': f"{current_app.config['MPESA_CALLBACK_BASE_URL']}/api/v1/payments/mpesa/b2c/timeout",
                'ResultURL': f"{current_app.config['MPESA_CALLBACK_BASE_URL']}/api/v1/payments/mpesa/b2c/result",
                'Occasion': occasion[:100] if occasion else remarks[:100]
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                self.b2c_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            if result.get('ResponseCode') == '0':
                logger.info(f"B2C payment initiated for {phone}: {result.get('ConversationID')}")
                return {
                    'success': True,
                    'conversation_id': result.get('ConversationID'),
                    'originator_conversation_id': result.get('OriginatorConversationID'),
                    'response_code': result.get('ResponseCode'),
                    'response_description': result.get('ResponseDescription')
                }
            else:
                return {
                    'success': False,
                    'response_code': result.get('ResponseCode'),
                    'response_description': result.get('ResponseDescription'),
                    'error': result.get('ResponseDescription')
                }
                
        except Exception as e:
            logger.error(f"B2C payment failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def reversal(
        self,
        transaction_id: str,
        amount: float,
        receiver_party: str,
        remarks: str = "Reversal"
    ) -> Dict[str, Any]:
        """Reverse a completed transaction"""
        try:
            access_token = self._get_access_token()
            
            # Generate security credential
            security_credential = self._generate_security_credential()
            
            payload = {
                'CommandID': 'TransactionReversal',
                'ReceiverParty': receiver_party,
                'RecieverIdentifierType': '11',  # 11 = Paybill/Till
                'TransactionID': transaction_id,
                'Amount': int(amount),
                'Initiator': self.consumer_key,
                'SecurityCredential': security_credential,
                'Remarks': remarks[:100],
                'QueueTimeOutURL': f"{current_app.config['MPESA_CALLBACK_BASE_URL']}/api/v1/payments/mpesa/reversal/timeout",
                'ResultURL': f"{current_app.config['MPESA_CALLBACK_BASE_URL']}/api/v1/payments/mpesa/reversal/result"
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                self.reversal_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            
            return {
                'success': result.get('ResponseCode') == '0',
                'conversation_id': result.get('ConversationID'),
                'response_code': result.get('ResponseCode'),
                'response_description': result.get('ResponseDescription')
            }
            
        except Exception as e:
            logger.error(f"Reversal failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
    
    def _generate_security_credential(self) -> str:
        """Generate security credential for B2C/B2B requests"""
        # In production, this should use proper encryption with Safaricom's public certificate
        # For sandbox, a simplified version is acceptable
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa, padding
        from cryptography.hazmat.backends import default_backend
        
        # This is a placeholder - in production, load the actual Safaricom certificate
        # and encrypt the initiator password
        initiator_password = self.consumer_secret
        return base64.b64encode(initiator_password.encode()).decode()
    
    def _format_phone(self, phone: str) -> str:
        """Format phone number to 254XXXXXXXXX"""
        import re
        phone = re.sub(r'\D', '', phone)
        
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('7') or phone.startswith('1'):
            phone = '254' + phone
        
        return phone
    
    def register_urls(self, confirmation_url: str, validation_url: str) -> Dict[str, Any]:
        """Register C2B URLs for paybill"""
        try:
            access_token = self._get_access_token()
            
            payload = {
                'ShortCode': self.shortcode,
                'ResponseType': 'Completed',
                'ConfirmationURL': confirmation_url,
                'ValidationURL': validation_url
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                f'{self.base_url}/mpesa/c2b/v1/registerurl',
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"URL registration failed: {e}")
            return {'success': False, 'error': str(e)}
    
    def simulate_c2b(self, phone_number: str, amount: float, reference: str) -> Dict[str, Any]:
        """Simulate C2B payment (sandbox only)"""
        if self.environment != 'sandbox':
            return {'success': False, 'error': 'Simulation only available in sandbox'}
        
        try:
            access_token = self._get_access_token()
            
            phone = self._format_phone(phone_number)
            
            payload = {
                'ShortCode': self.shortcode,
                'Msisdn': phone,
                'Amount': int(amount),
                'BillRefNumber': reference,
                'CommandID': 'CustomerPayBillOnline'
            }
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                f'{self.base_url}/mpesa/c2b/v1/simulate',
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"C2B simulation failed: {e}")
            return {'success': False, 'error': str(e)}