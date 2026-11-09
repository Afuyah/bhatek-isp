#!/usr/bin/env bash
# Manage WireGuard peers for the bhatek-isp platform.
#
# Called by the application over SSH:
#   wg-manage.sh add <public-key> <ip>[/prefix]
#   wg-manage.sh remove <public-key>
#   wg-manage.sh list
#
# The caller may pass either "10.0.1.10" or "10.0.1.10/32"; the prefix is
# stripped and re-applied here so the config can never end up as "10.0.1.10/32/32"
# (which makes `wg syncconf` reject the entire file and silently drops every peer).
set -euo pipefail

IFACE="${WG_IFACE:-wg0}"
CONF="/etc/wireguard/${IFACE}.conf"
ACTION="${1:-}"
shift || true

apply() {
  # syncconf applies peers without tearing the interface down, so existing
  # tunnels are not interrupted.
  wg syncconf "$IFACE" <(wg-quick strip "$IFACE")
}

case "$ACTION" in
  add)
    PUB="${1:?public key required}"
    RAW_IP="${2:?ip required}"
    IP="${RAW_IP%%/*}"          # drop any prefix length supplied by the caller
    if ! [[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
      echo "invalid ipv4 address: $RAW_IP" >&2
      exit 2
    fi

    if grep -qF "PublicKey = ${PUB}" "$CONF"; then
      # Refresh the address if it changed, rather than duplicating the block.
      python3 - "$CONF" "$PUB" "$IP" <<'PY'
import re, sys
conf, pub, ip = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(conf).read()
pattern = re.compile(
    r'(PublicKey = ' + re.escape(pub) + r'\nAllowedIPs = )[^\n]*', re.M)
text, n = pattern.subn(lambda m: m.group(1) + ip + '/32', text)
open(conf, 'w').write(text)
print(f"updated {n} peer(s) to {ip}/32")
PY
    else
      printf '\n[Peer]\n# added %s\nPublicKey = %s\nAllowedIPs = %s/32\n' \
        "$(date -u +%FT%TZ)" "$PUB" "$IP" >> "$CONF"
      echo "added ${PUB} -> ${IP}/32"
    fi
    apply
    echo "peers now: $(wg show "$IFACE" peers | wc -l)"
    ;;
  remove)
    PUB="${1:?public key required}"
    python3 - "$CONF" "$PUB" <<'PY'
import re, sys

conf, pub = sys.argv[1], sys.argv[2]
text = open(conf).read()
head, *blocks = re.split(r'(?m)^\[Peer\]\s*$', text)
kept = [b for b in blocks if pub not in b]
new = head.rstrip('\n') + '\n'
for b in kept:
    new += '\n[Peer]' + b.rstrip('\n') + '\n'
open(conf, 'w').write(new)
print(f"removed {len(blocks) - len(kept)} peer block(s)")
PY
    apply
    ;;
  list)
    wg show "$IFACE"
    ;;
  *)
    echo "usage: $0 {add <pubkey> <ip>|remove <pubkey>|list}" >&2
    exit 2
    ;;
esac
