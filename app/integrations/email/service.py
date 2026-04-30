import smtplib
import threading
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app
from app.core.logging.logger import logger
import time
from typing import Dict, Any, Optional


class EmailService:
    """Production-grade async email service with SSL/TLS support"""
    
    def __init__(self):
        # Load configuration from Flask app config
        self.smtp_host = current_app.config.get('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = current_app.config.get('SMTP_PORT', 587)
        self.smtp_user = current_app.config.get('SMTP_USER', '')
        self.smtp_password = current_app.config.get('SMTP_PASSWORD', '')
        self.smtp_use_tls = current_app.config.get('SMTP_USE_TLS', True)
        self.smtp_use_ssl = current_app.config.get('SMTP_USE_SSL', False)
        self.from_email = current_app.config.get('FROM_EMAIL', 'noreply@isp.com')
        self.from_name = current_app.config.get('FROM_NAME', 'ISP SaaS')
        self.base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
        
        # Email settings
        self.async_mode = current_app.config.get('EMAIL_ASYNC_MODE', True)
        self.retry_count = current_app.config.get('EMAIL_RETRY_COUNT', 3)
        self.timeout = current_app.config.get('EMAIL_TIMEOUT', 15)
    
    def send_verification_email(self, to_email: str, verification_url: str) -> bool:
        """Send email verification link (async)"""
        email_data = {
            'type': 'verification',
            'to_email': to_email,
            'verification_url': verification_url,
            'subject': "Verify Your Email - ISP SaaS",
            'template': 'verification'
        }
        return self._send_email_async(email_data)
    
    def send_welcome_email(self, to_email: str, first_name: str, organization_name: str) -> bool:
        """Send welcome email after registration"""
        email_data = {
            'type': 'welcome',
            'to_email': to_email,
            'first_name': first_name,
            'organization_name': organization_name,
            'subject': f"Welcome to ISP SaaS, {first_name}!",
            'template': 'welcome'
        }
        return self._send_email_async(email_data)
    
    def send_password_reset_email(self, to_email: str, reset_token: str) -> bool:
        """Send password reset email"""
        reset_url = f"{self.base_url}/reset-password?token={reset_token}"
        email_data = {
            'type': 'password_reset',
            'to_email': to_email,
            'reset_url': reset_url,
            'subject': "Reset Your Password - ISP SaaS",
            'template': 'password_reset'
        }
        return self._send_email_async(email_data)
    
    def send_payment_confirmation(self, to_email: str, amount: float, 
                                   transaction_id: str, plan_name: str) -> bool:
        """Send payment confirmation email"""
        email_data = {
            'type': 'payment_confirmation',
            'to_email': to_email,
            'amount': amount,
            'transaction_id': transaction_id,
            'plan_name': plan_name,
            'subject': "Payment Confirmation - ISP SaaS",
            'template': 'payment_confirmation'
        }
        return self._send_email_async(email_data)
    
    def send_invoice_email(self, to_email: str, invoice_number: str, amount: float) -> bool:
        """Send invoice email"""
        invoice_url = f"{self.base_url}/invoices/{invoice_number}"
        email_data = {
            'type': 'invoice',
            'to_email': to_email,
            'invoice_number': invoice_number,
            'amount': amount,
            'invoice_url': invoice_url,
            'subject': f"Invoice {invoice_number} - ISP SaaS",
            'template': 'invoice'
        }
        return self._send_email_async(email_data)
    
    def _send_email_async(self, email_data: Dict[str, Any]) -> bool:
        """Send email asynchronously using threading"""
        
        # For development without SMTP, just log and return success
        if not self.smtp_user or not self.smtp_password:
            logger.info(f"[EMAIL_MOCK] To: {email_data.get('to_email')}, Subject: {email_data.get('subject')}")
            return True
        
        if self.async_mode:
            # Non-blocking async sending
            thread = threading.Thread(
                target=self._send_with_retry,
                args=(email_data,),
                daemon=True
            )
            thread.start()
            logger.debug(f"Email queued: {email_data.get('subject')} to {email_data.get('to_email')}")
            return True
        else:
            # Sync mode (blocking)
            return self._send_with_retry(email_data)
    
    def _send_with_retry(self, email_data: Dict[str, Any]) -> bool:
        """Send email with retry logic and exponential backoff"""
        for attempt in range(self.retry_count):
            try:
                success = self._send_email_sync(email_data)
                if success:
                    logger.info(f"Email sent: {email_data.get('subject')} to {email_data.get('to_email')}")
                    return True
                else:
                    logger.warning(f"Email attempt {attempt + 1} failed for {email_data.get('to_email')}")
                    if attempt < self.retry_count - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff: 1, 2, 4 seconds
            except Exception as e:
                logger.error(f"Email attempt {attempt + 1} error: {e}")
                if attempt < self.retry_count - 1:
                    time.sleep(2 ** attempt)
        
        logger.error(f"Failed to send email after {self.retry_count} attempts: {email_data.get('subject')}")
        return False
    
    def _send_email_sync(self, email_data: Dict[str, Any]) -> bool:
        """Actually send the email synchronously with SSL/TLS support"""
        try:
            to_email = email_data['to_email']
            
            # Generate content based on template
            html_content = self._generate_html_content(email_data)
            text_content = self._generate_text_content(email_data)
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email
            msg['Subject'] = email_data['subject']
            msg['Message-ID'] = f"<{int(time.time())}.{hash(to_email)}@isp.com>"
            
            # Attach parts
            msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email based on configuration
            if self.smtp_use_ssl:
                # SSL connection (port 465)
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout, context=context) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            else:
                # TLS connection (port 587)
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as server:
                    if self.smtp_use_tls:
                        server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP Authentication failed: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False
    
    def _generate_html_content(self, data: Dict[str, Any]) -> str:
        """Generate HTML content based on template"""
        template_type = data.get('template', 'default')
        
        templates = {
            'verification': self._get_verification_html,
            'welcome': self._get_welcome_html,
            'password_reset': self._get_password_reset_html,
            'payment_confirmation': self._get_payment_confirmation_html,
            'invoice': self._get_invoice_html,
        }
        
        generator = templates.get(template_type, self._get_default_html)
        return generator(data)
    
    def _generate_text_content(self, data: Dict[str, Any]) -> str:
        """Generate plain text content based on template"""
        template_type = data.get('template', 'default')
        
        if template_type == 'verification':
            return f"""
Verify Your Email - ISP SaaS

Please click the link below to verify your email address:
{data['verification_url']}

This link expires in 24 hours.

If you didn't request this, please ignore this email.

© 2024 ISP SaaS. All rights reserved.
"""
        elif template_type == 'welcome':
            return f"""
Welcome to ISP SaaS, {data.get('first_name', 'User')}!

Your organization "{data.get('organization_name', 'Your Organization')}" has been successfully registered.

Login to your dashboard: {self.base_url}/login

Need help? Contact support@isp.com

© 2024 ISP SaaS. All rights reserved.
"""
        elif template_type == 'password_reset':
            return f"""
Reset Your Password

Click the link below to reset your password:
{data['reset_url']}

This link expires in 1 hour.

If you didn't request this, please ignore this email.

© 2024 ISP SaaS. All rights reserved.
"""
        else:
            return f"Please check your email for more information from ISP SaaS."
    
    def _get_verification_html(self, data: Dict[str, Any]) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify Your Email</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1a202c; background-color: #f7fafc; margin: 0; padding: 0; }}
        .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
        .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
        .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
        .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 24px 0; }}
        .button:hover {{ opacity: 0.9; }}
        .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
        .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; margin: 16px 0; font-size: 13px; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to ISP SaaS</h1>
        </div>
        <div class="content">
            <p>Hello,</p>
            <p>Thank you for choosing ISP SaaS! Please verify your email address to complete your registration and start managing your ISP infrastructure.</p>
            <div style="text-align: center;">
                <a href="{data['verification_url']}" class="button">Verify Email Address</a>
            </div>
            <div class="warning">
                <strong>⚠️ This link expires in 24 hours</strong><br>
                If you didn't request this, please ignore this email.
            </div>
            <p style="margin-top: 24px; font-size: 12px; color: #718096;">
                Or copy and paste this link: <span style="word-break: break-all;">{data['verification_url']}</span>
            </p>
        </div>
        <div class="footer">
            <p>&copy; 2024 ISP SaaS. All rights reserved.</p>
            <p>Empowering ISP businesses worldwide</p>
        </div>
    </div>
</body>
</html>
"""
    
    def _get_welcome_html(self, data: Dict[str, Any]) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Welcome to ISP SaaS</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
        .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
        .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
        .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
        .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 16px 0; }}
        .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
        .feature-list {{ margin: 24px 0; padding-left: 20px; }}
        .feature-list li {{ margin: 12px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome Aboard, {data.get('first_name', 'User')}! 🎉</h1>
        </div>
        <div class="content">
            <p>Dear {data.get('first_name', 'User')},</p>
            <p>Your organization <strong>{data.get('organization_name', 'Your Organization')}</strong> has been successfully registered.</p>
            <p>Here's what you can do next:</p>
            <ul class="feature-list">
                <li>🚀 Set up your routers and access points</li>
                <li>💰 Create internet plans and pricing</li>
                <li>👥 Add subscribers and manage customers</li>
                <li>💳 Configure M-Pesa payment integration</li>
            </ul>
            <div style="text-align: center;">
                <a href="{self.base_url}/login" class="button">Go to Dashboard</a>
            </div>
            <hr style="margin: 24px 0;">
            <p style="font-size: 12px; color: #718096;">
                Need help? Contact us at support@isp.com
            </p>
        </div>
        <div class="footer">
            <p>&copy; 2024 ISP SaaS. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    def _get_password_reset_html(self, data: Dict[str, Any]) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Reset Your Password</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
        .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #ef4444; padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
        .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
        .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
        .button {{ display: inline-block; background: #ef4444; color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 16px 0; }}
        .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Reset Your Password</h1>
        </div>
        <div class="content">
            <p>We received a request to reset your password.</p>
            <div style="text-align: center;">
                <a href="{data['reset_url']}" class="button">Reset Password</a>
            </div>
            <p>This link will expire in 1 hour.</p>
            <p>If you didn't request this, please ignore this email.</p>
        </div>
        <div class="footer">
            <p>&copy; 2024 ISP SaaS. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    def _get_payment_confirmation_html(self, data: Dict[str, Any]) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Payment Confirmation</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
        .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #10b981; padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
        .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
        .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
        .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Payment Confirmed! ✅</h1>
        </div>
        <div class="content">
            <p>Your payment of <strong>KES {data.get('amount', 0):,.2f}</strong> for <strong>{data.get('plan_name', 'Internet Plan')}</strong> has been confirmed.</p>
            <p>Transaction ID: {data.get('transaction_id', 'N/A')}</p>
            <p>Your internet service is now active. You can now connect to the network.</p>
            <p>Thank you for choosing ISP SaaS!</p>
        </div>
        <div class="footer">
            <p>&copy; 2024 ISP SaaS. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    def _get_invoice_html(self, data: Dict[str, Any]) -> str:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Invoice {data.get('invoice_number', 'N/A')}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
        .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #667eea; padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
        .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
        .invoice-details {{ background: #f7fafc; padding: 16px; border-radius: 8px; margin: 16px 0; }}
        .button {{ display: inline-block; background: #667eea; color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 16px 0; }}
        .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Invoice {data.get('invoice_number', 'N/A')}</h1>
        </div>
        <div class="content">
            <p>Hello,</p>
            <div class="invoice-details">
                <p><strong>Amount Due:</strong> KES {data.get('amount', 0):,.2f}</p>
                <p><strong>Invoice Number:</strong> {data.get('invoice_number', 'N/A')}</p>
            </div>
            <div style="text-align: center;">
                <a href="{data.get('invoice_url', '#')}" class="button">View Invoice</a>
            </div>
        </div>
        <div class="footer">
            <p>&copy; 2024 ISP SaaS. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    def _get_default_html(self, data: Dict[str, Any]) -> str:
        return f"<html><body><h1>{data.get('subject', 'Email from ISP SaaS')}</h1><p>{data.get('message', 'Please check your account.')}</p></body></html>"