from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, BigInteger, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, MACADDR, INET
from sqlalchemy.orm import relationship

from app.core.database.base import BaseModel
from app.core.database.mixins import OrganizationMixin


class ActiveSession(BaseModel, OrganizationMixin):
    __tablename__ = 'active_sessions'
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'), nullable=True)
    voucher_id = Column(UUID(as_uuid=True), ForeignKey('vouchers.id'), nullable=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'), nullable=True)
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id'), nullable=True)
    hotspot_server_id = Column(UUID(as_uuid=True), ForeignKey('hotspot_servers.id'), nullable=True)
    pppoe_server_id = Column(UUID(as_uuid=True), ForeignKey('pppoe_servers.id'), nullable=True)
    access_point_id = Column(UUID(as_uuid=True), ForeignKey('access_points.id'), nullable=True, index=True)
    
    session_type = Column(String(20), nullable=False)  # hotspot, pppoe
    session_id = Column(String(255), nullable=False, index=True)
    username = Column(String(255), nullable=False)
    device_mac = Column(MACADDR, nullable=True, index=True)
    ip_address = Column(INET, nullable=True)
    nas_identifier = Column(String(255), nullable=True)
    framed_ip_address = Column(INET, nullable=True)
    called_station_id = Column(String(255), nullable=True)  # AP MAC/SSID
    calling_station_id = Column(String(255), nullable=True)  # Client MAC
    
    start_time = Column(DateTime, nullable=False, index=True)
    last_update = Column(DateTime, nullable=False)
    expiry_time = Column(DateTime, nullable=False, index=True)
    
    bytes_in = Column(BigInteger, default=0)
    bytes_out = Column(BigInteger, default=0)
    session_time = Column(Integer, default=0)
    
    status = Column(String(20), default='active', index=True)
    termination_cause = Column(String(100), nullable=True)
    
    # Relationships - one-way only (no back_populates to avoid circular dependencies)
    subscriber = relationship('Subscriber', foreign_keys=[subscriber_id])
    voucher = relationship('Voucher', foreign_keys=[voucher_id])
    subscription = relationship('Subscription', foreign_keys=[subscription_id])
    router = relationship('Router', foreign_keys=[router_id])
    hotspot_server = relationship('HotspotServer', foreign_keys=[hotspot_server_id])
    pppoe_server = relationship('PPPoeServer', foreign_keys=[pppoe_server_id])
    access_point = relationship('AccessPoint', foreign_keys=[access_point_id])  # No back_populates
    
    __table_args__ = (
        Index('idx_session_subscriber', 'subscriber_id', 'status'),
        Index('idx_session_router', 'router_id', 'status'),
        Index('idx_session_ap', 'access_point_id', 'status'),
        Index('idx_session_device', 'device_mac', 'status'),
        Index('idx_session_expiry', 'expiry_time'),
        Index('idx_session_org', 'organization_id'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'session_type': self.session_type,
            'username': self.username,
            'device_mac': self.device_mac,
            'ip_address': str(self.ip_address) if self.ip_address else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'expiry_time': self.expiry_time.isoformat() if self.expiry_time else None,
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'session_time': self.session_time,
            'status': self.status,
            'access_point_id': str(self.access_point_id) if self.access_point_id else None,
            'router_id': str(self.router_id) if self.router_id else None,
        }


class RadiusAccounting(BaseModel):
    __tablename__ = 'radius_accounting'
    
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    username = Column(String(255), nullable=False, index=True)
    nas_ip_address = Column(INET, nullable=True)
    framed_ip_address = Column(INET, nullable=True)
    called_station_id = Column(String(255), nullable=True)
    calling_station_id = Column(String(255), nullable=True)
    acct_status_type = Column(String(50), nullable=True)
    acct_start_time = Column(DateTime, nullable=True, index=True)
    acct_stop_time = Column(DateTime, nullable=True)
    acct_input_octets = Column(BigInteger, default=0)
    acct_output_octets = Column(BigInteger, default=0)
    acct_session_time = Column(Integer, default=0)
    acct_terminate_cause = Column(String(100), nullable=True)
    acct_unique_id = Column(String(255), nullable=True, unique=True)
    
    __table_args__ = (
        Index('idx_radius_session', 'session_id'),
        Index('idx_radius_username', 'username'),
        Index('idx_radius_start', 'acct_start_time'),
        Index('idx_radius_org', 'organization_id'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'organization_id': str(self.organization_id),
            'session_id': self.session_id,
            'username': self.username,
            'nas_ip_address': str(self.nas_ip_address) if self.nas_ip_address else None,
            'framed_ip_address': str(self.framed_ip_address) if self.framed_ip_address else None,
            'acct_status_type': self.acct_status_type,
            'acct_start_time': self.acct_start_time.isoformat() if self.acct_start_time else None,
            'acct_stop_time': self.acct_stop_time.isoformat() if self.acct_stop_time else None,
            'acct_input_octets': self.acct_input_octets,
            'acct_output_octets': self.acct_output_octets,
            'acct_session_time': self.acct_session_time,
            'acct_terminate_cause': self.acct_terminate_cause,
            'acct_unique_id': self.acct_unique_id,
        }