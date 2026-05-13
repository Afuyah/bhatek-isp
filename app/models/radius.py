"""
RADIUS Database Models for FreeRADIUS integration
These models map to FreeRADIUS tables
"""
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin


class RadCheck(BaseModel, OrganizationMixin):
    """RADIUS authentication table (radcheck)"""
    __tablename__ = 'radcheck'
    
    username = Column(String(64), nullable=False, index=True)
    attribute = Column(String(64), nullable=False)
    op = Column(String(2), nullable=False, default=':=')
    value = Column(String(253), nullable=False)
    
    __table_args__ = (
        Index('idx_radcheck_username', 'username'),
        Index('idx_radcheck_org', 'organization_id'),
    )


class RadReply(BaseModel, OrganizationMixin):
    """RADIUS reply attributes table (radreply)"""
    __tablename__ = 'radreply'
    
    username = Column(String(64), nullable=False, index=True)
    attribute = Column(String(64), nullable=False)
    op = Column(String(2), nullable=False, default=':=')
    value = Column(String(253), nullable=False)
    
    __table_args__ = (
        Index('idx_radreply_username', 'username'),
        Index('idx_radreply_org', 'organization_id'),
    )


class RadUserGroup(BaseModel, OrganizationMixin):
    """RADIUS user group table (radusergroup)"""
    __tablename__ = 'radusergroup'
    
    username = Column(String(64), nullable=False, index=True)
    groupname = Column(String(64), nullable=False)
    priority = Column(Integer, default=1)
    
    __table_args__ = (
        Index('idx_radusergroup_username', 'username'),
        Index('idx_radusergroup_org', 'organization_id'),
    )


class RadAcct(BaseModel, OrganizationMixin):
    """RADIUS accounting table (radacct)"""
    __tablename__ = 'radacct'
    
    radacctid = Column(Integer, primary_key=True, autoincrement=True)
    acct_session_id = Column(String(64), nullable=False, index=True)
    acct_unique_id = Column(String(32), unique=True, index=True)
    username = Column(String(64), nullable=False, index=True)
    groupname = Column(String(64))
    realm = Column(String(64))
    nas_ip_address = Column(String(15), nullable=False)
    nas_port_id = Column(Integer)
    nas_port_type = Column(String(32))
    acct_start_time = Column(DateTime)
    acct_stop_time = Column(DateTime)
    acct_session_time = Column(Integer)
    acct_input_octets = Column(Integer)
    acct_output_octets = Column(Integer)
    called_station_id = Column(String(50))
    calling_station_id = Column(String(50))
    acct_terminate_cause = Column(String(32))
    service_type = Column(String(32))
    framed_protocol = Column(String(32))
    framed_ip_address = Column(String(15))
    
    __table_args__ = (
        Index('idx_radacct_username', 'username'),
        Index('idx_radacct_session_id', 'acct_session_id'),
        Index('idx_radacct_unique_id', 'acct_unique_id'),
        Index('idx_radacct_org', 'organization_id'),
        Index('idx_radacct_start_time', 'acct_start_time'),
    )
