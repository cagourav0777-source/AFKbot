#!/bin/bash
set -e

# Update system
sudo apt-get update -y
sudo apt-get upgrade -y

# Install Python 3.11 and dependencies
sudo apt-get install -y python3.11 python3.11-venv python3-pip git

# Create app directory
sudo mkdir -p /home/ubuntu/afk-bot
sudo chown ubuntu:ubuntu /home/ubuntu/afk-bot
cd /home/ubuntu/afk-bot

# Extract deployment package
if [ -f "/tmp/afk-bot-deploy.tar.gz" ]; then
    tar -xzf /tmp/afk-bot-deploy.tar.gz
    rm /tmp/afk-bot-deploy.tar.gz
elif [ -f "/tmp/afk-bot-deploy.zip" ]; then
    unzip -o /tmp/afk-bot-deploy.zip
    rm /tmp/afk-bot-deploy.zip
fi

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file with placeholders
echo "API_HASH=your_api_hash_here" > .env
echo "API_ID=your_api_id_here" >> .env
echo "BOT_TOKEN=your_bot_token_here" >> .env
echo "BOT_USERNAME=your_bot_username_here" >> .env
echo "MONGODB_URI=your_mongodb_uri_here" >> .env
echo "OWNER_ID=your_owner_id_here" >> .env
echo "PYTHON_VERSION=3.11" >> .env

# Create necessary directories
mkdir -p downloads

echo "Application setup complete"
