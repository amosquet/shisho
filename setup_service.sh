#!/bin/bash

# Shisho Service Setup Script for Root User
# This script automates the installation of the Shisho systemd service.

set -e

echo "🚀 Starting Shisho service setup..."

# 1. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# 2. Copy the service file
echo "📄 Copying shisho.service to /etc/systemd/system/..."
cp shisho.service /etc/systemd/system/

# 3. Reload systemd
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

# 4. Enable and Start the service
echo "🏗️ Enabling and starting shisho.service..."
systemctl enable shisho.service
systemctl start shisho.service

echo "✅ Setup complete! Checking service status..."
systemctl status shisho.service --no-pager
