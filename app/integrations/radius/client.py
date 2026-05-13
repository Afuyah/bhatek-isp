import socket
import struct
import hashlib
import random
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from flask import current_app

from app.core.logging.logger import logger
from app.integrations.radius.radius_cache import RadiusCache

class RadiusPacket:
   # Packet types
    ACCESS_REQUEST = 1
    ACCESS_ACCEPT = 2
    ACCESS_REJECT = 3
    ACCOUNTING_REQUEST = 4
    ACCOUNTING_RESPONSE = 5
    ACCESS_CHALLENGE = 11
    COA_REQUEST = 43
    COA_ACK = 44
    COA_NAK = 45
    
    # Attributes
    USER_NAME = 1
    USER_PASSWORD = 2
    CHAP_PASSWORD = 3
    NAS_IP_ADDRESS = 4
    NAS_PORT = 5
    SERVICE_TYPE = 6
    FRAMED_PROTOCOL = 7
    FRAMED_IP_ADDRESS = 8
    FRAMED_IP_NETMASK = 9
    FRAMED_ROUTING = 10
    FILTER_ID = 11
    FRAMED_MTU = 12
    FRAMED_COMPRESSION = 13
    LOGIN_IP_HOST = 14
    LOGIN_SERVICE = 15
    LOGIN_TCP_PORT = 16
    REPLY_MESSAGE = 18
    CALLBACK_NUMBER = 19
    CALLBACK_ID = 20
    FRAMED_ROUTE = 22
    FRAMED_IPX_NETWORK = 23
    STATE = 24
    CLASS = 25
    VENDOR_SPECIFIC = 26
    SESSION_TIMEOUT = 27
    IDLE_TIMEOUT = 28
    TERMINATION_ACTION = 29
    CALLED_STATION_ID = 30
    CALLING_STATION_ID = 31
    NAS_IDENTIFIER = 32
    PROXY_STATE = 33
    LOGIN_LAT_SERVICE = 34
    LOGIN_LAT_NODE = 35
    LOGIN_LAT_GROUP = 36
    FRAMED_APPLETALK_LINK = 37
    FRAMED_APPLETALK_NETWORK = 38
    FRAMED_APPLETALK_ZONE = 39
    ACCT_STATUS_TYPE = 40
    ACCT_DELAY_TIME = 41
    ACCT_INPUT_OCTETS = 42
    ACCT_OUTPUT_OCTETS = 43
    ACCT_SESSION_ID = 44
    ACCT_AUTHENTIC = 45
    ACCT_SESSION_TIME = 46
    ACCT_INPUT_PACKETS = 47
    ACCT_OUTPUT_PACKETS = 48
    ACCT_TERMINATE_CAUSE = 49
    ACCT_MULTI_SESSION_ID = 50
    ACCT_LINK_COUNT = 51
    EVENT_TIMESTAMP = 55
    NAS_PORT_TYPE = 61
    TUNNEL_TYPE = 64
    TUNNEL_MEDIUM_TYPE = 65
    TUNNEL_CLIENT_ENDPOINT = 66
    TUNNEL_SERVER_ENDPOINT = 67
    CONNECT_INFO = 77
    NAS_PORT_ID = 87
    FRAMED_INTERFACE_ID = 96
    FRAMED_IPV6_PREFIX = 97
    DELEGATED_IPV6_PREFIX = 123
    MIKROTIK_RECV_LIMIT = 1
    MIKROTIK_XMIT_LIMIT = 2
    MIKROTIK_GROUP = 3
    MIKROTIK_WIRELESS_ENCODING = 4
    MIKROTIK_WIRELESS_ENC_KEY = 5
    MIKROTIK_WIRELESS_FORWARD = 6
    MIKROTIK_RATE_LIMIT = 7

class RadiusClient:
    """RADIUS client for communication with FreeRADIUS server"""
    
    def __init__(self, host: str = 'localhost', port: int = 1812, secret: str = 'testing123'):
        self.host = host
        self.port = port
        self.secret = secret.encode()
        self.socket = None
        self.timeout = 10
    
    def connect(self):
        """Create UDP socket"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(self.timeout)
    
    def close(self):
        """Close socket"""
        if self.socket:
            self.socket.close()
    
    def authenticate(self, username: str, password: str, nas_ip: str = None, 
                     nas_port: int = 0, calling_station_id: str = None,
                     called_station_id: str = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Authenticate user via RADIUS
        
        Returns: (success, attributes)
        """
        try:
            self.connect()
            
            # Create packet
            packet_id = random.randint(1, 255)
            attributes = []
            
            # Add required attributes
            attributes.append(self._make_attribute(RadiusPacket.USER_NAME, username))
            attributes.append(self._make_attribute(RadiusPacket.USER_PASSWORD, password, encrypt=True))
            
            if nas_ip:
                attributes.append(self._make_attribute(RadiusPacket.NAS_IP_ADDRESS, nas_ip))
            
            if nas_port:
                attributes.append(self._make_attribute(RadiusPacket.NAS_PORT, str(nas_port)))
            
            if calling_station_id:
                attributes.append(self._make_attribute(RadiusPacket.CALLING_STATION_ID, calling_station_id))
            
            if called_station_id:
                attributes.append(self._make_attribute(RadiusPacket.CALLED_STATION_ID, called_station_id))
            
            # Add NAS identifier
            attributes.append(self._make_attribute(RadiusPacket.NAS_IDENTIFIER, socket.gethostname()))
            
            # Add service type
            attributes.append(self._make_attribute(RadiusPacket.SERVICE_TYPE, '2'))  # Framed
            
            # Add framed protocol
            attributes.append(self._make_attribute(RadiusPacket.FRAMED_PROTOCOL, '1'))  # PPP
            
            # Build packet
            packet = self._build_packet(RadiusPacket.ACCESS_REQUEST, packet_id, attributes)
            
            # Send request
            self.socket.sendto(packet, (self.host, self.port))
            
            # Receive response
            response, _ = self.socket.recvfrom(4096)
            
            # Parse response
            response_code, response_id, response_attributes = self._parse_packet(response)
            
            if response_code == RadiusPacket.ACCESS_ACCEPT:
                # Parse vendor-specific attributes for MikroTik
                radius_attrs = self._parse_attributes(response_attributes)
                
                # Get MikroTik rate limit if present
                rate_limit = radius_attrs.get('MikroTik-Rate-Limit')
                
                return True, {
                    'session_timeout': radius_attrs.get('Session-Timeout'),
                    'idle_timeout': radius_attrs.get('Idle-Timeout'),
                    'rate_limit': rate_limit,
                    'reply_message': radius_attrs.get('Reply-Message'),
                    'class': radius_attrs.get('Class')
                }
            elif response_code == RadiusPacket.ACCESS_REJECT:
                return False, {'error': 'Access rejected'}
            else:
                return False, {'error': f'Unexpected response: {response_code}'}
                
        except socket.timeout:
            logger.error("RADIUS authentication timeout")
            return False, {'error': 'Timeout'}
        except Exception as e:
            logger.error(f"RADIUS authentication error: {e}", exc_info=True)
            return False, {'error': str(e)}
        finally:
            self.close()
    
    def accounting(self, username: str, session_id: str, status_type: str,
                   input_octets: int = 0, output_octets: int = 0,
                   session_time: int = 0, terminate_cause: str = None,
                   nas_ip: str = None, nas_port: int = 0,
                   calling_station_id: str = None,
                   called_station_id: str = None) -> bool:
        """
        Send accounting record to RADIUS
        
        status_type: 'start', 'stop', 'interim-update', 'alive'
        """
        try:
            self.connect()
            
            # Map status type to RADIUS code
            status_map = {
                'start': 1,
                'stop': 2,
                'interim-update': 3,
                'alive': 3
            }
            acct_status = status_map.get(status_type, 1)
            
            packet_id = random.randint(1, 255)
            attributes = []
            
            # Add attributes
            attributes.append(self._make_attribute(RadiusPacket.USER_NAME, username))
            attributes.append(self._make_attribute(RadiusPacket.ACCT_STATUS_TYPE, str(acct_status)))
            attributes.append(self._make_attribute(RadiusPacket.ACCT_SESSION_ID, session_id))
            
            if nas_ip:
                attributes.append(self._make_attribute(RadiusPacket.NAS_IP_ADDRESS, nas_ip))
            
            if nas_port:
                attributes.append(self._make_attribute(RadiusPacket.NAS_PORT, str(nas_port)))
            
            if calling_station_id:
                attributes.append(self._make_attribute(RadiusPacket.CALLING_STATION_ID, calling_station_id))
            
            if called_station_id:
                attributes.append(self._make_attribute(RadiusPacket.CALLED_STATION_ID, called_station_id))
            
            if input_octets:
                attributes.append(self._make_attribute(RadiusPacket.ACCT_INPUT_OCTETS, str(input_octets)))
            
            if output_octets:
                attributes.append(self._make_attribute(RadiusPacket.ACCT_OUTPUT_OCTETS, str(output_octets)))
            
            if session_time:
                attributes.append(self._make_attribute(RadiusPacket.ACCT_SESSION_TIME, str(session_time)))
            
            if terminate_cause:
                terminate_cause_map = {
                    'user_request': 1,
                    'lost_carrier': 2,
                    'lost_service': 3,
                    'idle_timeout': 4,
                    'session_timeout': 5,
                    'admin_reset': 6,
                    'admin_reboot': 7,
                    'port_error': 8,
                    'nas_error': 9,
                    'nas_request': 10,
                    'nas_reboot': 11,
                    'port_unneeded': 12,
                    'port_preempted': 13,
                    'port_suspended': 14,
                    'service_unavailable': 15,
                    'callback': 16,
                    'user_error': 17,
                    'host_request': 18
                }
                cause_code = terminate_cause_map.get(terminate_cause, 1)
                attributes.append(self._make_attribute(RadiusPacket.ACCT_TERMINATE_CAUSE, str(cause_code)))
            
            # Add NAS identifier
            attributes.append(self._make_attribute(RadiusPacket.NAS_IDENTIFIER, socket.gethostname()))
            
            # Add event timestamp
            attributes.append(self._make_attribute(RadiusPacket.EVENT_TIMESTAMP, str(int(datetime.now().timestamp()))))
            
            # Build and send packet
            packet = self._build_packet(RadiusPacket.ACCOUNTING_REQUEST, packet_id, attributes)
            self.socket.sendto(packet, (self.host, self.port))
            
            # Receive response
            response, _ = self.socket.recvfrom(4096)
            response_code, _, _ = self._parse_packet(response)
            
            success = response_code == RadiusPacket.ACCOUNTING_RESPONSE
            if success:
                logger.info(f"RADIUS accounting {status_type} for {username}")
            else:
                logger.warning(f"RADIUS accounting failed: {response_code}")
            
            return success
            
        except Exception as e:
            logger.error(f"RADIUS accounting error: {e}", exc_info=True)
            return False
        finally:
            self.close()
    
    def disconnect_user(self, username: str, nas_ip: str, session_id: str = None) -> bool:
        """
        Send CoA (Change of Authorization) to disconnect user
        """
        try:
            self.connect()
            
            packet_id = random.randint(1, 255)
            attributes = []
            
            # Add attributes
            attributes.append(self._make_attribute(RadiusPacket.USER_NAME, username))
            attributes.append(self._make_attribute(RadiusPacket.NAS_IP_ADDRESS, nas_ip))
            
            if session_id:
                attributes.append(self._make_attribute(RadiusPacket.ACCT_SESSION_ID, session_id))
            
            # Add disconnect command (Vendor-Specific for MikroTik)
            # This is a simplified version - actual implementation depends on NAS capabilities
            
            packet = self._build_packet(RadiusPacket.COA_REQUEST, packet_id, attributes)
            self.socket.sendto(packet, (self.host, self.port))
            
            response, _ = self.socket.recvfrom(4096)
            response_code, _, _ = self._parse_packet(response)
            
            success = response_code == RadiusPacket.COA_ACK
            if success:
                logger.info(f"User {username} disconnected via CoA")
            else:
                logger.warning(f"CoA disconnect failed: {response_code}")
            
            return success
            
        except Exception as e:
            logger.error(f"CoA disconnect error: {e}", exc_info=True)
            return False
        finally:
            self.close()
    
    def _make_attribute(self, attr_type: int, value: str, encrypt: bool = False) -> bytes:
        """Create RADIUS attribute"""
        if encrypt:
            # Encrypt password using RADIUS shared secret
            value = self._encrypt_password(value)
        
        value_bytes = value.encode() if isinstance(value, str) else value
        length = len(value_bytes) + 2
        return struct.pack('!BB', attr_type, length) + value_bytes
    
    def _encrypt_password(self, password: str) -> bytes:
        """Encrypt password using RADIUS shared secret"""
        # This is a simplified implementation
        # Full RADIUS password encryption uses MD5 with shared secret
        import hashlib
        
        encrypted = b''
        last = b'\x00' * 16
        
        for i in range(0, len(password), 16):
            chunk = password[i:i+16].encode()
            md5 = hashlib.md5()
            md5.update(self.secret)
            md5.update(last)
            key = md5.digest()
            
            encrypted_chunk = bytes(a ^ b for a, b in zip(chunk, key[:len(chunk)]))
            encrypted += encrypted_chunk
            last = encrypted_chunk + b'\x00' * (16 - len(encrypted_chunk))
        
        return encrypted
    
    def _build_packet(self, code: int, packet_id: int, attributes: List[bytes]) -> bytes:
        """Build RADIUS packet"""
        # Calculate length (20 bytes header + attributes)
        length = 20 + sum(len(attr) for attr in attributes)
        
        # Build header
        header = struct.pack('!BBH', code, packet_id, length)
        
        # Add placeholder for authenticator (will be filled after)
        authenticator = b'\x00' * 16
        header += authenticator
        
        # Build packet body
        body = b''.join(attributes)
        
        # Calculate authenticator (MD5 of code + id + length + secret + body)
        import hashlib
        md5 = hashlib.md5()
        md5.update(header[:4])  # Code + ID + Length
        md5.update(self.secret)
        md5.update(body)
        authenticator = md5.digest()
        
        # Replace placeholder with actual authenticator
        packet = header[:4] + authenticator + body
        
        return packet
    
    def _parse_packet(self, packet: bytes) -> Tuple[int, int, List[bytes]]:
        """Parse RADIUS packet"""
        code, packet_id, length = struct.unpack('!BBH', packet[:4])
        authenticator = packet[4:20]
        body = packet[20:length]
        
        # Verify authenticator
        import hashlib
        md5 = hashlib.md5()
        md5.update(packet[:4])
        md5.update(self.secret)
        md5.update(body)
        
        if md5.digest() != authenticator:
            logger.warning("RADIUS packet authenticator mismatch")
        
        # Parse attributes
        attributes = []
        pos = 0
        while pos < len(body):
            attr_type = body[pos]
            attr_length = body[pos + 1]
            attr_value = body[pos + 2:pos + attr_length]
            attributes.append((attr_type, attr_length, attr_value))
            pos += attr_length
        
        return code, packet_id, attributes
    
    def _parse_attributes(self, attributes: List[Tuple[int, int, bytes]]) -> Dict[str, Any]:
        """Parse RADIUS attributes into dictionary"""
        result = {}
        
        for attr_type, attr_length, attr_value in attributes:
            if attr_type == RadiusPacket.SESSION_TIMEOUT:
                result['Session-Timeout'] = int(attr_value)
            elif attr_type == RadiusPacket.IDLE_TIMEOUT:
                result['Idle-Timeout'] = int(attr_value)
            elif attr_type == RadiusPacket.REPLY_MESSAGE:
                result['Reply-Message'] = attr_value.decode()
            elif attr_type == RadiusPacket.CLASS:
                result['Class'] = attr_value
            elif attr_type == RadiusPacket.VENDOR_SPECIFIC:
                # Parse MikroTik vendor-specific attributes
                vendor_id = struct.unpack('!I', attr_value[:4])[0]
                if vendor_id == 14988:  # MikroTik vendor ID
                    vendor_data = attr_value[4:]
                    vendor_pos = 0
                    while vendor_pos < len(vendor_data):
                        vendor_type = vendor_data[vendor_pos]
                        vendor_length = vendor_data[vendor_pos + 1]
                        vendor_value = vendor_data[vendor_pos + 2:vendor_pos + vendor_length]
                        
                        if vendor_type == RadiusPacket.MIKROTIK_RATE_LIMIT:
                            result['MikroTik-Rate-Limit'] = vendor_value.decode()
                        elif vendor_type == RadiusPacket.MIKROTIK_GROUP:
                            result['MikroTik-Group'] = vendor_value.decode()
                        
                        vendor_pos += vendor_length
        
        return result