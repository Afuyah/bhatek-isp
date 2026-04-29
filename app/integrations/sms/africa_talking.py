import requests
from typing import Dict, Any, List
from datetime import datetime

from app.core.logging.logger import logger
from app.integrations.sms.provider import SMSProvider

class AfricaTalkingProvider(SMSProvider):
     
    def __init__(self, api_key: str, username: str = 'sandbox', 
                 sender_id: str = None, environment: str = 'sandbox'):
        self.api_key = api_key
        self.username = username
        self.sender_id = sender_id
        self.environment = environment
        
        if environment == 'production':
            self.base_url = 'https://api.africastalking.com/version1'
        else:
            self.base_url = 'https://api.sandbox.africastalking.com/version1'
    
    def send(self, phone: str, message: str, **kwargs) -> Dict[str, Any]:
        """Send SMS via Africa's Talking"""
        try:
            # Format phone number
            phone = self._format_phone(phone)
            
            payload = {
                'username': self.username,
                'to': phone,
                'message': message
            }
            
            if self.sender_id:
                payload['from'] = self.sender_id
            
            headers = {
                'apiKey': self.api_key,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            response = requests.post(
                f'{self.base_url}/messaging',
                data=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            sms_data = result.get('SMSMessageData', {})
            recipients = sms_data.get('Recipients', [])
            
            if recipients:
                recipient = recipients[0]
                return {
                    'success': recipient.get('status') == 'Success',
                    'message_id': recipient.get('messageId'),
                    'status': recipient.get('status'),
                    'phone': recipient.get('number'),
                    'cost': recipient.get('cost'),
                    'provider': 'africa_talking'
                }
            
            return {
                'success': False,
                'error': 'No recipients in response',
                'provider': 'africa_talking'
            }
            
        except requests.RequestException as e:
            logger.error(f"Africa's Talking SMS request failed: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            return {
                'success': False,
                'error': str(e),
                'provider': 'africa_talking'
            }
        except Exception as e:
            logger.error(f"Africa's Talking SMS failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'provider': 'africa_talking'
            }
    
    def send_bulk(self, phones: List[str], message: str, **kwargs) -> List[Dict[str, Any]]:
        """Send SMS to multiple recipients"""
        results = []
        for phone in phones:
            results.append(self.send(phone, message))
        return results
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance"""
        try:
            headers = {
                'apiKey': self.api_key,
                'Accept': 'application/json'
            }
            
            response = requests.get(
                f'{self.base_url}/user/balance',
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            user_data = result.get('UserData', {})
            
            return {
                'success': True,
                'balance': user_data.get('balance'),
                'currency': user_data.get('currencyCode')
            }
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {'success': False, 'error': str(e)}
    
    def _format_phone(self, phone: str) -> str:
        """Format phone number for Africa's Talking"""
        import re
        phone = re.sub(r'\D', '', phone)
        
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif not phone.startswith('254'):
            phone = '254' + phone
        
        return phone