#!/bin/sh

set -e

curl -sSf http://localhost:80/api/info/client > /dev/null 2>&1 || {
    echo "Pi-hole API is not healthy. Please check the Pi-hole container."
    exit 1
}
