#!/bin/bash
# ============================================================
# DIS – Deploy to EC2
# Usage: ./deploy/deploy.sh <EC2_PUBLIC_IP> <PEM_KEY_PATH>
# Example: ./deploy/deploy.sh 52.12.34.56 ~/.ssh/my-key.pem
# ============================================================
set -e

EC2_IP="${1:?Usage: $0 <EC2_IP> <PEM_KEY>}"
PEM_KEY="${2:?Usage: $0 <EC2_IP> <PEM_KEY>}"
EC2_USER="ubuntu"
REMOTE_DIR="/opt/dis"
APP_DIR="$REMOTE_DIR/app"

echo "=== Deploying DIS to EC2: $EC2_IP ==="

SSH="ssh -i $PEM_KEY -o StrictHostKeyChecking=no $EC2_USER@$EC2_IP"
SCP="scp -i $PEM_KEY -o StrictHostKeyChecking=no"

# 1. Package the app (exclude virtualenv and cache)
echo "[1/6] Packaging app..."
TMPZIP="/tmp/dis_deploy_$(date +%s).zip"
zip -r "$TMPZIP" . \
    --exclude "*.pyc" \
    --exclude "*/__pycache__/*" \
    --exclude "venv/*" \
    --exclude ".git/*" \
    --exclude "*.zip"

# 2. Upload to EC2
echo "[2/6] Uploading to EC2..."
$SCP "$TMPZIP" "$EC2_USER@$EC2_IP:/tmp/dis_deploy.zip"
rm "$TMPZIP"

# 3. Deploy on EC2
echo "[3/6] Deploying on server..."
$SSH "bash -s" << 'REMOTE'
    set -e
    sudo mkdir -p /opt/dis/app
    sudo chown -R ubuntu:ubuntu /opt/dis/app

    # Backup current if exists
    if [ -d "/opt/dis/app/main.py" ]; then
        sudo mv /opt/dis/app "/opt/dis/app_backup_$(date +%s)"
    fi

    # Extract new code
    cd /opt/dis/app
    unzip -o /tmp/dis_deploy.zip
    rm /tmp/dis_deploy.zip

    # Fix ownership
    sudo chown -R dis:dis /opt/dis/app
REMOTE

# 4. Upload .env file if it exists locally
if [ -f ".env" ]; then
    echo "[4/6] Uploading .env..."
    $SCP .env "$EC2_USER@$EC2_IP:/tmp/dis.env"
    $SSH "sudo mv /tmp/dis.env /opt/dis/.env && sudo chown dis:dis /opt/dis/.env && sudo chmod 600 /opt/dis/.env"
else
    echo "[4/6] No local .env found — make sure /opt/dis/.env exists on server"
fi

# 5. Install Python dependencies
echo "[5/6] Installing dependencies..."
$SSH "sudo -u dis /opt/dis/venv/bin/pip install -r /opt/dis/app/requirements.txt --quiet"

# 6. Restart service
echo "[6/6] Restarting DIS service..."
$SSH "sudo systemctl restart dis && sleep 3 && sudo systemctl status dis --no-pager"

echo ""
echo "========================================"
echo "Deployment COMPLETE!"
echo "API:    http://$EC2_IP/"
echo "Health: http://$EC2_IP/health"
echo "Docs:   http://$EC2_IP/docs"
echo "Logs:   ssh -i $PEM_KEY ubuntu@$EC2_IP 'sudo journalctl -u dis -f'"
echo "========================================"
