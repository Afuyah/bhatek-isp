from sqlalchemy import Column, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.database.session import db
import uuid

class BaseModel(db.Model):
    __abstract__ = True
    
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,  
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    def to_dict(self, exclude: set = None, include_relationships: bool = False):
        """Convert model to dictionary"""
        exclude = exclude or set()
        exclude.add('_sa_instance_state')
        
        result = {}
        for column in self.__table__.columns:
            if column.name not in exclude:
                value = getattr(self, column.name)
                if isinstance(value, uuid.UUID):
                    value = str(value)
                elif hasattr(value, 'isoformat'):
                    value = value.isoformat()
                result[column.name] = value
        
        if include_relationships:
            for rel_name in self.__mapper__.relationships.keys():
                if rel_name not in exclude:
                    rel_value = getattr(self, rel_name)
                    if rel_value:
                        if isinstance(rel_value, list):
                            result[rel_name] = [item.to_dict() for item in rel_value]
                        else:
                            result[rel_name] = rel_value.to_dict()
        
        return result
    
    def __repr__(self):
        return f"<{self.__class__.__name__} {self.id}>"