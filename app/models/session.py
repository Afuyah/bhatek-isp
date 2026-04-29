from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, BigInteger, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, MACADDR, INET
from sqlalchemy.orm import relationship

from app.core.database.base import  BaseModel
from app.core.database.mixins import OrganizationMixin

class ActiveSession(BaseModel, OrganizationMixin):
    __tablename__ = 'active_sessions'
    
    subscriber_id = Column(UUID(as_uuid=True), ForeignKey('subscribers.id'))
    voucher_id = Column(UUID(as_uuid=True), ForeignKey('vouchers.id'))
    subscription_id = Column(UUID(as_uuid=True), ForeignKey('subscriptions.id'))
    router_id = Column(UUID(as_uuid=True), ForeignKey('routers.id'))
    hotspot_server_id = Column(UUID(as_uuid=True), ForeignKey('hotspot_servers.id'))
    pppoe_server_id = Column(UUID(as_uuid=True), ForeignKey('pppoe_servers.id'))
    access_point_id = Column(UUID(as_uuid=True), ForeignKey('access_points.id'))
    
    session_type = Column(String(20), nullable=False)  # hotspot, pppoe
    session_id = Column(String(255), nullable=False, index=True)
    username = Column(String(255), nullable=False)
    device_mac = Column(MACADDR, index=True)
    ip_address = Column(INET)
    nas_identifier = Column(String(255))
    framed_ip_address = Column(INET)
    called_station_id = Column(String(255))  # AP MAC/SSID
    calling_station_id = Column(String(255))  # Client MAC
    
    start_time = Column(DateTime, nullable=False, index=True)
    last_update = Column(DateTime, nullable=False)
    expiry_time = Column(DateTime, nullable=False, index=True)
    
    bytes_in = Column(BigInteger, default=0)
    bytes_out = Column(BigInteger, default=0)
    session_time = Column(Integer, default=0)
    
    status = Column(String(20), default='active', index=True)
    termination_cause = Column(String(100))
    
    # Relationships
    subscriber = relationship('Subscriber', back_populates='active_sessions')
    voucher = relationship('Voucher')
    subscription = relationship('Subscription', back_populates='active_sessions')
    router = relationship('Router')
    access_point = relationship('AccessPoint', back_populates='active_sessions')
    
    __table_args__ = (
        Index('idx_session_subscriber', 'subscriber_id', 'status'),
        Index('idx_session_router', 'router_id', 'status'),
        Index('idx_session_ap', 'access_point_id', 'status'),
        Index('idx_session_device', 'device_mac', 'status'),
        Index('idx_session_expiry', 'expiry_time'),
    )
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'session_type': self.session_type,
            'username': self.username,
            'device_mac': self.device_mac,
            'ip_address': str(self.ip_address) if self.ip_address else None,
            'start_time': self.start_time.isoformat(),
            'expiry_time': self.expiry_time.isoformat(),
            'bytes_in': self.bytes_in,
            'bytes_out': self.bytes_out,
            'session_time': self.session_time,
            'status': self.status
        }

class RadiusAccounting(BaseModel):
    __tablename__ = 'radius_accounting'
    
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    username = Column(String(255), nullable=False, index=True)
    nas_ip_address = Column(INET)
    framed_ip_address = Column(INET)
    called_station_id = Column(String(255))
    calling_station_id = Column(String(255))
    acct_status_type = Column(String(50))
    acct_start_time = Column(DateTime, index=True)
    acct_stop_time = Column(DateTime)
    acct_input_octets = Column(BigInteger)
    acct_output_octets = Column(BigInteger)
    acct_session_time = Column(Integer)
    acct_terminate_cause = Column(String(100))
    acct_unique_id = Column(String(255))
    
    __table_args__ = (
        Index('idx_radius_session', 'session_id'),
        Index('idx_radius_username', 'username'),
        Index('idx_radius_start', 'acct_start_time'),
    )