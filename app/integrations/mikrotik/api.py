from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import re

from app.integrations.mikrotik.client import MikroTikClient, MikroTikAPIError
from app.integrations.mikrotik.pool import PoolManager
from app.core.logging.logger import logger
from app.core.security.encryption import EncryptionService

class MikroTikAPI:
    """
    High-level MikroTik API for common ISP management operations
    """
    
    def __init__(self):
        self.client = MikroTikClient()
        self.pool_manager = PoolManager()
        self.encryption = EncryptionService()
    
    # Router Connection & Health
    
    def test_connection(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Test connection to router and return status
        """
        try:
            start_time = datetime.now()
            conn = self.client.get_connection(router_data)
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            
            # Get system info to verify connectivity
            system_info = self.client.get_router_info(router_data)
            
            return {
                'success': True,
                'connected': True,
                'response_time_ms': round(response_time, 2),
                'hostname': system_info.get('hostname'),
                'version': system_info.get('version'),
                'uptime': system_info.get('uptime'),
                'cpu_load': system_info.get('cpu_load'),
                'memory_free': system_info.get('free_memory'),
                'memory_total': system_info.get('total_memory')
            }
        except MikroTikAPIError as e:
            logger.error(f"Connection test failed for {router_data.get('ip_address')}: {e}")
            return {
                'success': False,
                'connected': False,
                'error': str(e)
            }
    
    def get_router_health(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
       
        try:
            # Get system resources
            resources = self.client.execute(router_data, '/system/resource/print')
            resource = resources[0] if resources else {}
            
            # Get interface statistics
            interfaces = self.client.get_interface_stats(router_data)
            
            # Get active connections
            hotspot_sessions = self.client.get_active_sessions(router_data)
            pppoe_sessions = self.client.get_pppoe_active_sessions(router_data)
            
            # Calculate total traffic
            total_rx = sum(int(iface.get('rx_byte', 0)) for iface in interfaces)
            total_tx = sum(int(iface.get('tx_byte', 0)) for iface in interfaces)
            
            # Identify WAN interface (usually ether1 or with gateway)
            wan_interface = None
            for iface in interfaces:
                if iface.get('name') == 'ether1' or iface.get('name') == 'wan':
                    wan_interface = iface
                    break
            
            return {
                'success': True,
                'system': {
                    'cpu_load': resource.get('cpu-load'),
                    'free_memory': resource.get('free-memory'),
                    'total_memory': resource.get('total-memory'),
                    'free_hdd': resource.get('free-hdd'),
                    'total_hdd': resource.get('total-hdd'),
                    'uptime': resource.get('uptime'),
                    'version': resource.get('version')
                },
                'network': {
                    'total_sessions': len(hotspot_sessions) + len(pppoe_sessions),
                    'hotspot_sessions': len(hotspot_sessions),
                    'pppoe_sessions': len(pppoe_sessions),
                    'total_rx_mb': round(total_rx / (1024 * 1024), 2),
                    'total_tx_mb': round(total_tx / (1024 * 1024), 2),
                    'wan_interface': {
                        'name': wan_interface.get('name') if wan_interface else None,
                        'rx_rate': wan_interface.get('rx_byte') if wan_interface else None,
                        'tx_rate': wan_interface.get('tx_byte') if wan_interface else None
                    } if wan_interface else None
                }
            }
        except Exception as e:
            logger.error(f"Failed to get router health: {e}")
            return {'success': False, 'error': str(e)}
    
    # Hotspot Management
    
    def create_hotspot_user(self, router_data: Dict[str, Any], 
                           username: str, password: str,
                           profile: str, server: str,
                           limit_uptime: str = None,
                           limit_bytes_in: int = None,
                           limit_bytes_out: int = None,
                           comment: str = None) -> Dict[str, Any]:
        """
        Create a hotspot user on the router
        """
        try:
            result = self.client.create_hotspot_user(
                router_data=router_data,
                hotspot_server_id=server,
                username=username,
                password=password,
                profile=profile,
                limit_uptime=limit_uptime,
                limit_bytes_in=limit_bytes_in,
                limit_bytes_out=limit_bytes_out,
                comment=comment
            )
            
            logger.info(f"Hotspot user created: {username} on {router_data.get('ip_address')}")
            return {
                'success': True,
                'username': username,
                'server': server,
                'profile': profile
            }
        except MikroTikAPIError as e:
            logger.error(f"Failed to create hotspot user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def disable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Disable a hotspot user
        """
        try:
            result = self.client.disable_hotspot_user(router_data, username)
            logger.info(f"Hotspot user disabled: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disable hotspot user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def enable_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Enable a hotspot user
        """
        try:
            result = self.client.enable_hotspot_user(router_data, username)
            logger.info(f"Hotspot user enabled: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to enable hotspot user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Remove a hotspot user
        """
        try:
            result = self.client.remove_hotspot_user(router_data, username)
            logger.info(f"Hotspot user removed: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to remove hotspot user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_hotspot_users(self, router_data: Dict[str, Any], 
                         server: str = None,
                         active_only: bool = False) -> List[Dict[str, Any]]:
        """
        Get all hotspot users, optionally filtered by server or active status
        """
        try:
            users = self.client.get_hotspot_users(router_data, server)
            
            if active_only:
                # Get active sessions to filter
                active_sessions = self.client.get_active_sessions(router_data, server)
                active_usernames = {s['username'] for s in active_sessions}
                users = [u for u in users if u['username'] in active_usernames]
            
            return users
        except Exception as e:
            logger.error(f"Failed to get hotspot users: {e}")
            return []
    
    def get_active_hotspot_sessions(self, router_data: Dict[str, Any],
                                     server: str = None) -> List[Dict[str, Any]]:
        """
        Get all active hotspot sessions
        """
        try:
            sessions = self.client.get_active_sessions(router_data, server)
            
            # Enrich with additional data
            for session in sessions:
                # Calculate session duration
                if session.get('uptime'):
                    # Parse uptime format: "1h2m3s"
                    uptime = session['uptime']
                    hours = minutes = seconds = 0
                    
                    hour_match = re.search(r'(\d+)h', uptime)
                    if hour_match:
                        hours = int(hour_match.group(1))
                    
                    minute_match = re.search(r'(\d+)m', uptime)
                    if minute_match:
                        minutes = int(minute_match.group(1))
                    
                    second_match = re.search(r'(\d+)s', uptime)
                    if second_match:
                        seconds = int(second_match.group(1))
                    
                    session['duration_seconds'] = hours * 3600 + minutes * 60 + seconds
                
                # Calculate data usage in MB
                session['data_usage_mb'] = round(
                    (session.get('bytes_in', 0) + session.get('bytes_out', 0)) / (1024 * 1024), 2
                )
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to get active hotspot sessions: {e}")
            return []
    
    def disconnect_hotspot_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Disconnect a hotspot user
        """
        try:
            result = self.client.disconnect_hotspot_user(router_data, username)
            logger.info(f"Hotspot user disconnected: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disconnect hotspot user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def disconnect_all_hotspot_users(self, router_data: Dict[str, Any], 
                                      server: str = None) -> Dict[str, Any]:
        """
        Disconnect all active hotspot users
        """
        try:
            sessions = self.get_active_hotspot_sessions(router_data, server)
            disconnected = []
            failed = []
            
            for session in sessions:
                result = self.disconnect_hotspot_user(router_data, session['username'])
                if result['success']:
                    disconnected.append(session['username'])
                else:
                    failed.append(session['username'])
            
            return {
                'success': len(failed) == 0,
                'disconnected': disconnected,
                'failed': failed,
                'total': len(sessions)
            }
        except Exception as e:
            logger.error(f"Failed to disconnect all hotspot users: {e}")
            return {'success': False, 'error': str(e)}
    
    # Hotspot Profiles
    
    def create_hotspot_profile(self, router_data: Dict[str, Any],
                               name: str,
                               rate_limit: str = None,
                               session_timeout: str = None,
                               idle_timeout: str = None,
                               shared_users: int = 1,
                               transparent_proxy: bool = False,
                               advertise: bool = False) -> Dict[str, Any]:
        """
        Create a hotspot user profile
        """
        try:
            params = {'name': name}
            
            if rate_limit:
                params['rate-limit'] = rate_limit
            if session_timeout:
                params['session-timeout'] = session_timeout
            if idle_timeout:
                params['idle-timeout'] = idle_timeout
            if shared_users:
                params['shared-users'] = str(shared_users)
            if transparent_proxy:
                params['transparent-proxy'] = 'yes'
            if advertise:
                params['advertise'] = 'yes'
            
            result = self.client.execute(router_data, '/ip/hotspot/user/profile/add', **params)
            logger.info(f"Hotspot profile created: {name}")
            return {'success': True, 'name': name}
        except Exception as e:
            logger.error(f"Failed to create hotspot profile: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_hotspot_profiles(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all hotspot user profiles
        """
        try:
            return self.client.get_hotspot_profiles(router_data)
        except Exception as e:
            logger.error(f"Failed to get hotspot profiles: {e}")
            return []
    
    def update_hotspot_profile(self, router_data: Dict[str, Any],
                               name: str,
                               **kwargs) -> Dict[str, Any]:
        """
        Update a hotspot user profile
        """
        try:
            result = self.client.execute(router_data, '/ip/hotspot/user/profile/set',
                                         numbers=name, **kwargs)
            logger.info(f"Hotspot profile updated: {name}")
            return {'success': True, 'name': name}
        except Exception as e:
            logger.error(f"Failed to update hotspot profile: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_hotspot_profile(self, router_data: Dict[str, Any], name: str) -> Dict[str, Any]:
        """
        Remove a hotspot user profile
        """
        try:
            result = self.client.execute(router_data, '/ip/hotspot/user/profile/remove',
                                         numbers=name)
            logger.info(f"Hotspot profile removed: {name}")
            return {'success': True, 'name': name}
        except Exception as e:
            logger.error(f"Failed to remove hotspot profile: {e}")
            return {'success': False, 'error': str(e)}
    
    # PPPoE Management
    
    def create_pppoe_secret(self, router_data: Dict[str, Any],
                            username: str, password: str,
                            profile: str, service: str = None,
                            remote_address: str = None,
                            remote_ipv6_prefix: str = None,
                            comment: str = None) -> Dict[str, Any]:
        """
        Create a PPPoE secret (user account)
        """
        try:
            result = self.client.create_pppoe_secret(
                router_data=router_data,
                username=username,
                password=password,
                profile=profile,
                service=service,
                comment=comment,
                remote_address=remote_address,
                remote_ipv6_prefix=remote_ipv6_prefix
            )
            
            logger.info(f"PPPoE secret created: {username}")
            return {
                'success': True,
                'username': username,
                'profile': profile
            }
        except MikroTikAPIError as e:
            logger.error(f"Failed to create PPPoE secret {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def disable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Disable a PPPoE secret
        """
        try:
            result = self.client.disable_pppoe_secret(router_data, username)
            logger.info(f"PPPoE secret disabled: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disable PPPoE secret {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def enable_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Enable a PPPoE secret
        """
        try:
            result = self.client.enable_pppoe_secret(router_data, username)
            logger.info(f"PPPoE secret enabled: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to enable PPPoE secret {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_pppoe_secret(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Remove a PPPoE secret
        """
        try:
            result = self.client.remove_pppoe_secret(router_data, username)
            logger.info(f"PPPoE secret removed: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to remove PPPoE secret {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_pppoe_secrets(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all PPPoE secrets
        """
        try:
            return self.client.get_pppoe_secrets(router_data)
        except Exception as e:
            logger.error(f"Failed to get PPPoE secrets: {e}")
            return []
    
    def get_active_pppoe_sessions(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all active PPPoE sessions
        """
        try:
            sessions = self.client.get_pppoe_active_sessions(router_data)
            
            # Enrich with additional data
            for session in sessions:
                # Parse uptime
                if session.get('uptime'):
                    uptime = session['uptime']
                    hours = minutes = seconds = 0
                    
                    hour_match = re.search(r'(\d+)h', uptime)
                    if hour_match:
                        hours = int(hour_match.group(1))
                    
                    minute_match = re.search(r'(\d+)m', uptime)
                    if minute_match:
                        minutes = int(minute_match.group(1))
                    
                    second_match = re.search(r'(\d+)s', uptime)
                    if second_match:
                        seconds = int(second_match.group(1))
                    
                    session['duration_seconds'] = hours * 3600 + minutes * 60 + seconds
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to get active PPPoE sessions: {e}")
            return []
    
    def disconnect_pppoe_user(self, router_data: Dict[str, Any], username: str) -> Dict[str, Any]:
        """
        Disconnect a PPPoE user
        """
        try:
            result = self.client.disconnect_pppoe_user(router_data, username)
            logger.info(f"PPPoE user disconnected: {username}")
            return {'success': True, 'username': username}
        except MikroTikAPIError as e:
            logger.error(f"Failed to disconnect PPPoE user {username}: {e}")
            return {'success': False, 'error': str(e)}
    
    # Bandwidth Management
    
    def set_simple_queue(self, router_data: Dict[str, Any],
                        name: str, target: str,
                        upload_limit: int, download_limit: int,
                        upload_burst: int = None, download_burst: int = None,
                        burst_threshold: int = None, burst_time: int = None,
                        comment: str = None) -> Dict[str, Any]:
        """
        Set a simple queue for bandwidth limiting
        """
        try:
            rate_limit = f"{download_limit}M/{upload_limit}M"
            
            if upload_burst and download_burst:
                rate_limit += f" {download_burst}M/{upload_burst}M"
                
                if burst_threshold:
                    rate_limit += f" {burst_threshold}M/{burst_threshold}M"
                
                if burst_time:
                    rate_limit += f" {burst_time}s/{burst_time}s"
            
            params = {
                'name': name,
                'target': target,
                'max-limit': rate_limit
            }
            
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/queue/simple/add', **params)
            logger.info(f"Simple queue created: {name} with limit {rate_limit}")
            return {'success': True, 'name': name, 'rate_limit': rate_limit}
        except Exception as e:
            logger.error(f"Failed to set simple queue: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_simple_queue(self, router_data: Dict[str, Any], name: str) -> Dict[str, Any]:
        """
        Remove a simple queue
        """
        try:
            result = self.client.execute(router_data, '/queue/simple/remove', numbers=name)
            logger.info(f"Simple queue removed: {name}")
            return {'success': True, 'name': name}
        except Exception as e:
            logger.error(f"Failed to remove simple queue: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_simple_queues(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all simple queues
        """
        try:
            result = self.client.execute(router_data, '/queue/simple/print')
            return result
        except Exception as e:
            logger.error(f"Failed to get simple queues: {e}")
            return []
    
    # Firewall Management
    
    def add_firewall_filter_rule(self, router_data: Dict[str, Any],
                                  chain: str, action: str,
                                  src_address: str = None,
                                  dst_address: str = None,
                                  protocol: str = None,
                                  src_port: int = None,
                                  dst_port: int = None,
                                  comment: str = None) -> Dict[str, Any]:
        """
        Add a firewall filter rule
        """
        try:
            params = {
                'chain': chain,
                'action': action
            }
            
            if src_address:
                params['src-address'] = src_address
            if dst_address:
                params['dst-address'] = dst_address
            if protocol:
                params['protocol'] = protocol
            if src_port:
                params['src-port'] = str(src_port)
            if dst_port:
                params['dst-port'] = str(dst_port)
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/ip/firewall/filter/add', **params)
            logger.info(f"Firewall rule added: {chain} -> {action}")
            return {'success': True, 'rule_id': result[0].get('ret') if result else None}
        except Exception as e:
            logger.error(f"Failed to add firewall rule: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_firewall_rule(self, router_data: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
        """
        Remove a firewall rule
        """
        try:
            result = self.client.execute(router_data, '/ip/firewall/filter/remove', numbers=rule_id)
            logger.info(f"Firewall rule removed: {rule_id}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to remove firewall rule: {e}")
            return {'success': False, 'error': str(e)}
    
    def add_nat_rule(self, router_data: Dict[str, Any],
                     chain: str, action: str,
                     src_address: str = None,
                     dst_address: str = None,
                     to_addresses: str = None,
                     to_ports: str = None,
                     comment: str = None) -> Dict[str, Any]:
        """
        Add a NAT rule
        """
        try:
            params = {
                'chain': chain,
                'action': action
            }
            
            if src_address:
                params['src-address'] = src_address
            if dst_address:
                params['dst-address'] = dst_address
            if to_addresses:
                params['to-addresses'] = to_addresses
            if to_ports:
                params['to-ports'] = to_ports
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/ip/firewall/nat/add', **params)
            logger.info(f"NAT rule added: {chain} -> {action}")
            return {'success': True, 'rule_id': result[0].get('ret') if result else None}
        except Exception as e:
            logger.error(f"Failed to add NAT rule: {e}")
            return {'success': False, 'error': str(e)}
    
    # DHCP Management
    
    def get_dhcp_leases(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all DHCP leases
        """
        try:
            result = self.client.execute(router_data, '/ip/dhcp-server/lease/print')
            return result
        except Exception as e:
            logger.error(f"Failed to get DHCP leases: {e}")
            return []
    
    def add_dhcp_lease(self, router_data: Dict[str, Any],
                       address: str, mac_address: str,
                       client_id: str = None,
                       comment: str = None) -> Dict[str, Any]:
        """
        Add a static DHCP lease
        """
        try:
            params = {
                'address': address,
                'mac-address': mac_address
            }
            
            if client_id:
                params['client-id'] = client_id
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/ip/dhcp-server/lease/add', **params)
            logger.info(f"DHCP lease added: {address} -> {mac_address}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to add DHCP lease: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_dhcp_lease(self, router_data: Dict[str, Any], lease_id: str) -> Dict[str, Any]:
        """
        Remove a DHCP lease
        """
        try:
            result = self.client.execute(router_data, '/ip/dhcp-server/lease/remove', numbers=lease_id)
            logger.info(f"DHCP lease removed: {lease_id}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to remove DHCP lease: {e}")
            return {'success': False, 'error': str(e)}
    
    # Interface Management
    
    def get_interfaces(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all interfaces with their status
        """
        try:
            return self.client.get_interface_stats(router_data)
        except Exception as e:
            logger.error(f"Failed to get interfaces: {e}")
            return []
    
    def enable_interface(self, router_data: Dict[str, Any], interface_name: str) -> Dict[str, Any]:
        """
        Enable an interface
        """
        try:
            result = self.client.execute(router_data, '/interface/enable', numbers=interface_name)
            logger.info(f"Interface enabled: {interface_name}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to enable interface: {e}")
            return {'success': False, 'error': str(e)}
    
    def disable_interface(self, router_data: Dict[str, Any], interface_name: str) -> Dict[str, Any]:
        """
        Disable an interface
        """
        try:
            result = self.client.execute(router_data, '/interface/disable', numbers=interface_name)
            logger.info(f"Interface disabled: {interface_name}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to disable interface: {e}")
            return {'success': False, 'error': str(e)}
    
    # Address Management
    
    def get_addresses(self, router_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all IP addresses configured on the router
        """
        try:
            result = self.client.execute(router_data, '/ip/address/print')
            return result
        except Exception as e:
            logger.error(f"Failed to get addresses: {e}")
            return []
    
    def add_address(self, router_data: Dict[str, Any],
                    address: str, interface: str,
                    network: str = None, comment: str = None) -> Dict[str, Any]:
        """
        Add an IP address to an interface
        """
        try:
            params = {
                'address': address,
                'interface': interface
            }
            
            if network:
                params['network'] = network
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/ip/address/add', **params)
            logger.info(f"IP address added: {address} on {interface}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to add address: {e}")
            return {'success': False, 'error': str(e)}
    
    def remove_address(self, router_data: Dict[str, Any], address_id: str) -> Dict[str, Any]:
        """
        Remove an IP address
        """
        try:
            result = self.client.execute(router_data, '/ip/address/remove', numbers=address_id)
            logger.info(f"IP address removed: {address_id}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to remove address: {e}")
            return {'success': False, 'error': str(e)}
    
    # DNS Management
    
    def get_dns_settings(self, router_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get DNS settings
        """
        try:
            result = self.client.execute(router_data, '/ip/dns/print')
            return result[0] if result else {}
        except Exception as e:
            logger.error(f"Failed to get DNS settings: {e}")
            return {}
    
    def set_dns_servers(self, router_data: Dict[str, Any], 
                        servers: List[str], allow_remote_requests: bool = True) -> Dict[str, Any]:
        """
        Set DNS servers
        """
        try:
            servers_str = ','.join(servers)
            result = self.client.execute(router_data, '/ip/dns/set',
                                         servers=servers_str,
                                         allow-remote-requests='yes' if allow_remote_requests else 'no')
            logger.info(f"DNS servers set: {servers_str}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to set DNS servers: {e}")
            return {'success': False, 'error': str(e)}
    
    # Script Management
    
    def run_script(self, router_data: Dict[str, Any], script_name: str) -> Dict[str, Any]:
        """
        Run a script on the router
        """
        try:
            result = self.client.execute(router_data, '/system/script/run', numbers=script_name)
            logger.info(f"Script executed: {script_name}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to run script: {e}")
            return {'success': False, 'error': str(e)}
    
    def add_script(self, router_data: Dict[str, Any],
                   name: str, source: str, comment: str = None) -> Dict[str, Any]:
        """
        Add a script to the router
        """
        try:
            params = {
                'name': name,
                'source': source
            }
            
            if comment:
                params['comment'] = comment
            
            result = self.client.execute(router_data, '/system/script/add', **params)
            logger.info(f"Script added: {name}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to add script: {e}")
            return {'success': False, 'error': str(e)}
    
    # Backup & Restore
    
    def backup_config(self, router_data: Dict[str, Any], name: str = None) -> Dict[str, Any]:
        """
        Backup router configuration
        """
        try:
            if not name:
                name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            result = self.client.execute(router_data, '/system/backup/save', name=name)
            logger.info(f"Backup created: {name}")
            return {'success': True, 'backup_name': name}
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return {'success': False, 'error': str(e)}
    
    def export_config(self, router_data: Dict[str, Any], 
                      section: str = None, compact: bool = True) -> Dict[str, Any]:
        """
        Export router configuration
        """
        try:
            params = {}
            if compact:
                params['compact'] = 'yes'
            
            result = self.client.execute(router_data, '/export', **params)
            
            # Join all response lines
            config = '\n'.join(result) if isinstance(result, list) else str(result)
            
            return {'success': True, 'config': config}
        except Exception as e:
            logger.error(f"Failed to export config: {e}")
            return {'success': False, 'error': str(e)}
    
    def reset_configuration(self, router_data: Dict[str, Any], 
                            no_defaults: bool = True) -> Dict[str, Any]:
        """
        Reset router configuration
        """
        try:
            params = {}
            if no_defaults:
                params['no-defaults'] = 'yes'
            
            result = self.client.execute(router_data, '/system/reset-configuration', **params)
            logger.warning(f"Router configuration reset: {router_data.get('ip_address')}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Failed to reset configuration: {e}")
            return {'success': False, 'error': str(e)}
    
    # Monitoring & Logs
    
    def get_logs(self, router_data: Dict[str, Any], 
                 topics: List[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get router logs
        """
        try:
            params = {}
            if topics:
                params['topics'] = ','.join(topics)
            if limit:
                params['limit'] = str(limit)
            
            result = self.client.execute(router_data, '/log/print', **params)
            return result
        except Exception as e:
            logger.error(f"Failed to get logs: {e}")
            return []
    
    def get_traffic_monitoring(self, router_data: Dict[str, Any],
                                interface: str = None) -> Dict[str, Any]:
        """
        Get traffic monitoring data
        """
        try:
            if interface:
                result = self.client.execute(router_data, '/interface/monitor-traffic',
                                            interface=interface, once='yes')
            else:
                result = self.client.execute(router_data, '/interface/monitor-traffic', once='yes')
            
            if result:
                return result[0] if isinstance(result, list) else result
            return {}
        except Exception as e:
            logger.error(f"Failed to get traffic monitoring: {e}")
            return {}
    
    # Bulk Operations
    
    def bulk_create_hotspot_users(self, router_data: Dict[str, Any],
                                   users: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple hotspot users in batch
        """
        results = {
            'success': [],
            'failed': [],
            'total': len(users)
        }
        
        for user in users:
            result = self.create_hotspot_user(
                router_data=router_data,
                username=user.get('username'),
                password=user.get('password'),
                profile=user.get('profile'),
                server=user.get('server'),
                limit_uptime=user.get('limit_uptime'),
                limit_bytes_in=user.get('limit_bytes_in'),
                limit_bytes_out=user.get('limit_bytes_out'),
                comment=user.get('comment')
            )
            
            if result['success']:
                results['success'].append(user.get('username'))
            else:
                results['failed'].append({
                    'username': user.get('username'),
                    'error': result.get('error')
                })
        
        logger.info(f"Bulk created {len(results['success'])}/{len(users)} hotspot users")
        return results
    
    def sync_hotspot_users(self, router_data: Dict[str, Any],
                           expected_users: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Synchronize hotspot users with expected list (create/update/delete)
        """
        current_users = self.get_hotspot_users(router_data)
        current_usernames = {u['username'] for u in current_users}
        expected_usernames = {u['username'] for u in expected_users}
        
        # Users to delete (in current but not in expected)
        to_delete = current_usernames - expected_usernames
        
        # Users to create (in expected but not in current)
        to_create = [u for u in expected_users if u['username'] not in current_usernames]
        
        # Users to update (in both)
        to_update = [u for u in expected_users if u['username'] in current_usernames]
        
        results = {
            'created': [],
            'updated': [],
            'deleted': [],
            'errors': []
        }
        
        # Create new users
        for user in to_create:
            result = self.create_hotspot_user(router_data, **user)
            if result['success']:
                results['created'].append(user['username'])
            else:
                results['errors'].append({'action': 'create', 'user': user['username'], 'error': result.get('error')})
        
        # Update existing users (simplified - just ensure enabled)
        for user in to_update:
            # Could implement more sophisticated update logic
            result = self.enable_hotspot_user(router_data, user['username'])
            if result['success']:
                results['updated'].append(user['username'])
            else:
                results['errors'].append({'action': 'update', 'user': user['username'], 'error': result.get('error')})
        
        # Delete users not in expected list
        for username in to_delete:
            result = self.remove_hotspot_user(router_data, username)
            if result['success']:
                results['deleted'].append(username)
            else:
                results['errors'].append({'action': 'delete', 'user': username, 'error': result.get('error')})
        
        logger.info(f"Sync completed: created={len(results['created'])}, updated={len(results['updated'])}, deleted={len(results['deleted'])}, errors={len(results['errors'])}")
        
        return results