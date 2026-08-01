#!/bin/sh

set -e

curl -sSf http://localhost:5000/api/health > /dev/null 2>&1 || {
    echo "Service is not healthy. Please check the dns-user sidecar."
    exit 1
}
