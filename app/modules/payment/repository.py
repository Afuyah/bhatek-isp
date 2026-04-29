from typing import Optional, Dict, Any, List
from uuid import UUID
from sqlalchemy import and_, desc
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

from app.models.payment import Transaction, PaymentAccount
from app.core.database.session import db
from app.core.logging.logger import logger

class TransactionRepository:
    """Data access layer for Transaction operations"""
    
    def __init__(self):
        self.model = Transaction
    
    def get_by_id(self, transaction_id: UUID) -> Optional[Transaction]:
        try:
            return self.model.query.filter_by(id=transaction_id).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise
    
    def get_by_reference(self, reference: str) -> Optional[Transaction]:
        try:
            return self.model.query.filter_by(transaction_reference=reference).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_reference: {e}", exc_info=True)
            raise
    
    def get_by_checkout_id(self, checkout_id: str) -> Optional[Transaction]:
        try:
            return self.model.query.filter(
                self.model.payment_details['checkout_request_id'].astext == checkout_id
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_checkout_id: {e}", exc_info=True)
            raise
    
    def get_by_organization(self, organization_id: UUID, skip: int = 0, limit: int = 100,
                            subscriber_id: UUID = None, status: str = None) -> List[Transaction]:
        try:
            query = self.model.query.filter_by(organization_id=organization_id)
            
            if subscriber_id:
                query = query.filter_by(subscriber_id=subscriber_id)
            if status:
                query = query.filter_by(status=status)
            
            return query.order_by(desc(self.model.created_at)).offset(skip).limit(limit).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_organization: {e}", exc_info=True)
            raise
    
    def count_by_organization(self, organization_id: UUID, subscriber_id: UUID = None, status: str = None) -> int:
        try:
            query = self.model.query.filter_by(organization_id=organization_id)
            if subscriber_id:
                query = query.filter_by(subscriber_id=subscriber_id)
            if status:
                query = query.filter_by(status=status)
            return query.count()
        except SQLAlchemyError as e:
            logger.error(f"Database error in count_by_organization: {e}", exc_info=True)
            raise
    
    def create(self, data: Dict[str, Any]) -> Transaction:
        try:
            transaction = self.model(**data)
            db.session.add(transaction)
            db.session.commit()
            logger.info(f"Created transaction: {transaction.transaction_reference}")
            return transaction
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise
    
    def update(self, transaction_id: UUID, data: Dict[str, Any]) -> Optional[Transaction]:
        try:
            transaction = self.get_by_id(transaction_id)
            if not transaction:
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

class PaymentAccountRepository:
    """Data access layer for PaymentAccount operations"""
    
    def __init__(self):
        self.model = PaymentAccount
    
    def get_default(self, organization_id: UUID) -> Optional[PaymentAccount]:
        try:
            return self.model.query.filter_by(
                organization_id=organization_id,
                is_default=True,
                is_active=True
            ).first()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_default: {e}", exc_info=True)
            raise