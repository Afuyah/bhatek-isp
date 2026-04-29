from typing import Dict, Any, List
from datetime import datetime

from app.core.logging.logger import logger
from app.integrations.sms.provider import SMSProvider

class TwilioProvider(SMSProvider):
    """Twilio SMS provider implementation"""
    
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.base_url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}'
    
    def send(self, phone: str, message: str, **kwargs) -> Dict[str, Any]:
        """Send SMS via Twilio"""
        try:
            from twilio.rest import Client
            
            client = Client(self.account_sid, self.auth_token)
            
            message_obj = client.messages.create(
                body=message,
                from_=self.from_number,
                to=phone
            )
            
            return {
                'success': True,
                'message_id': message_obj.sid,
                'status': message_obj.status,
                'phone': phone,
                'provider': 'twilio'
            }
            
        except Exception as e:
            logger.error(f"Twilio SMS failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'phone': phone,
                'provider': 'twilio'
            }
    
    def send_bulk(self, phones: List[str], message: str, **kwargs) -> List[Dict[str, Any]]:
        """Send SMS to multiple recipients"""
        from concurrent.futures import ThreadPoolExecutor
        
        def send_one(phone):
            return self.send(phone, message)
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(send_one, phones))
        
        return results
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance"""
        try:
            from twilio.rest import Client
            
            client = Client(self.account_sid, self.auth_token)
            balance = client.balance.fetch()
            
            return {
                'success': True,
                'balance': balance.balance,
                'currency': balance.currency
            }
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_message_status(self, message_sid: str) -> Dict[str, Any]:
        """Get status of a specific message"""
        try:
            from twilio.rest import Client
            
            client = Client(self.account_sid, self.auth_token)
            message = client.messages(message_sid).fetch()
            
            return {
                'success': True,
                'status': message.status,
                'error_code': message.error_code,
                'error_message': message.error_message
            }
        except Exception as e:
            logger.error(f"Failed to get message status: {e}")
            return {'success': False, 'error': str(e)}