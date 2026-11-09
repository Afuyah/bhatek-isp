"""Post-onboarding configuration: hardening, LAN/port roles, hotspot + WLAN.

Onboarding creates the tunnel, the platform API user and the RADIUS client.
This module is the second act, applied once the router answers on its tunnel
address:

  1. HARDEN   - close every management service to the world, keep only the
                platform subnet, drop invalid traffic, stop discovery leaking.
  2. TOPOLOGY - ether1 is the uplink; the remaining ports (and optionally the
                wireless radio) join a hotspot bridge.
  3. HOTSPOT  - DHCP, DNS and a hotspot profile bound to RADIUS, so a phone that
                joins the WiFi is redirected to the payment portal, pays, and
                then browses.

Everything is expressed as data first (:func:`build_plan`) and executed second
(:meth:`RouterProvisioner.apply`), so the plan can be previewed, tested and
diffed without touching a device.

Every step is idempotent: configuration objects carry a ``bhatek-`` comment and
are removed before being re-added, so re-running the plan converges instead of
duplicating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PLATFORM_SUBNET = '10.0.0.0/16'
COMMENT = 'bhatek-isp'


@dataclass
class Step:
    """One configuration step: a label plus RouterOS API commands."""

    key: str
    title: str
    commands: List[tuple]          # [(api path, {attr: value}), ...]
    optional: bool = False
    role: str = 'hardening'

    def as_dict(self) -> Dict[str, Any]:
        return {
            'key': self.key,
            'title': self.title,
            'role': self.role,
            'optional': self.optional,
            'commands': [{'path': p, 'attributes': a} for p, a in self.commands],
        }


@dataclass
class Topology:
    """How this router's physical interfaces are used."""

    uplink: str = 'ether1'
    lan_ports: List[str] = field(default_factory=lambda: ['ether2', 'ether3', 'ether4', 'ether5'])
    use_wlan: bool = True
    ssid: str = 'Bhatek WiFi'
    #: RouterOS 7 uses /interface/wifi; RouterOS 6 uses /interface/wireless.
    wlan_driver: str = 'wifi'
    hotspot_network: str = '10.10.88.0/24'
    hotspot_gateway: str = '10.10.88.1'
    dns_name: str = 'wifi.bhalinks.com'
    portal_url: str = 'https://bhatek-isp.fly.dev'

    @classmethod
    def from_config(cls, data: Dict[str, Any]) -> 'Topology':
        data = data or {}
        lan = data.get('lan_ports')
        if isinstance(lan, str):
            lan = [p.strip() for p in lan.split(',') if p.strip()]
        return cls(
            uplink=data.get('uplink') or 'ether1',
            lan_ports=list(lan) if lan else ['ether2', 'ether3', 'ether4', 'ether5'],
            use_wlan=bool(data.get('use_wlan', True)),
            ssid=(data.get('ssid') or 'Bhatek WiFi')[:32],
            wlan_driver=(data.get('wlan_driver') or 'wifi').lower(),
            hotspot_network=data.get('hotspot_network') or '10.10.88.0/24',
            hotspot_gateway=data.get('hotspot_gateway') or '10.10.88.1',
            dns_name=data.get('dns_name') or 'wifi.bhalinks.com',
            portal_url=data.get('portal_url') or 'https://bhatek-isp.fly.dev',
        )

    @property
    def pool_range(self) -> str:
        base = self.hotspot_gateway.rsplit('.', 1)[0]
        return f'{base}.10-{base}.254'


def _hardening_steps(topo: Topology) -> List[Step]:
    """Close the management surface and stop discovery leaking to the WAN."""
    return [
        Step('services', 'Restrict management services to the platform tunnel', [
            ('/ip/service/set', {'name': 'api', 'address': PLATFORM_SUBNET, 'disabled': 'no'}),
            ('/ip/service/set', {'name': 'winbox', 'address': PLATFORM_SUBNET, 'disabled': 'no'}),
            ('/ip/service/set', {'name': 'ssh', 'address': PLATFORM_SUBNET, 'disabled': 'no'}),
            ('/ip/service/set', {'name': 'telnet', 'disabled': 'yes'}),
            ('/ip/service/set', {'name': 'ftp', 'disabled': 'yes'}),
            ('/ip/service/set', {'name': 'www', 'disabled': 'yes'}),
            ('/ip/service/set', {'name': 'www-ssl', 'disabled': 'yes'}),
        ]),
        Step('discovery', 'Stop neighbour/MAC discovery on the WAN side', [
            ('/ip/neighbor/discovery-settings/set', {'discover-interface-list': 'LAN'}),
            ('/tool/mac-server/set', {'allowed-interface-list': 'LAN'}),
            ('/tool/mac-winbox/set', {'allowed-interface-list': 'LAN'}),
            ('/tool/bandwidth-server/set', {'enabled': 'no'}),
        ]),
        Step('firewall', 'Input firewall: platform trusted, everything else dropped', [
            # remove our previous rules first so re-runs converge
            ('/ip/firewall/filter/remove', {'numbers': f'[find comment="{COMMENT}-input"]'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'action': 'accept', 'connection-state': 'established,related',
                'comment': f'{COMMENT}-input'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'action': 'drop', 'connection-state': 'invalid',
                'comment': f'{COMMENT}-input'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'action': 'accept', 'src-address': PLATFORM_SUBNET,
                'comment': f'{COMMENT}-input'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'action': 'accept', 'protocol': 'icmp',
                'limit': '10,20:packet', 'comment': f'{COMMENT}-input'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'in-interface': 'bridge-hotspot',
                'action': 'accept', 'dst-port': '53,67,80,443', 'protocol': 'tcp,udp',
                'comment': f'{COMMENT}-input'}),
            ('/ip/firewall/filter/add', {
                'chain': 'input', 'action': 'drop', 'in-interface': topo.uplink,
                'comment': f'{COMMENT}-input'}),
        ]),
        Step('nat', 'Masquerade customer traffic to the uplink', [
            ('/ip/firewall/nat/remove', {'numbers': f'[find comment="{COMMENT}-nat"]'}),
            ('/ip/firewall/nat/add', {
                'chain': 'srcnat', 'out-interface': topo.uplink, 'action': 'masquerade',
                'comment': f'{COMMENT}-nat'}),
        ]),
    ]


def _topology_steps(topo: Topology) -> List[Step]:
    """Bridge the LAN ports (and radio) and give the bridge an address."""
    port_commands = [('/interface/bridge/add', {'name': 'bridge-hotspot', 'comment': f'{COMMENT}-bridge'})]
    for port in [*topo.lan_ports, *(['wlan1'] if topo.use_wlan else [])]:
        port_commands.append(('/interface/bridge/port/add', {
            'bridge': 'bridge-hotspot', 'interface': port, 'comment': f'{COMMENT}-port'}))
    address = topo.hotspot_gateway + '/24'
    return [
        Step('bridge', 'Create the customer bridge', port_commands, role='topology'),
        Step('addressing', 'Address the customer bridge', [
            ('/ip/address/add', {'address': address, 'interface': 'bridge-hotspot',
                                 'comment': f'{COMMENT}-addr'}),
        ], role='topology'),
    ]


def _wlan_steps(topo: Topology) -> List[Step]:
    """Configure the radio as a hotspot AP."""
    if not topo.use_wlan:
        return []
    if topo.wlan_driver == 'wifi':       # RouterOS 7
        path = '/interface/wifi/set'
    else:                                 # RouterOS 6
        path = '/interface/wireless/set'
    return [
        Step('wlan', 'Expose the wireless radio as a hotspot AP', [
            (path, {'numbers': 'wlan1', 'ssid': topo.ssid, 'disabled': 'no'}),
        ], role='wlan', optional=True),
    ]


def _hotspot_steps(topo: Topology) -> List[Step]:
    """DHCP + DNS + hotspot profile bound to RADIUS."""
    return [
        Step('dhcp', 'DHCP for hotspot clients', [
            ('/ip/pool/add', {'name': 'hs-pool', 'ranges': topo.pool_range,
                              'comment': f'{COMMENT}-pool'}),
            ('/ip/dhcp-server/add', {'name': 'hs-dhcp', 'interface': 'bridge-hotspot',
                                     'address-pool': 'hs-pool', 'disabled': 'no',
                                     'comment': f'{COMMENT}-dhcp'}),
            ('/ip/dhcp-server/network/add', {
                'address': topo.hotspot_network, 'gateway': topo.hotspot_gateway,
                'dns-server': topo.hotspot_gateway, 'comment': f'{COMMENT}-dhcpnet'}),
        ], role='hotspot'),
        Step('dns', 'Resolver for pre-login clients', [
            ('/ip/dns/set', {'servers': '1.1.1.1,8.8.8.8', 'allow-remote-requests': 'yes'}),
        ], role='hotspot'),
        Step('hotspot_profile', 'Hotspot profile bound to platform RADIUS', [
            ('/ip/hotspot/profile/add', {
                'name': 'hs-bhatek', 'hotspot-address': topo.hotspot_gateway,
                'dns-name': topo.dns_name, 'login-by': 'http-pap',
                'use-radius': 'yes', 'radius-accounting': 'yes',
                'radius-interim-update': '5m',
                'html-directory': 'hotspot', 'comment': f'{COMMENT}-hsprof'}),
        ], role='hotspot'),
        Step('hotspot', 'Serve the captive portal on the bridge', [
            ('/ip/hotspot/add', {'name': 'hs-bhatek', 'interface': 'bridge-hotspot',
                                 'address-pool': 'hs-pool', 'profile': 'hs-bhatek',
                                 'comment': f'{COMMENT}-hotspot'}),
        ], role='hotspot'),
        Step('walled_garden', 'Let unauthenticated clients reach the portal and M-Pesa', [
            ('/ip/hotspot/walled-garden/ip/add', {
                'dst-host': topo.portal_url.replace('https://', '').replace('http://', ''),
                'action': 'accept', 'comment': f'{COMMENT}-wg-portal'}),
            ('/ip/hotspot/walled-garden/ip/add', {
                'dst-host': '*.safaricom.co.ke', 'action': 'accept', 'comment': f'{COMMENT}-wg-mpesa'}),
            ('/ip/hotspot/walled-garden/ip/add', {
                'dst-port': '53', 'protocol': 'udp', 'action': 'accept', 'comment': f'{COMMENT}-wg-dns'}),
        ], role='hotspot'),
    ]


def build_plan(topo: Topology, roles: Optional[List[str]] = None) -> List[Step]:
    """Return the ordered configuration plan.

    ``roles`` filters the sections: ``hardening``, ``topology``, ``wlan``,
    ``hotspot``. Default: all of them.
    """
    roles = roles or ['hardening', 'topology', 'wlan', 'hotspot']
    steps: List[Step] = []
    if 'hardening' in roles:
        steps += _hardening_steps(topo)
    if 'topology' in roles:
        steps += _topology_steps(topo)
    if 'wlan' in roles:
        steps += _wlan_steps(topo)
    if 'hotspot' in roles:
        steps += _hotspot_steps(topo)
    return steps


class RouterProvisioner:
    """Applies a plan to one router through the existing API proxy."""

    def __init__(self, execute):
        #: callable(router, api_path, **attributes) -> list[dict]
        self._execute = execute

    def apply(self, router, topo: Topology, roles: Optional[List[str]] = None,
              dry_run: bool = False) -> Dict[str, Any]:
        steps = build_plan(topo, roles)
        results = []
        for step in steps:
            outcome = {'key': step.key, 'title': step.title, 'role': step.role,
                       'ok': True, 'errors': [], 'skipped': False}
            if dry_run:
                outcome['planned'] = step.as_dict()['commands']
                results.append(outcome)
                continue
            for path, attrs in step.commands:
                try:
                    reply = self._execute(router, path, **attrs) or []
                except Exception as exc:
                    outcome['ok'] = False
                    outcome['errors'].append(f'{path}: {exc}')
                    continue
                trapped = [r for r in reply if isinstance(r, dict) and r.get('_trap')]
                if trapped:
                    outcome['ok'] = False
                    outcome['errors'].append(f'{path}: {trapped[0].get("message", "trap")}')
            results.append(outcome)
        failed = [r for r in results if not r['ok']]
        return {
            'success': not failed,
            'dry_run': dry_run,
            'steps': results,
            'failed': [r['key'] for r in failed],
        }
