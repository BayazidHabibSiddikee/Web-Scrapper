# Linux network control: opt-in, explicit, and dry-run by default.
# Requires root only for --apply operations. Never changes state without --apply and confirmation.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: network_control.sh <command> [options]

Read-only:
  status                         Show interfaces, routes, DNS, and current MAC
  list                           List supported commands and options
  doctor                         Run the read-only network doctor

Network changes (all require --apply and interactive confirmation):
  dns <interface> <server...>     Set interface DNS using resolvectl
  dns-reset <interface>           Reset interface DNS to automatic
  mac-random <interface>          Set a locally administered random MAC
  mac-restore <interface> <mac>   Restore a specific MAC address
  dhcp <interface>                Request DHCP renewal
  static <interface> <cidr> <gateway> <nmcli-connection> [dns...]
                                 Configure a static IPv4 address through NetworkManager

Examples:
  bash scripts/network_control.sh list
  bash scripts/network_control.sh status
  bash scripts/network_control.sh dns wlan0 1.1.1.1 1.0.0.1 --dry-run
  sudo bash scripts/network_control.sh dns wlan0 1.1.1.1 1.0.0.1 --apply
  sudo bash scripts/network_control.sh mac-random wlan0 --apply
  sudo bash scripts/network_control.sh dhcp wlan0 --apply

Warnings:
  Changing the active interface can disconnect the current session.
  DHCP may remove an existing static configuration. Static mode can also break routing.
  The script never edits files under /etc automatically; use NetworkManager when available.
EOF
}

if [[ $# -eq 0 ]]; then usage; exit 2; fi
command_name=$1
shift
apply=0
dry_run=0
args=()
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --dry-run) dry_run=1 ;;
    *) args+=("$arg") ;;
  esac
done

run() {
  if [[ $dry_run -eq 1 || $apply -eq 0 ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  printf 'Applying:'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

confirm() {
  if [[ $apply -eq 0 || $dry_run -eq 1 ]]; then return 0; fi
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo 'Root privileges are required for --apply network changes.' >&2
    exit 1
  fi
  printf 'This changes the active network. Type APPLY to continue: '
  read -r answer
  [[ $answer == APPLY ]] || { echo 'Cancelled.'; exit 1; }
}

case "$command_name" in
  list)
    usage
    ;;
  status|doctor)
    bash "$(dirname "$0")/network_doctor.sh"
    ;;
  dns)
    iface=${args[0]:-}
    shift || true
    servers=("$@")
    [[ -n $iface && ${#servers[@]} -gt 0 ]] || { usage; exit 2; }
    confirm
    if command -v resolvectl >/dev/null; then
      run resolvectl dns "$iface" "${servers[@]}"
    else
      echo 'resolvectl is required for DNS changes.' >&2
      exit 1
    fi
    ;;
  dns-reset)
    iface=${args[0]:-}
    [[ -n $iface ]] || { usage; exit 2; }
    confirm
    command -v resolvectl >/dev/null || { echo 'resolvectl is required.' >&2; exit 1; }
    run resolvectl revert "$iface"
    ;;
  mac-random)
    iface=${args[0]:-}
    [[ -n $iface ]] || { usage; exit 2; }
    confirm
    run ip link set dev "$iface" down
    run ip link set dev "$iface" address "$(printf '02:%02x:%02x:%02x:%02x:%02x' "$RANDOM" "$((RANDOM%256))" "$((RANDOM%256))" "$((RANDOM%256))" "$((RANDOM%256))")"
    run ip link set dev "$iface" up
    ;;
  mac-restore)
    iface=${args[0]:-}; mac=${args[1]:-}
    [[ -n $iface && -n $mac ]] || { usage; exit 2; }
    [[ $mac =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]] || { echo 'Invalid MAC address.' >&2; exit 2; }
    confirm
    run ip link set dev "$iface" down
    run ip link set dev "$iface" address "$mac"
    run ip link set dev "$iface" up
    ;;
  dhcp)
    iface=${args[0]:-}
    [[ -n $iface ]] || { usage; exit 2; }
    confirm
    if command -v nmcli >/dev/null; then
      run nmcli device reapply "$iface"
    else
      echo 'nmcli is required for DHCP renewal on this system.' >&2
      exit 1
    fi
    ;;
  static)
    iface=${args[0]:-}; cidr=${args[1]:-}; gateway=${args[2]:-}
    [[ -n $iface && -n $cidr && -n $gateway ]] || { usage; exit 2; }
    confirm
    echo 'Static mode requires a NetworkManager connection profile; no /etc files are edited.'
    if command -v nmcli >/dev/null; then
      connection=${args[3]:?}
      dns=("${args[@]:4}")
      run nmcli con mod "$connection" ipv4.method manual ipv4.addresses "$cidr" ipv4.gateway "$gateway"
      if [[ ${#dns[@]} -gt 0 ]]; then
        run nmcli con mod "$connection" ipv4.dns "${dns[@]}"
      else
        run nmcli con mod "$connection" ipv4.ignore-auto-dns yes
      fi
      run nmcli con up "$connection"
    else
      echo 'nmcli is required for static configuration.' >&2
      exit 1
    fi
    ;;
  *)
    usage
    exit 2
    ;;
esac
