#!/bin/sh

set -e

curl -sSf http://localhost:${SERVER_PORT:-5000}/api/health > /dev/null 2>&1 || {
    echo "Pi-hole API is not healthy. Please check the Pi-hole container."
    exit 1
}
