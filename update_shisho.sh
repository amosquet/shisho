#!/bin/bash

# Shisho Update Script
# This script pulls the latest changes from GitHub and restarts the service.

set -e

echo "Searching for updates..."

# 1. Pull latest changes
echo "📥 Pulling latest changes from GitHub..."
git pull

# 2. Sync dependencies (in case pyproject.toml changed)
echo "📦 Syncing environment with uv..."
uv sync

# 3. Restart the service
echo "🔄 Restarting shisho.service..."
systemctl restart shisho.service

echo "✅ Update complete! Service restarted."
systemctl status shisho.service --no-pager
