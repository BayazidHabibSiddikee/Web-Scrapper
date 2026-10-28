#!/bin/bash
# Proper network change script using your actual gateway

INTERFACE="wlan0"
NEW_MAC=$(xxd -l 6 -p /dev/urandom | sed 's/\(.\{2\}\)/\1:/g; s/:$//')
NEW_IP="192.168.0.$((RANDOM % 254 + 2))"  # Random IP in your subnet
GATEWAY="192.168.0.1"  # Your actual gateway

echo "=== Network Change ==="
echo "Interface: $INTERFACE"
echo "New IP: $NEW_IP/24"
echo "Gateway: $GATEWAY"
echo "New MAC: $NEW_MAC"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "Run with: sudo bash $0"
    exit 1
fi

# Down
ip link set $INTERFACE down
# Change MAC
ip link set $INTERFACE address $NEW_MAC
# Remove old IPs
ip addr flush dev $INTERFACE
# Add new IP
ip addr add ${NEW_IP}/24 dev $INTERFACE
# Set gateway
ip route replace default via $GATEWAY
# Up
ip link set $INTERFACE up

echo ""
echo "=== Done ==="
echo "Your new IP: $NEW_IP"
echo "Test with: curl https://httpbin.org/ip"
