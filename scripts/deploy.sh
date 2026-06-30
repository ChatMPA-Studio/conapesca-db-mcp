#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Safe deployment workflow for CONAPESCA MCP Server
#
# Usage:
#   ssh root@your-server 'cd /opt/conapesca-db-mcp && bash scripts/deploy.sh'
# =============================================================================

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

CONTAINER_NAME="conapesca-mcp"
COMPOSE_FILE="docker-compose.yml"
BACKUP_DIR="$APP_DIR/.deploy-backups"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

log "Step 1: Backing up current configuration..."
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
[ -f "$COMPOSE_FILE" ] && cp "$COMPOSE_FILE" "$BACKUP_DIR/${COMPOSE_FILE}.${TIMESTAMP}"
[ -f ".env" ]          && cp .env "$BACKUP_DIR/.env.${TIMESTAMP}"
ls -t "$BACKUP_DIR"/*.* 2>/dev/null | tail -n +11 | xargs -r rm --
log "  Backup complete"

log "Step 2: Pulling latest code from git..."
git fetch origin
git pull origin "$(git rev-parse --abbrev-ref HEAD)"
log "  Git pull complete ($(git rev-parse --short HEAD))"

log "Step 3: Rebuilding Docker image..."
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi
$COMPOSE build --no-cache
log "  Image rebuilt"

log "Step 4: Restarting container..."
$COMPOSE down
$COMPOSE up -d
log "  Container started"

log "Step 5: Waiting for container to become healthy..."
MAX_WAIT=60
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")
    case "$STATUS" in
        healthy)   log "  Container is healthy!"; break ;;
        unhealthy) fail "Container is unhealthy. Check logs: $COMPOSE logs $CONTAINER_NAME" ;;
        *)         sleep 5; ELAPSED=$((ELAPSED + 5)); echo -n "." ;;
    esac
done
echo ""
[ $ELAPSED -ge $MAX_WAIT ] && warn "Health check timed out after ${MAX_WAIT}s"

log "Step 6: Testing MCP initialize endpoint..."
PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 || echo "8000")
MCP_PATH=$(grep -E '^MCP_BASE_PATH=' .env 2>/dev/null | cut -d= -f2 || echo "/mcp")
INIT_RESPONSE=$(curl -sf -X POST "http://localhost:${PORT}${MCP_PATH}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"deploy-test","version":"1.0"}}}' \
    2>/dev/null || echo "FAILED")
echo "$INIT_RESPONSE" | grep -q '"jsonrpc"' \
    && log "  Initialize endpoint responding correctly" \
    || warn "  Unexpected response: $INIT_RESPONSE"

echo ""
log "=== Deployment Complete ==="
log "  Container: $CONTAINER_NAME"
log "  Commit:    $(git rev-parse --short HEAD)"
log "  Status:    $(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo 'unknown')"
