#!/bin/bash
# ============================================================
# DIS – EC2 One-Time Setup Script
# Run this ONCE on a fresh Ubuntu 22.04 EC2 instance.
# Recommended instance: t3.medium (2 vCPU, 4GB RAM) or larger.
# ============================================================
set -e

echo "=== [1/8] System update ==="
sudo apt-get update -y
sudo apt-get upgrade -y

echo "=== [2/8] Install system dependencies ==="
sudo apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    nginx git unzip curl \
    libmagic1 poppler-utils \
    libpq-dev build-essential

echo "=== [3/8] Create DIS user ==="
sudo useradd -m -s /bin/bash dis 2>/dev/null || true
sudo mkdir -p /opt/dis
sudo chown dis:dis /opt/dis

echo "=== [4/8] Install Python packages ==="
sudo -u dis python3.11 -m venv /opt/dis/venv
sudo -u dis /opt/dis/venv/bin/pip install --upgrade pip

echo "=== [5/8] Configure Nginx ==="
sudo bash -c 'cat > /etc/nginx/sites-available/dis << EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 500M;
    proxy_read_timeout 300;
    proxy_connect_timeout 300;
    proxy_send_timeout 300;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
}
EOF'

sudo ln -sf /etc/nginx/sites-available/dis /etc/nginx/sites-enabled/dis
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "=== [6/8] Create systemd service ==="
sudo bash -c 'cat > /etc/systemd/system/dis.service << EOF
[Unit]
Description=DIS Dynamic Ingestion System
After=network.target
Wants=network-online.target

[Service]
Type=exec
User=dis
Group=dis
WorkingDirectory=/opt/dis/app
EnvironmentFile=/opt/dis/.env
ExecStart=/opt/dis/venv/bin/gunicorn main:app \
    -k uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 127.0.0.1:8000 \
    --timeout 300 \
    --access-logfile /opt/dis/logs/access.log \
    --error-logfile /opt/dis/logs/error.log \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=append:/opt/dis/logs/dis.log
StandardError=append:/opt/dis/logs/dis.log

[Install]
WantedBy=multi-user.target
EOF'

sudo mkdir -p /opt/dis/logs
sudo chown -R dis:dis /opt/dis/logs
sudo systemctl daemon-reload
sudo systemctl enable dis

echo "=== [7/8] Set up log rotation ==="
sudo bash -c 'cat > /etc/logrotate.d/dis << EOF
/opt/dis/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    postrotate
        systemctl reload dis
    endscript
}
EOF'

echo "=== [8/8] Open firewall ports ==="
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # HTTP
sudo ufw allow 443/tcp    # HTTPS (when you add SSL)
sudo ufw --force enable

echo ""
echo "========================================="
echo "EC2 setup COMPLETE."
echo "Next step: run ./deploy/deploy.sh"
echo "========================================="
