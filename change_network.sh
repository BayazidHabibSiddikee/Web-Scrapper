#!/bin/bash
# Network configuration script
# Changes IP to 10.0.0.100 with new MAC

INTERFACE="wlan0"
NEW_IP="10.0.0.100"
NEW_GW="10.0.0.1"
NEW_MAC=$(xxd -l 6 -p /dev/urandom | sed 's/\(.\{2\}\)/\1:/g; s/:$//')

echo "=== Network Change Script ==="
echo "Interface: $INTERFACE"
echo "New IP: $NEW_IP/24"
echo "Gateway: $NEW_GW"
echo "New MAC: $NEW_MAC"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run with sudo:"
    echo "sudo bash $0"
    exit 1
fi

# Down interface
echo "[1/5] Bringing down $INTERFACE..."
ip link set $INTERFACE down

# Change MAC
echo "[2/5] Changing MAC to $NEW_MAC..."
ip link set $INTERFACE address $NEW_MAC

# Add new IP
echo "[3/5] Setting IP $NEW_IP/24..."
ip addr add ${NEW_IP}/24 dev $INTERFACE

# Set gateway
echo "[4/5] Setting gateway $NEW_GW..."
ip route add default via $NEW_GW 2>/dev/null || ip route replace default via $NEW_GW

# Up interface
echo "[5/5] Bringing up $INTERFACE..."
ip link set $INTERFACE up

echo ""
echo "=== Verification ==="
ip addr show $INTERFACE | grep "inet "
ip route show | grep default
echo ""
echo "MAC Address: $(ip link show $INTERFACE | grep 'link/ether' | awk '{print $2}')"
echo ""
echo "Done! Test with: curl https://httpbin.org/ip"
