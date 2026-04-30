import requests
import json
from flask import current_app
from app.core.logging.logger import logger
import threading
import time

class BrevoEmailService:
      
    def __init__(self):
        self.api_key = current_app.config.get('BREVO_API_KEY', '')
        self.use_api = current_app.config.get('BREVO_USE_API', True)
        self.smtp_host = current_app.config.get('SMTP_HOST', 'smtp-relay.brevo.com')
        self.smtp_port = current_app.config.get('SMTP_PORT', 587)
        self.smtp_user = current_app.config.get('SMTP_USER', '')
        self.smtp_password = current_app.config.get('SMTP_PASSWORD', '')
        self.from_email = current_app.config.get('FROM_EMAIL')
        self.from_name = current_app.config.get('FROM_NAME', 'Bhatek Solution')
        
        # Use Brevo API if key is available
        if self.use_api and self.api_key:
            self.use_api = True
            logger.info("Brevo email service initialized with API")
        elif self.smtp_user and self.smtp_password:
            self.use_api = False
            logger.info("Brevo email service initialized with SMTP")
        else:
            logger.warning("Brevo email service initialized in MOCK mode")
            self.use_api = False
    
    def send_email(self, to_email: str, subject: str, html_content: str, 
                   text_content: str = None, reply_to: str = None) -> bool:
        """Send email using Brevo (API or SMTP)"""
        
        if self.use_api and self.api_key:
            return self._send_via_api(to_email, subject, html_content, text_content, reply_to)
        elif self.smtp_user and self.smtp_password:
            return self._send_via_smtp(to_email, subject, html_content, text_content)
        else:
            # Mock mode for development
            logger.info(f"[BREVO_MOCK] Email to {to_email}: {subject}")
            return True
    
    def _send_via_api(self, to_email: str, subject: str, html_content: str, 
                      text_content: str = None, reply_to: str = None) -> bool:
        """Send email using Brevo REST API"""
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            
            payload = {
                "sender": {"name": self.from_name, "email": self.from_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_content
            }
            
            if text_content:
                payload["textContent"] = text_content
            
            if reply_to:
                payload["replyTo"] = {"email": reply_to}
            
            headers = {
                "accept": "application/json",
                "api-key": self.api_key,
                "content-type": "application/json"
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 201:
                logger.info(f"Email sent via Brevo API to {to_email}")
                return True
            else:
                logger.error(f"Brevo API error: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.Timeout:
            logger.error(f"Brevo API timeout for {to_email}")
            return False
        except Exception as e:
            logger.error(f"Brevo API exception: {e}", exc_info=True)
            return False
    
    def _send_via_smtp(self, to_email: str, subject: str, html_content: str, 
                       text_content: str = None) -> bool:
        """Send email using Brevo SMTP relay"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email
            msg['Subject'] = subject
            
            if text_content:
                msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent via Brevo SMTP to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Brevo SMTP error: {e}", exc_info=True)
            return False
    
    def send_verification_email(self, to_email: str, verification_url: str) -> bool:
        """Send email verification link"""
        subject = "Verify Your Email - Bhatek Solution"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Verify Your Email</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #1a202c; background-color: #f7fafc; margin: 0; padding: 0; }}
                .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
                .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
                .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
                .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 24px 0; }}
                .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Welcome to Bhatek Solution</h1>
                </div>
                <div class="content">
                    <p>Hello,</p>
                    <p>Thank you for choosing Bhatek Solution! Please verify your email address to complete your registration.</p>
                    <div style="text-align: center;">
                        <a href="{verification_url}" class="button">Verify Email Address</a>
                    </div>
                    <p>This link will expire in 24 hours.</p>
                    <p>If you didn't create an account, please ignore this email.</p>
                </div>
                <div class="footer">
                    <p>&copy; 2024 Bhatek Solution. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Welcome to Bhatek Solution!
        
        Please verify your email address by clicking the link below:
        {verification_url}
        
        This link expires in 24 hours.
        
        If you didn't create an account, please ignore this email.
        
        © 2024 Bhatek Solution. All rights reserved.
        """
        
        return self.send_email(to_email, subject, html_content, text_content)
    
    def send_welcome_email(self, to_email: str, first_name: str, organization_name: str) -> bool:
        """Send welcome email after registration"""
        try:
            subject = f"Welcome to Bhatek Solution, {first_name}!"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Welcome to Bhatek Solution</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
                    .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
                    .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
                    .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
                    .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
                    .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 16px 0; }}
                    .footer {{ text-align: center; padding: 24px; color: #718096; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Welcome Aboard, {first_name}! 🎉</h1>
                    </div>
                    <div class="content">
                        <p>Dear {first_name},</p>
                        <p>Your organization <strong>{organization_name}</strong> has been successfully registered with Bhatek Solution.</p>
                        <p>You're now ready to start managing your ISP infrastructure:</p>
                        <ul>
                            <li>📡 Set up your routers and access points</li>
                            <li>💰 Create internet plans and pricing</li>
                            <li>👥 Add subscribers and manage customers</li>
                            <li>💳 Configure M-Pesa payment integration</li>
                        </ul>
                        <div style="text-align: center;">
                            <a href="{current_app.config['BASE_URL']}/login" class="button">Go to Dashboard</a>
                        </div>
                        <hr>
                        <p style="font-size: 12px; color: #718096;">
                            Need help? Contact us at support@bhatek.space
                        </p>
                    </div>
                    <div class="footer">
                        <p>&copy; 2024 Bhatek Solution. All rights reserved.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            logger.info(f"Sending welcome email to {to_email} for {organization_name}")
            result = self.send_email(to_email, subject, html_content)
            
            if result:
                logger.info(f"Welcome email sent successfully to {to_email}")
            else:
                logger.error(f"Failed to send welcome email to {to_email}")
            
            return result
            
        except Exception as e:
            logger.error(f"Exception sending welcome email to {to_email}: {e}", exc_info=True)
            return False
    
    def send_password_reset_email(self, to_email: str, reset_token: str) -> bool:
        """Send password reset email"""
        reset_url = f"{current_app.config['BASE_URL']}/reset-password?token={reset_token}"
        subject = "Reset Your Password - Bhatek Solution"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Reset Your Password</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
                .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #ef4444; padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
                .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
                .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
                .button {{ display: inline-block; background: #ef4444; color: white; text-decoration: none; padding: 12px 32px; border-radius: 8px; font-weight: 600; margin: 16px 0; }}
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
                        <a href="{reset_url}" class="button">Reset Password</a>
                    </div>
                    <p>This link will expire in 1 hour.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, html_content)