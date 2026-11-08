#!/bin/bash
# Start Mimikiku Docker services
# Usage: ./docker-start.sh [--build]
# Telemetry goes to Olly (docker/compose.olly.yml) when its olly_network exists.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "No .env file yet. Create one from the example, then fill it in:"
    echo ""
    echo "    cp .env.example .env"
    echo ""
    echo "At least set MIMIKIKU_ADMIN_EMAIL and MIMIKIKU_ADMIN_PASSWORD (your first admin)."
    echo "See README.md, Quick start."
    exit 1
fi

BUILD_FLAG=""
if [[ "$1" == "--build" ]]; then
    BUILD_FLAG="--build"
fi

FILES=(-f "$SCRIPT_DIR/docker/docker-compose.yml")
if docker network inspect olly_network >/dev/null 2>&1; then
    FILES+=(-f "$SCRIPT_DIR/docker/compose.olly.yml")
    if grep -qE '^OLLY_OTLP_URL=[[:space:]]*$' "$SCRIPT_DIR/docker/.env" 2>/dev/null; then
        echo "Telemetry off: OLLY_OTLP_URL is empty in docker/.env."
    else
        echo "Telemetry on: sending to olly-collector (olly_network). Set OLLY_OTLP_URL= in docker/.env to turn it off."
    fi
fi

docker compose "${FILES[@]}" -p mimikiku up -d $BUILD_FLAG
