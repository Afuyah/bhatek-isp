"""Post-onboarding provisioning: hardening, topology, WLAN and hotspot."""

import pytest

from app.integrations.mikrotik.provisioner import (
    PLATFORM_SUBNET, RouterProvisioner, Topology, build_plan,
)


def _paths(steps):
    return [cmd[0] for step in steps for cmd in step.commands]


def _attrs(steps, path):
    return [cmd[1] for step in steps for cmd in step.commands if cmd[0] == path]


def test_hardening_restricts_management_to_the_tunnel():
    plan = build_plan(Topology(), roles=['hardening'])
    service_sets = {a['name']: a for a in _attrs(plan, '/ip/service/set')}
    assert service_sets['api']['address'] == PLATFORM_SUBNET
    assert service_sets['winbox']['address'] == PLATFORM_SUBNET
    assert service_sets['ssh']['address'] == PLATFORM_SUBNET
    for closed in ('telnet', 'ftp', 'www', 'www-ssl'):
        assert service_sets[closed]['disabled'] == 'yes'


def test_firewall_trusts_platform_and_drops_wan_input():
    plan = build_plan(Topology(uplink='ether9'), roles=['hardening'])
    rules = _attrs(plan, '/ip/firewall/filter/add')
    assert any(r.get('src-address') == PLATFORM_SUBNET and r['action'] == 'accept' for r in rules)
    assert any(r.get('in-interface') == 'ether9' and r['action'] == 'drop' for r in rules)
    assert any(r.get('connection-state') == 'invalid' and r['action'] == 'drop' for r in rules)


def test_nat_uses_the_configured_uplink():
    plan = build_plan(Topology(uplink='sfp1'), roles=['hardening'])
    nat = _attrs(plan, '/ip/firewall/nat/add')
    assert nat and nat[0]['out-interface'] == 'sfp1'


def test_every_hardening_rule_carries_a_comment_for_idempotency():
    plan = build_plan(Topology(), roles=['hardening'])
    for step in plan:
        if step.key != 'nat':
            for path, attrs in step.commands:
                if path.endswith('/add'):
                    assert attrs.get('comment', '').startswith('bhatek-isp'), path


def test_bridge_joins_lan_ports_and_radio():
    topo = Topology(lan_ports=['ether2', 'ether3'], use_wlan=True)
    plan = build_plan(topo, roles=['topology'])
    ports = [a['interface'] for a in _attrs(plan, '/interface/bridge/port/add')]
    assert ports == ['ether2', 'ether3', 'wlan1']


def test_ethernet_only_when_wlan_disabled():
    topo = Topology(lan_ports=['ether2', 'ether3'], use_wlan=False)
    plan = build_plan(topo, roles=['topology', 'wlan'])
    ports = [a['interface'] for a in _attrs(plan, '/interface/bridge/port/add')]
    assert ports == ['ether2', 'ether3']
    assert '/interface/wifi/set' not in _paths(plan)


def test_wlan_driver_depends_on_routeros_version():
    v7 = build_plan(Topology(wlan_driver='wifi'), roles=['wlan'])
    v6 = build_plan(Topology(wlan_driver='wireless'), roles=['wlan'])
    assert '/interface/wifi/set' in _paths(v7)
    assert '/interface/wireless/set' in _paths(v6)
    assert _attrs(v7, '/interface/wifi/set')[0]['ssid']


def test_hotspot_profile_is_radius_bound():
    plan = build_plan(Topology(), roles=['hotspot'])
    profile = _attrs(plan, '/ip/hotspot/profile/add')[0]
    assert profile['use-radius'] == 'yes'
    assert profile['radius-accounting'] == 'yes'
    assert profile['login-by'] == 'http-pap'


def test_walled_garden_allows_portal_and_mpesa():
    plan = build_plan(Topology(portal_url='https://bhat.bhalinks.com'), roles=['hotspot'])
    hosts = [a.get('dst-host') for a in _attrs(plan, '/ip/hotspot/walled-garden/ip/add')]
    assert 'bhat.bhalinks.com' in hosts
    assert '*.safaricom.co.ke' in hosts


def test_pool_range_follows_the_gateway():
    topo = Topology(hotspot_gateway='10.20.5.1')
    assert topo.pool_range == '10.20.5.10-10.20.5.254'


def test_apply_reports_traps_as_failures():
    calls = []

    def execute(router, path, **attrs):
        calls.append((path, attrs))
        if path == '/ip/service/set' and attrs.get('name') == 'ftp':
            return [{'_trap': True, 'message': 'no such service'}]
        return [{'ret': 'ok'}]

    result = RouterProvisioner(execute).apply(object(), Topology(), roles=['hardening'])
    assert result['success'] is False
    assert 'services' in result['failed']
    assert calls


def test_dry_run_makes_no_device_calls():
    calls = []
    result = RouterProvisioner(lambda *a, **k: calls.append(1)).apply(
        object(), Topology(), roles=['hardening'], dry_run=True)
    assert calls == []
    assert result['success'] is True
    assert result['steps'][0]['planned']


def test_dry_run_endpoint_requires_a_tunnel(client):
    """Preview is available over the API and never touches the device."""
    creds = {'email': 'admin@acme.test', 'password': 'Passw0rd!x'}
    token = client.post('/api/v1/auth/login', json=creds).get_json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    created = client.post('/api/v1/routers',
                          json={'description': 'Provisioning preview'},
                          headers=headers).get_json()

    response = client.get(f"/api/v1/routers/{created['router']['id']}/provision-plan",
                          headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    plan = response.get_json()['plan']
    assert plan['dry_run'] is True
    assert any(step['key'] == 'firewall' for step in plan['steps'])
    assert plan['topology']['uplink'] == 'ether1'
