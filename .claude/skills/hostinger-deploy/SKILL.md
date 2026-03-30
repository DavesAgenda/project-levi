---
name: hostinger-deploy
description: Docker deployment to Hostinger KVM1 — Dockerfile, docker-compose, Hostinger API, GitHub Actions, reverse proxy
metadata:
  internal: false
---

# Hostinger Deployment Patterns

Tony's skill for deploying and managing the church budget tool on Hostinger KVM1 VPS.

## Hostinger API

**Docs**: https://developers.hostinger.com/
**SDKs**: Python, TypeScript, PHP
**Auth**: API token from Hostinger dashboard (Account Settings → API)

### Key Capabilities
- VPS management (list, restart, manage settings)
- Firewall configuration
- SSH key management
- Backup and snapshot management

### Docker Manager API (Experimental)
Hostinger's Docker Manager allows programmatic docker-compose deployment:
- Deploy from GitHub/GitLab repos or direct compose file contents
- List all projects on a VM
- Get container details (status, ports, runtime config)
- Delete projects
- **Status**: Experimental — check https://developers.hostinger.com/ for current availability

### Hostinger MCP Server
```bash
npm install -g hostinger-api-mcp
```
- AI-assisted VPS management via Model Context Protocol
- Supports Docker container management, DNS, deployments
- Requires Node.js 24+

### GitHub Actions Integration
```yaml
# .github/workflows/deploy.yml
- uses: hostinger/deploy-on-vps@v1
  with:
    host: ${{ secrets.VPS_HOST }}
    username: ${{ secrets.VPS_USER }}
    key: ${{ secrets.VPS_SSH_KEY }}
    script: |
      cd /opt/church-budget
      docker compose pull
      docker compose up -d
      docker compose exec app python -c "import httpx; print('Health OK')"
```

## Dockerfile

```dockerfile
# Multi-stage build for Python 3.11 + FastAPI
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ ./app/
USER nobody
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## docker-compose.yml

```yaml
services:
  budget-app:
    build: .
    image: ghcr.io/valid-agenda/church-budget:latest
    container_name: church-budget
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"    # Localhost only — Caddy proxies
    volumes:
      - ./data:/app/data          # Git data repo (actuals, budgets, config)
    environment:
      - XERO_CLIENT_ID_FILE=/run/secrets/xero_client_id
      - XERO_CLIENT_SECRET_FILE=/run/secrets/xero_client_secret
    secrets:
      - xero_client_id
      - xero_client_secret
    networks:
      - church-net
    mem_limit: 512m
    cpus: 0.5
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"]
      interval: 30s
      timeout: 5s
      retries: 3

networks:
  church-net:
    external: true    # Shared with n8n if inter-service comms needed

secrets:
  xero_client_id:
    file: ./secrets/xero_client_id.txt
  xero_client_secret:
    file: ./secrets/xero_client_secret.txt
```

## Reverse Proxy (Caddy)

```
# Caddyfile addition
budget.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Caddy handles automatic HTTPS via Let's Encrypt. Budget app only listens on localhost:8000.

## Deployment Pipeline

```
git push main
  → GitHub Actions triggers
  → Build Docker image, tag with git SHA + latest
  → Push to GHCR (GitHub Container Registry)
  → SSH to Hostinger VPS (or Hostinger deploy action)
  → docker compose pull && docker compose up -d
  → Health check: GET /health returns 200
  → On failure: notify via n8n webhook
```

## Rollback

```bash
# On VPS
docker compose pull ghcr.io/valid-agenda/church-budget:{previous-tag}
docker compose up -d
```

Tag every release with version number — never rely solely on `latest`.

## Resource Constraints

This VPS is shared with n8n:
- Budget app: 512MB RAM, 0.5 CPU (configurable)
- Monitor with `docker stats`
- If VPS is a KVM1 (likely 1-2GB RAM), keep total usage under 80%

## n8n Integration

n8n on the same VPS can:
- Hit `http://church-budget:8000/api/sync` to trigger Xero pull
- Monitor `http://church-budget:8000/health` for uptime alerts
- Send notifications on sync completion or variance alerts
- Communication via shared Docker network (`church-net`)

## Security (Coordinate with Kryptonite)
- App runs as non-root user (`nobody`)
- Only port 8000 exposed, and only to localhost
- Secrets via Docker secrets, not env vars
- Caddy terminates TLS
- Docker network isolation between services
