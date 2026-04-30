from flask import current_app
from app.core.logging.logger import logger
from app.integrations.email.brevo_service import BrevoEmailService


class EmailService:
    """Unified email service using Brevo"""
    
    def __init__(self):
        self.brevo = BrevoEmailService()
    
    def send_verification_email(self, to_email: str, verification_url: str) -> bool:
        """Send email verification link"""
        return self.brevo.send_verification_email(to_email, verification_url)
    
    def send_welcome_email(self, to_email: str, first_name: str, organization_name: str) -> bool:
        """Send welcome email after registration"""
        return self.brevo.send_welcome_email(to_email, first_name, organization_name)
    
    def send_password_reset_email(self, to_email: str, reset_token: str) -> bool:
        """Send password reset email"""
        return self.brevo.send_password_reset_email(to_email, reset_token)
    
    def send_payment_confirmation(self, to_email: str, amount: float, 
                                   transaction_id: str, plan_name: str) -> bool:
        """Send payment confirmation email"""
        subject = f"Payment Confirmation - Bhatek Solution"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Payment Confirmation</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f7fafc; margin: 0; padding: 0; }}
                .container {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #10b981; padding: 32px 24px; text-align: center; border-radius: 12px 12px 0 0; }}
                .header h1 {{ color: white; margin: 0; font-size: 24px; font-weight: 600; }}
                .content {{ background: white; padding: 32px 24px; border-radius: 0 0 12px 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Payment Confirmed! ✅</h1>
                </div>
                <div class="content">
                    <p>Your payment of <strong>KES {amount:,.2f}</strong> for <strong>{plan_name}</strong> has been confirmed.</p>
                    <p>Transaction ID: {transaction_id}</p>
                    <p>Your internet service is now active.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.brevo.send_email(to_email, subject, html_content)