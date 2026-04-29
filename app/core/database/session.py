from flask_sqlalchemy import SQLAlchemy
from contextlib import contextmanager
from app.core.logging.logger import logger

db = SQLAlchemy()

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations"""
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database session error: {e}", exc_info=True)
        raise
    finally:
        db.session.close()