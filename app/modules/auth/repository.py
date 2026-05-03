from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timedelta

from sqlalchemy import select, update, and_, or_, desc
from sqlalchemy.exc import SQLAlchemyError

from app.models.auth import User, RefreshToken, AuditLog
from app.core.database.session import db
from app.core.logging.logger import logger


# USER REPOSITORY 
class UserRepository:

    def __init__(self):
        self.model = User

    def get_by_id(self, user_id: UUID) -> Optional[User]:
        try:
            stmt = select(self.model).where(self.model.id == user_id)
            return db.session.execute(stmt).scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_id: {e}", exc_info=True)
            raise

    def get_by_email(self, email: str) -> Optional[User]:
        try:
            stmt = select(self.model).where(self.model.email == email)
            return db.session.execute(stmt).scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_email: {e}", exc_info=True)
            raise

    def get_by_phone(self, phone: str) -> Optional[User]:
        try:
            stmt = select(self.model).where(self.model.phone == phone)
            return db.session.execute(stmt).scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_by_phone: {e}", exc_info=True)
            raise

    def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Dict[str, Any] = None
    ) -> List[User]:
        try:
            stmt = select(self.model)

            if filters:
                conditions = []

                if filters.get("organization_id"):
                    conditions.append(self.model.organization_id == filters["organization_id"])

                if filters.get("role"):
                    conditions.append(self.model.role == filters["role"])

                if filters.get("is_active") is not None:
                    conditions.append(self.model.is_active == filters["is_active"])

                if filters.get("search"):
                    search = f"%{filters['search']}%"
                    conditions.append(
                        or_(
                            self.model.email.ilike(search),
                            self.model.first_name.ilike(search),
                            self.model.last_name.ilike(search),
                            self.model.phone.ilike(search),
                        )
                    )

                if conditions:
                    stmt = stmt.where(and_(*conditions))

            stmt = stmt.order_by(desc(self.model.created_at)).offset(skip).limit(limit)

            return db.session.execute(stmt).scalars().all()

        except SQLAlchemyError as e:
            logger.error(f"Database error in get_all: {e}", exc_info=True)
            raise

    def create(self, data: Dict[str, Any]) -> User:
        try:
            user = self.model(**data)
            db.session.add(user)
            db.session.commit()
            logger.info(f"Created user: {user.email}")
            return user
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create: {e}", exc_info=True)
            raise

    def update(self, user_id: UUID, data: Dict[str, Any]) -> Optional[User]:
        try:
            user = self.get_by_id(user_id)
            if not user:
                return None

            for key, value in data.items():
                if hasattr(user, key) and value is not None:
                    setattr(user, key, value)

            db.session.commit()
            logger.info(f"Updated user: {user_id}")
            return user

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update: {e}", exc_info=True)
            raise

    def delete(self, user_id: UUID) -> bool:
        try:
            user = self.get_by_id(user_id)
            if not user:
                return False

            db.session.delete(user)
            db.session.commit()
            logger.info(f"Deleted user: {user_id}")
            return True

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in delete: {e}", exc_info=True)
            raise

    def update_login_attempts(self, user_id: UUID, success: bool):
        try:
            user = self.get_by_id(user_id)
            if not user:
                return

            if success:
                user.login_attempts = 0
                user.locked_until = None
                user.last_login_at = datetime.utcnow()
            else:
                user.login_attempts += 1

                if user.login_attempts >= 5:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=30)

            db.session.commit()

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in update_login_attempts: {e}", exc_info=True)
            raise


# REFRESH TOKEN REPOSITORY 
class RefreshTokenRepository:

    def __init__(self):
        self.model = RefreshToken

    def create(self, data: Dict[str, Any]) -> RefreshToken:
        """
        Create a new refresh token record
        Expected data: user_id, token, session_id, expires_at, user_agent, ip_address, device_fingerprint
        """
        try:
            token = self.model(**data)
            db.session.add(token)
            db.session.commit()
            logger.debug(f"Created refresh token for user {token.user_id}, session: {token.session_id}")
            return token

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create refresh token: {e}", exc_info=True)
            raise

    def get_valid_token(self, token: str) -> Optional[RefreshToken]:
        """
        Get a valid (non-revoked, not expired) refresh token by token string
        """
        try:
            stmt = select(self.model).where(
                and_(
                    self.model.token == token,
                    self.model.revoked == False,
                    self.model.expires_at > datetime.utcnow()
                )
            )
            return db.session.execute(stmt).scalar_one_or_none()

        except SQLAlchemyError as e:
            logger.error(f"Database error in get_valid_token: {e}", exc_info=True)
            raise

    def get_valid_token_by_session(self, user_id: UUID, session_id: str) -> Optional[RefreshToken]:
        """
        Get a valid refresh token for a specific session
        """
        try:
            stmt = select(self.model).where(
                and_(
                    self.model.user_id == user_id,
                    self.model.session_id == session_id,
                    self.model.revoked == False,
                    self.model.expires_at > datetime.utcnow()
                )
            ).order_by(desc(self.model.created_at)).limit(1)
            
            return db.session.execute(stmt).scalar_one_or_none()

        except SQLAlchemyError as e:
            logger.error(f"Database error in get_valid_token_by_session: {e}", exc_info=True)
            raise

    def revoke_user_tokens(self, user_id: UUID):
        """
        Revoke ALL refresh tokens for a user (logout from all devices)
        """
        try:
            stmt = (
                update(self.model)
                .where(
                    self.model.user_id == user_id,
                    self.model.revoked == False
                )
                .values(
                    revoked=True,
                    revoked_at=datetime.utcnow()
                )
            )

            result = db.session.execute(stmt)
            db.session.commit()
            logger.info(f"Revoked {result.rowcount} refresh tokens for user {user_id}")

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in revoke_user_tokens: {e}", exc_info=True)
            raise

    def revoke_session_tokens(self, user_id: UUID, session_id: str):
        """
        Revoke ALL refresh tokens for a specific session (logout single device)
        """
        try:
            stmt = (
                update(self.model)
                .where(
                    self.model.user_id == user_id,
                    self.model.session_id == session_id,
                    self.model.revoked == False
                )
                .values(
                    revoked=True,
                    revoked_at=datetime.utcnow()
                )
            )

            result = db.session.execute(stmt)
            db.session.commit()
            logger.info(f"Revoked {result.rowcount} refresh tokens for user {user_id}, session {session_id}")

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in revoke_session_tokens: {e}", exc_info=True)
            raise

    def revoke_single_token(self, token_id: UUID) -> bool:
        """
        Revoke a single refresh token by ID
        """
        try:
            stmt = (
                update(self.model)
                .where(
                    self.model.id == token_id,
                    self.model.revoked == False
                )
                .values(
                    revoked=True,
                    revoked_at=datetime.utcnow()
                )
            )
            result = db.session.execute(stmt)
            db.session.commit()
            
            success = result.rowcount > 0
            if success:
                logger.info(f"Revoked single refresh token: {token_id}")
            return success

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in revoke_single_token: {e}", exc_info=True)
            raise

    def revoke_token_by_value(self, token: str) -> bool:
        """
        Revoke a refresh token by its token value
        """
        try:
            stmt = (
                update(self.model)
                .where(
                    self.model.token == token,
                    self.model.revoked == False
                )
                .values(
                    revoked=True,
                    revoked_at=datetime.utcnow()
                )
            )
            result = db.session.execute(stmt)
            db.session.commit()
            
            success = result.rowcount > 0
            if success:
                logger.info(f"Revoked refresh token by value")
            return success

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in revoke_token_by_value: {e}", exc_info=True)
            raise

    def get_active_sessions(self, user_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all active sessions for a user (for session management UI)
        """
        try:
            stmt = select(self.model).where(
                and_(
                    self.model.user_id == user_id,
                    self.model.revoked == False,
                    self.model.expires_at > datetime.utcnow()
                )
            ).order_by(desc(self.model.created_at))
            
            tokens = db.session.execute(stmt).scalars().all()
            
            # Return unique sessions (group by session_id)
            sessions = {}
            for token in tokens:
                if token.session_id and token.session_id not in sessions:
                    sessions[token.session_id] = {
                        'session_id': token.session_id,
                        'created_at': token.created_at.isoformat() if token.created_at else None,
                        'expires_at': token.expires_at.isoformat() if token.expires_at else None,
                        'user_agent': token.user_agent,
                        'ip_address': token.ip_address,
                        'device_fingerprint': token.device_fingerprint
                    }
            
            return list(sessions.values())

        except SQLAlchemyError as e:
            logger.error(f"Database error in get_active_sessions: {e}", exc_info=True)
            raise

    def cleanup_expired_tokens(self) -> int:
        """
        Delete or mark as expired old tokens (maintenance)
        """
        try:
            stmt = (
                update(self.model)
                .where(self.model.expires_at <= datetime.utcnow())
                .values(revoked=True, revoked_at=datetime.utcnow())
            )
            result = db.session.execute(stmt)
            db.session.commit()
            
            logger.info(f"Cleaned up {result.rowcount} expired refresh tokens")
            return result.rowcount

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in cleanup_expired_tokens: {e}", exc_info=True)
            raise


# AUDIT LOG REPOSITORY 
class AuditLogRepository:

    def __init__(self):
        self.model = AuditLog

    def create(self, data: Dict[str, Any]) -> AuditLog:
        try:
            log = self.model(**data)
            db.session.add(log)
            db.session.commit()
            return log

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error in create audit log: {e}", exc_info=True)
            raise

    def get_user_logs(
        self,
        user_id: UUID,
        limit: int = 100
    ) -> List[AuditLog]:
        try:
            stmt = (
                select(self.model)
                .where(self.model.user_id == user_id)
                .order_by(desc(self.model.created_at))
                .limit(limit)
            )

            return db.session.execute(stmt).scalars().all()

        except SQLAlchemyError as e:
            logger.error(f"Database error in get_user_logs: {e}", exc_info=True)
            raise