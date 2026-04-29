from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
from datetime import datetime
import json

from app.core.logging.logger import logger
from app.core.database.redis_client import redis_client

class SMSProvider(ABC):
  
    @abstractmethod
    def send(self, phone: str, message: str, **kwargs) -> Dict[str, Any]:
        """Send SMS to a single recipient"""
        pass
    
    @abstractmethod
    def send_bulk(self, phones: List[str], message: str, **kwargs) -> List[Dict[str, Any]]:
        """Send SMS to multiple recipients"""
        pass
    
    @abstractmethod
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance"""
        pass

class SMSService:
    """SMS service with provider selection, rate limiting, and retry logic"""
    
    def __init__(self):
        self._providers = {}
        self._rate_limits = {}
        self._rate_limit_cache_ttl = 3600
    
    def register_provider(self, name: str, provider: SMSProvider):
        """Register an SMS provider"""
        self._providers[name] = provider
    
    def get_provider(self, provider_name: str = None) -> SMSProvider:
        """Get SMS provider by name"""
        if provider_name and provider_name in self._providers:
            return self._providers[provider_name]
        
        # Return default provider (first registered)
        if self._providers:
            return list(self._providers.values())[0]
        
        raise ValueError("No SMS provider registered")
    
    def _check_rate_limit(self, organization_id: str, phone: str, provider: str) -> bool:
        # Per-phone rate limit (max 10 messages per hour)
        phone_key = f"sms:rate:phone:{organization_id}:{phone}"
        phone_count = redis_client.get(phone_key)
        if phone_count and int(phone_count) >= 10:
            logger.warning(f"Rate limit exceeded for phone {phone}")
            return False
        
        # Per-organization rate limit (max 1000 messages per hour)
        org_key = f"sms:rate:org:{organization_id}"
        org_count = redis_client.get(org_key)
        if org_count and int(org_count) >= 1000:
            logger.warning(f"Rate limit exceeded for organization {organization_id}")
            return False
        
        # Increment counters
        pipe = redis_client.pipeline()
        pipe.incr(phone_key)
        pipe.expire(phone_key, 3600)
        pipe.incr(org_key)
        pipe.expire(org_key, 3600)
        pipe.execute()
        
        return True
    
    def send_sms(self, organization_id: str, phone: str, message: str,
                 provider_name: str = None, retries: int = 3) -> Dict[str, Any]:
        
        # Validate message length
        if len(message) > 1600:
            logger.warning(f"Message too long ({len(message)} chars), truncating")
            message = message[:1600]
        
        # Check rate limit
        if not self._check_rate_limit(organization_id, phone, provider_name or 'default'):
            return {
                'success': False,
                'error': 'Rate limit exceeded',
                'phone': phone,
                'message': message[:50] + '...' if len(message) > 50 else message
            }
        
        provider = self.get_provider(provider_name)
        
        for attempt in range(retries):
            try:
                result = provider.send(phone, message)
                
                if result.get('success'):
                    logger.info(f"SMS sent to {phone}: {result.get('message_id', 'unknown')}")
                    
                    # Store in Redis for tracking
                    tracking_key = f"sms:track:{organization_id}:{result.get('message_id', phone)}"
                    tracking_data = {
                        'phone': phone,
                        'message': message[:200],
                        'status': 'sent',
                        'sent_at': datetime.now().isoformat()
                    }
                    redis_client.setex(tracking_key, 86400, json.dumps(tracking_data))
                    
                    return result
                else:
                    logger.warning(f"SMS send attempt {attempt + 1} failed: {result.get('error')}")
                    
                    if attempt < retries - 1:
                        import time
                        time.sleep(2 ** attempt)
                    else:
                        return result
                        
            except Exception as e:
                logger.error(f"SMS send error (attempt {attempt + 1}): {e}")
                
                if attempt < retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                else:
                    return {
                        'success': False,
                        'error': str(e),
                        'phone': phone
                    }
        
        return {
            'success': False,
            'error': 'Max retries exceeded',
            'phone': phone
        }
    
    def send_bulk_sms(self, organization_id: str, phones: List[str], message: str,
                      provider_name: str = None, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Send SMS to multiple recipients in batches"""
        results = []
        
        for i in range(0, len(phones), batch_size):
            batch = phones[i:i + batch_size]
            
            for phone in batch:
                result = self.send_sms(organization_id, phone, message, provider_name)
                results.append(result)
                
                # Small delay between messages to avoid flooding
                import time
                time.sleep(0.5)
        
        return results
    
    def send_voucher_code(self, organization_id: str, phone: str, voucher_code: str,
                          plan_name: str, expiry_days: int) -> Dict[str, Any]:
        """Send voucher code SMS"""
        message = f"Your {plan_name} internet voucher: {voucher_code}. Valid for {expiry_days} days. Enjoy!"
        return self.send_sms(organization_id, phone, message)
    
    def send_payment_confirmation(self, organization_id: str, phone: str, amount: float,
                                   receipt: str, plan_name: str) -> Dict[str, Any]:
        """Send payment confirmation SMS"""
        message = f"Payment of {amount:.2f} KES confirmed. Receipt: {receipt}. {plan_name} plan activated."
        return self.send_sms(organization_id, phone, message)
    
    def send_expiry_warning(self, organization_id: str, phone: str, plan_name: str,
                            days_left: int) -> Dict[str, Any]:
        """Send subscription expiry warning SMS"""
        message = f"Your {plan_name} subscription expires in {days_left} days. Please renew to continue enjoying our service."
        return self.send_sms(organization_id, phone, message)
    
    def send_welcome_message(self, organization_id: str, phone: str, name: str = None) -> Dict[str, Any]:
        """Send welcome message to new subscriber"""
        greeting = f"Hi {name}, " if name else ""
        message = f"{greeting}Welcome to our internet service! "
        return self.send_sms(organization_id, phone, message)
    
    def get_send_stats(self, organization_id: str, hours: int = 24) -> Dict[str, Any]:
        pattern = f"sms:track:{organization_id}:*"
        keys = redis_client.keys(pattern)
        
        stats = {
            'total_sent': len(keys),
            'successful': 0,
            'failed': 0,
            'by_phone': {}
        }
        
        for key in keys:
            data = redis_client.get(key)
            if data:
                data = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                if data.get('status') == 'sent':
                    stats['successful'] += 1
                else:
                    stats['failed'] += 1
                
                phone = data.get('phone')
                if phone:
                    stats['by_phone'][phone] = stats['by_phone'].get(phone, 0) + 1
        
        return stats