#!/bin/bash
# Stop Mimikiku Docker services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker compose -f "$SCRIPT_DIR/docker/docker-compose.yml" -p mimikiku down
