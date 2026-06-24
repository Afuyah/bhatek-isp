
from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, desc, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.payment import Transaction, PaymentAccount, PaymentWebhookLog, Refund
from app.core.database.session import db
from app.core.logging.logger import logger


class TransactionRepository:
    """Data access layer for Transaction operations with tenant isolation."""

    def __init__(self):
        self.model = Transaction

    # -------------------------------------------------------------------------
    # READ — SINGLE
    # -------------------------------------------------------------------------

    def get_by_id(self, transaction_id: UUID) -> Optional[Transaction]:
        """Get transaction by ID."""
        try:
            return self.model.query.filter_by(id=transaction_id).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise

    def get_by_reference(self, reference: str) -> Optional[Transaction]:
        """Get transaction by internal reference number."""
        try:
            return self.model.query.filter_by(
                transaction_reference=reference
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_reference: {e}", exc_info=True)
            raise

    def get_by_checkout_id(
        self, checkout_id: str
    ) -> Optional[Transaction]:
        """
        Get transaction by M-Pesa checkout request ID.

        Used to find the transaction when Safaricom sends a callback
        with the CheckoutRequestID.
        """
        try:
            return self.model.query.filter(
                self.model.payment_details['checkout_request_id'].astext == checkout_id
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_checkout_id: {e}", exc_info=True)
            raise

    def get_by_mpesa_receipt(
        self,
        mpesa_receipt: str,
        organization_id: UUID,
    ) -> Optional[Transaction]:
        """
        Get transaction by M-Pesa receipt number within an organization.

        CRITICAL for multi-tenant isolation and self-remediation:
        A user entering an M-Pesa code in Org A's captive portal
        cannot use a receipt from Org B.
        """
        try:
            return self.model.query.filter(
                and_(
                    self.model.mpesa_receipt == mpesa_receipt.upper(),
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_mpesa_receipt: {e}", exc_info=True
            )
            raise

    def get_by_subscriber(
        self,
        subscriber_id: UUID,
        organization_id: UUID,
        limit: int = 50,
    ) -> List[Transaction]:
        """
        Get all transactions for a subscriber within an organization.

        Args:
            subscriber_id: Subscriber UUID
            organization_id: Tenant organization UUID
            limit: Maximum results

        Returns:
            List of transactions, most recent first
        """
        try:
            return (
                self.model.query.filter(
                    and_(
                        self.model.subscriber_id == subscriber_id,
                        self.model.organization_id == organization_id,
                    )
                )
                .order_by(desc(self.model.created_at))
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_subscriber: {e}", exc_info=True
            )
            raise

    # -------------------------------------------------------------------------
    # READ — LIST
    # -------------------------------------------------------------------------

    def get_by_organization(
        self,
        organization_id: UUID,
        skip: int = 0,
        limit: int = 100,
        subscriber_id: UUID = None,
        status: str = None,
        payment_method: str = None,
    ) -> List[Transaction]:
        """
        Get all transactions for an organization with optional filters.

        Args:
            organization_id: Tenant organization UUID
            skip: Pagination offset
            limit: Maximum results
            subscriber_id: Filter by subscriber
            status: Filter by status (pending, success, failed, refunded)
            payment_method: Filter by method (mpesa, cash, bank_transfer, voucher)

        Returns:
            List of transactions, most recent first
        """
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )

            if subscriber_id:
                query = query.filter(
                    self.model.subscriber_id == subscriber_id
                )
            if status:
                query = query.filter(self.model.status == status)
            if payment_method:
                query = query.filter(
                    self.model.payment_method == payment_method
                )

            return (
                query.order_by(desc(self.model.created_at))
                .offset(skip)
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_organization: {e}", exc_info=True
            )
            raise

    def get_pending_transactions(
        self,
        organization_id: UUID,
        older_than_minutes: int = 5,
    ) -> List[Transaction]:
        """
        Get pending transactions older than N minutes.

        Used by background tasks to query stuck payments.
        """
        try:
            cutoff = datetime.utcnow() - datetime.timedelta(
                minutes=older_than_minutes
            )
            return (
                self.model.query.filter(
                    and_(
                        self.model.organization_id == organization_id,
                        self.model.status == 'pending',
                        self.model.created_at <= cutoff,
                    )
                ).all()
            )
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_pending_transactions: {e}",
                exc_info=True,
            )
            raise

    # -------------------------------------------------------------------------
    # COUNT
    # -------------------------------------------------------------------------

    def count_by_organization(
        self,
        organization_id: UUID,
        subscriber_id: UUID = None,
        status: str = None,
        payment_method: str = None,
    ) -> int:
        """
        Count transactions for an organization with optional filters.
        """
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            if subscriber_id:
                query = query.filter(
                    self.model.subscriber_id == subscriber_id
                )
            if status:
                query = query.filter(self.model.status == status)
            if payment_method:
                query = query.filter(
                    self.model.payment_method == payment_method
                )
            return query.count()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in count_by_organization: {e}", exc_info=True
            )
            raise

    def get_total_revenue(
        self,
        organization_id: UUID,
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> float:
        """
        Calculate total successful transaction revenue for an org.

        Args:
            organization_id: Tenant organization UUID
            start_date: Optional start of date range
            end_date: Optional end of date range

        Returns:
            Total revenue amount
        """
        try:
            query = self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.status == 'success',
                )
            )
            if start_date:
                query = query.filter(self.model.completed_at >= start_date)
            if end_date:
                query = query.filter(self.model.completed_at <= end_date)

            result = query.with_entities(
                func.sum(self.model.amount)
            ).scalar()
            return float(result) if result else 0.0
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_total_revenue: {e}", exc_info=True
            )
            raise

    # -------------------------------------------------------------------------
    # CREATE / UPDATE
    # -------------------------------------------------------------------------

    def create(self, data: Dict[str, Any]) -> Transaction:
        """
        Create a new transaction record.

        Args:
            data: Transaction model fields dict

        Returns:
            Newly created Transaction instance
        """
        try:
            transaction = self.model(**data)
            db.session.add(transaction)
            db.session.commit()
            logger.info(
                f"Created transaction: {transaction.transaction_reference} "
                f"(status: {transaction.status})"
            )
            return transaction
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise

    def update(
        self,
        transaction_id: UUID,
        data: Dict[str, Any],
    ) -> Optional[Transaction]:
        """
        Update a transaction's fields.

        Args:
            transaction_id: Transaction UUID
            data: Dict of fields to update (None values are skipped)

        Returns:
            Updated Transaction or None if not found
        """
        try:
            transaction = self.get_by_id(transaction_id)
            if not transaction:
                logger.warning(
                    f"Update failed: transaction {transaction_id} not found"
                )
                return None

            for key, value in data.items():
                if hasattr(transaction, key) and value is not None:
                    setattr(transaction, key, value)

            db.session.commit()
            logger.info(f"Updated transaction: {transaction_id}")
            return transaction
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise

    # -------------------------------------------------------------------------
    # STATISTICS
    # -------------------------------------------------------------------------

    def get_payment_stats(
        self,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """
        Get payment statistics for dashboard.

        Returns counts by status, total revenue, and recent transactions.
        """
        try:
            total = self.count_by_organization(organization_id)
            successful = self.count_by_organization(
                organization_id, status='success'
            )
            pending = self.count_by_organization(
                organization_id, status='pending'
            )
            failed = self.count_by_organization(
                organization_id, status='failed'
            )
            refunded = self.count_by_organization(
                organization_id, status='refunded'
            )
            revenue = self.get_total_revenue(organization_id)

            # M-Pesa specific
            mpesa_total = self.count_by_organization(
                organization_id, payment_method='mpesa'
            )
            mpesa_success = self.count_by_organization(
                organization_id, status='success', payment_method='mpesa'
            )

            return {
                'total': total,
                'successful': successful,
                'pending': pending,
                'failed': failed,
                'refunded': refunded,
                'total_revenue': revenue,
                'mpesa': {
                    'total': mpesa_total,
                    'successful': mpesa_success,
                },
            }
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_payment_stats: {e}", exc_info=True
            )
            raise


class PaymentAccountRepository:
    """
    Data access layer for PaymentAccount operations.

    Each organization can have multiple payment accounts (paybill, till).
    One is marked as default for automatic selection.
    """

    def __init__(self):
        self.model = PaymentAccount

    def get_by_id(
        self,
        account_id: UUID,
        organization_id: UUID,
    ) -> Optional[PaymentAccount]:
        """Get payment account by ID with org isolation."""
        try:
            return self.model.query.filter(
                and_(
                    self.model.id == account_id,
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in PaymentAccount.get_by_id: {e}",
                exc_info=True,
            )
            raise

    def get_default(
        self,
        organization_id: UUID,
    ) -> Optional[PaymentAccount]:
        """
        Get the default payment account for an organization.

        Used automatically when processing payments to determine
        which M-Pesa credentials to use.
        """
        try:
            return self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_default == True,
                    self.model.is_active == True,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_default: {e}", exc_info=True
            )
            raise

    def get_by_shortcode(
        self,
        shortcode: str,
        organization_id: UUID,
    ) -> Optional[PaymentAccount]:
        """
        Get payment account by shortcode within an org.

        Used to identify which org a C2B callback belongs to
        when Safaricom sends the business shortcode.
        """
        try:
            return self.model.query.filter(
                and_(
                    self.model.shortcode == shortcode,
                    self.model.organization_id == organization_id,
                )
            ).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_shortcode: {e}", exc_info=True
            )
            raise

    def get_by_organization(
        self,
        organization_id: UUID,
        is_active: bool = True,
    ) -> List[PaymentAccount]:
        """Get all payment accounts for an organization."""
        try:
            query = self.model.query.filter(
                self.model.organization_id == organization_id
            )
            if is_active:
                query = query.filter(self.model.is_active == True)
            return query.order_by(desc(self.model.is_default)).all()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_organization: {e}", exc_info=True
            )
            raise

    def create(self, data: Dict[str, Any]) -> PaymentAccount:
        """Create a new payment account."""
        try:
            # If this is the first account, make it default
            existing = self.get_by_organization(
                data['organization_id'], is_active=True
            )
            if not existing:
                data['is_default'] = True

            account = self.model(**data)
            db.session.add(account)
            db.session.commit()
            logger.info(
                f"Created payment account: {account.account_name} "
                f"(shortcode: {account.shortcode})"
            )
            return account
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PaymentAccount.create: {e}", exc_info=True
            )
            raise

    def update(
        self,
        account_id: UUID,
        organization_id: UUID,
        data: Dict[str, Any],
    ) -> Optional[PaymentAccount]:
        """Update a payment account."""
        try:
            account = self.get_by_id(account_id, organization_id)
            if not account:
                return None

            for key, value in data.items():
                if hasattr(account, key) and value is not None:
                    setattr(account, key, value)

            db.session.commit()
            logger.info(f"Updated payment account: {account_id}")
            return account
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PaymentAccount.update: {e}", exc_info=True
            )
            raise

    def set_default(
        self,
        account_id: UUID,
        organization_id: UUID,
    ) -> bool:
        """
        Set a payment account as the default for its organization.

        Removes default flag from all other accounts in the org first.
        """
        try:
            # Remove default from all org accounts
            self.model.query.filter(
                and_(
                    self.model.organization_id == organization_id,
                    self.model.is_default == True,
                )
            ).update({'is_default': False})

            # Set this one as default
            account = self.get_by_id(account_id, organization_id)
            if not account:
                return False

            account.is_default = True
            db.session.commit()
            logger.info(
                f"Set payment account {account_id} as default for org "
                f"{organization_id}"
            )
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in set_default: {e}", exc_info=True
            )
            raise

    def delete(
        self,
        account_id: UUID,
        organization_id: UUID,
        soft_delete: bool = True,
    ) -> bool:
        """
        Delete or deactivate a payment account.

        Prevents deletion of the only active account.
        """
        try:
            account = self.get_by_id(account_id, organization_id)
            if not account:
                return False

            if soft_delete:
                # Check if this is the only active account
                active_count = len(
                    self.get_by_organization(organization_id, is_active=True)
                )
                if active_count <= 1 and account.is_active:
                    raise ValueError(
                        "Cannot deactivate the only payment account. "
                        "Add another account first."
                    )
                account.is_active = False
            else:
                db.session.delete(account)

            db.session.commit()
            logger.info(f"Payment account {account_id} deleted")
            return True
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in PaymentAccount.delete: {e}", exc_info=True
            )
            raise


class PaymentWebhookLogRepository:
    """Data access layer for PaymentWebhookLog operations."""

    def __init__(self):
        self.model = PaymentWebhookLog

    def create(self, data: Dict[str, Any]) -> PaymentWebhookLog:
        """Create a webhook log entry."""
        try:
            log = self.model(**data)
            db.session.add(log)
            db.session.commit()
            return log
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in WebhookLog.create: {e}", exc_info=True
            )
            raise

    def get_by_request_id(
        self, request_id: str
    ) -> Optional[PaymentWebhookLog]:
        """Get webhook log by request ID for deduplication."""
        try:
            return self.model.query.filter_by(request_id=request_id).first()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_request_id: {e}", exc_info=True
            )
            raise

    def get_unprocessed(
        self,
        organization_id: UUID = None,
    ) -> List[PaymentWebhookLog]:
        """Get unprocessed webhook logs for retry."""
        try:
            query = self.model.query.filter(
                self.model.processed == False
            )
            if organization_id:
                query = query.filter(
                    self.model.organization_id == organization_id
                )
            return query.order_by(self.model.created_at).limit(100).all()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_unprocessed: {e}", exc_info=True
            )
            raise


class RefundRepository:
    """Data access layer for Refund operations."""

    def __init__(self):
        self.model = Refund

    def create(self, data: Dict[str, Any]) -> Refund:
        """Create a refund record."""
        try:
            refund = self.model(**data)
            db.session.add(refund)
            db.session.commit()
            return refund
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"Database error in Refund.create: {e}", exc_info=True
            )
            raise

    def get_by_transaction(
        self,
        transaction_id: UUID,
        organization_id: UUID,
    ) -> List[Refund]:
        """Get all refunds for a transaction."""
        try:
            return self.model.query.filter(
                and_(
                    self.model.transaction_id == transaction_id,
                    self.model.organization_id == organization_id,
                )
            ).order_by(desc(self.model.created_at)).all()
        except SQLAlchemyError as e:
            logger.error(
                f"Database error in get_by_transaction: {e}", exc_info=True
            )
            raise