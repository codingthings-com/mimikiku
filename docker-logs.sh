#!/bin/bash
# Follow Mimikiku server logs
# Usage: ./docker-logs.sh [--tail N]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TAIL_FLAG=""
if [[ "$1" == "--tail" && -n "$2" ]]; then
    TAIL_FLAG="--tail $2"
fi

docker compose -f "$SCRIPT_DIR/docker/docker-compose.yml" -p mimikiku logs -f $TAIL_FLAG server
