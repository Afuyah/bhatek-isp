import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app, render_template_string
from app.core.logging.logger import logger

class EmailService:
    """Email service for sending notifications"""
    
    def __init__(self):
        self.smtp_host = current_app.config.get('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = current_app.config.get('SMTP_PORT', 587)
        self.smtp_user = current_app.config.get('SMTP_USER', '')
        self.smtp_password = current_app.config.get('SMTP_PASSWORD', '')
        self.from_email = current_app.config.get('FROM_EMAIL', 'noreply@bhatek.space')
        self.base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
    
    def send_verification_email(self, to_email: str, verification_url: str) -> bool:
        """Send email verification link"""
        try:
            subject = "Verify Your Email - Bhatek ISP"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Verify Your Email</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                        line-height: 1.6;
                        color: #1a202c;
                        background-color: #f7fafc;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 560px;
                        margin: 0 auto;
                        padding: 20px;
                    }}
                    .header {{
                        background: linear-gradient(135deg, #2d6a4f 0%, #1b4d3e 100%);
                        padding: 32px 24px;
                        text-align: center;
                        border-radius: 12px 12px 0 0;
                    }}
                    .header h1 {{
                        color: white;
                        margin: 0;
                        font-size: 24px;
                        font-weight: 600;
                    }}
                    .content {{
                        background: white;
                        padding: 32px 24px;
                        border-radius: 0 0 12px 12px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                    }}
                    .button {{
                        display: inline-block;
                        background: #2d6a4f;
                        color: white;
                        text-decoration: none;
                        padding: 12px 32px;
                        border-radius: 8px;
                        font-weight: 600;
                        margin: 24px 0;
                        transition: background 0.2s;
                    }}
                    .button:hover {{
                        background: #1b4d3e;
                    }}
                    .footer {{
                        text-align: center;
                        padding: 24px;
                        color: #718096;
                        font-size: 12px;
                    }}
                    .warning {{
                        background: #fff3cd;
                        border-left: 4px solid #ffc107;
                        padding: 12px;
                        margin: 16px 0;
                        font-size: 13px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Welcome to Bhatek ISP</h1>
                    </div>
                    <div class="content">
                        <p>Hello,</p>
                        <p>Thank you for choosing Bhatek ISP! Please verify your email address to complete your registration and start managing your ISP infrastructure.</p>
                        
                        <div style="text-align: center;">
                            <a href="{verification_url}" class="button">Verify Email Address</a>
                        </div>
                        
                        <div class="warning">
                            <strong>⚠️ This link expires in 24 hours</strong><br>
                            If you didn't request this, please ignore this email.
                        </div>
                        
                        <p style="margin-top: 24px;">Or copy and paste this link into your browser:</p>
                        <p style="background: #f7fafc; padding: 12px; border-radius: 6px; word-break: break-all; font-size: 12px;">
                            <a href="{verification_url}" style="color: #2d6a4f;">{verification_url}</a>
                        </p>
                        
                        <hr style="margin: 24px 0; border: none; border-top: 1px solid #e2e8f0;">
                        
                        <p style="font-size: 14px; color: #718096;">
                            Once verified, you'll be able to:<br>
                            • Set up your organization profile<br>
                            • Configure routers and access points<br>
                            • Create internet plans and pricing<br>
                            • Start accepting subscribers
                        </p>
                    </div>
                    <div class="footer">
                        <p>&copy; 2024 Bhatek ISP. All rights reserved.</p>
                        <p>Connectivity that empowers your business.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Plain text version
            text_content = f"""
            Welcome to Bhatek ISP!
            
            Please verify your email address to complete your registration.
            
            Verification URL: {verification_url}
            
            This link expires in 24 hours.
            
            If you didn't request this, please ignore this email.
            
            Once verified, you'll be able to:
            - Set up your organization profile
            - Configure routers and access points
            - Create internet plans and pricing
            - Start accepting subscribers
            
            © 2024 Bhatek ISP. All rights reserved.
            """
            
            msg = MIMEMultipart('alternative')
            msg['From'] = self.from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Attach both plain text and HTML versions
            msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email
            if self.smtp_user and self.smtp_password:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
                logger.info(f"Verification email sent to {to_email}")
            else:
                # For development, just log
                logger.info(f"[DEV] Verification email would be sent to {to_email}: {verification_url}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send verification email to {to_email}: {e}", exc_info=True)
            return False
    
    def send_welcome_email(self, to_email: str, first_name: str, organization_name: str) -> bool:
        """Send welcome email after successful registration"""
        try:
            subject = f"Welcome to Bhatek ISP, {first_name}!"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Welcome to Bhatek ISP</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                        line-height: 1.6;
                        color: #1a202c;
                        background-color: #f7fafc;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 560px;
                        margin: 0 auto;
                        padding: 20px;
                    }}
                    .header {{
                        background: linear-gradient(135deg, #2d6a4f 0%, #1b4d3e 100%);
                        padding: 32px 24px;
                        text-align: center;
                        border-radius: 12px 12px 0 0;
                    }}
                    .header h1 {{
                        color: white;
                        margin: 0;
                        font-size: 24px;
                        font-weight: 600;
                    }}
                    .content {{
                        background: white;
                        padding: 32px 24px;
                        border-radius: 0 0 12px 12px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                    }}
                    .feature-list {{
                        margin: 24px 0;
                        padding: 0;
                        list-style: none;
                    }}
                    .feature-list li {{
                        margin: 12px 0;
                        display: flex;
                        align-items: center;
                    }}
                    .feature-list li i {{
                        color: #2d6a4f;
                        margin-right: 12px;
                        font-size: 18px;
                    }}
                    .button {{
                        display: inline-block;
                        background: #2d6a4f;
                        color: white;
                        text-decoration: none;
                        padding: 12px 32px;
                        border-radius: 8px;
                        font-weight: 600;
                        margin: 16px 0;
                    }}
                    .footer {{
                        text-align: center;
                        padding: 24px;
                        color: #718096;
                        font-size: 12px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Welcome Aboard, {first_name}! 🎉</h1>
                    </div>
                    <div class="content">
                        <p>Dear {first_name},</p>
                        <p>Thank you for choosing Bhatek ISP! Your organization <strong>{organization_name}</strong> has been successfully registered.</p>
                        
                        <p>You're now ready to start managing your ISP infrastructure. Here's what you can do next:</p>
                        
                        <ul class="feature-list">
                            <li><i>🚀</i> <strong>Set up your routers</strong> - Connect and configure MikroTik routers</li>
                            <li><i>📡</i> <strong>Configure access points</strong> - Manage your network infrastructure</li>
                            <li><i>💰</i> <strong>Create internet plans</strong> - Set up pricing and bandwidth packages</li>
                            <li><i>👥</i> <strong>Add subscribers</strong> - Start onboarding customers</li>
                            <li><i>💳</i> <strong>Configure payments</strong> - Set up M-Pesa integration</li>
                        </ul>
                        
                        <div style="text-align: center;">
                            <a href="{self.base_url}/login" class="button">Go to Dashboard</a>
                        </div>
                        
                        <hr style="margin: 24px 0; border: none; border-top: 1px solid #e2e8f0;">
                        
                        <p style="font-size: 14px; color: #718096;">
                            Need help? Check out our <a href="{self.base_url}/docs" style="color: #2d6a4f;">documentation</a> or contact our support team at support@bhatek.space
                        </p>
                    </div>
                    <div class="footer">
                        <p>&copy; 2024 Bhatek ISP. All rights reserved.</p>
                        <p>Connectivity that empowers your business.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            msg = MIMEMultipart('alternative')
            msg['From'] = self.from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(html_content, 'html'))
            
            if self.smtp_user and self.smtp_password:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
                logger.info(f"Welcome email sent to {to_email}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send welcome email: {e}", exc_info=True)
            return False
    
    def send_password_reset_email(self, to_email: str, reset_token: str) -> bool:
        """Send password reset email"""
        try:
            reset_url = f"{self.base_url}/reset-password?token={reset_token}"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Reset Your Password</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                        background-color: #f7fafc;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 560px;
                        margin: 0 auto;
                        padding: 20px;
                    }}
                    .header {{
                        background: #2d6a4f;
                        padding: 32px 24px;
                        text-align: center;
                        border-radius: 12px 12px 0 0;
                    }}
                    .content {{
                        background: white;
                        padding: 32px 24px;
                        border-radius: 0 0 12px 12px;
                    }}
                    .button {{
                        display: inline-block;
                        background: #2d6a4f;
                        color: white;
                        text-decoration: none;
                        padding: 12px 32px;
                        border-radius: 8px;
                        font-weight: 600;
                        margin: 16px 0;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1 style="color: white;">Reset Your Password</h1>
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
            
            msg = MIMEMultipart('alternative')
            msg['From'] = self.from_email
            msg['To'] = to_email
            msg['Subject'] = "Reset Your Password - Bhatek ISP"
            
            msg.attach(MIMEText(html_content, 'html'))
            
            if self.smtp_user and self.smtp_password:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send password reset email: {e}", exc_info=True)
            return False