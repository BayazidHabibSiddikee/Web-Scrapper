# Linux network doctor: read-only by design.
# It reports DNS, routes, interfaces, proxy variables, and egress DNS resolution.
# It does not change IP, DNS, MAC, firewall, routes, or VPN state.
set -u
printf '%s\n' '== interfaces =='
ip -brief link 2>/dev/null || true
printf '%s\n' '== routes =='
ip route 2>/dev/null || true
printf '%s\n' '== resolv.conf =='
cat /etc/resolv.conf 2>/dev/null || true
printf '%s\n' '== systemd-resolved =='
resolvectl status 2>/dev/null | head -80 || true
printf '%s\n' '== proxy environment names =='
env | grep -iE '^(http|https|all|no)_proxy=' | sed 's/=.*$/=<set>/' || true
printf '%s\n' '== public egress DNS =='
getent ahostsv4 example.com 2>/dev/null | head -5 || true
