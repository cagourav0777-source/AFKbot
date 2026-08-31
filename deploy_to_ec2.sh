#!/bin/bash

# EC2 Deployment Script for AFK Telegram Bot
# This script deploys the bot to an EC2 instance and sets up systemd service

set -e

# Configuration
REGION="ap-south-1"
PROFILE="default"
INSTANCE_TYPE="t3.micro"
KEY_NAME="afk-bot-key"  # You'll need to create this key pair first
SECURITY_GROUP_NAME="afk-bot-sg"
APP_DIR="/home/ubuntu/afk-bot"
SERVICE_NAME="afk-bot"

echo "🚀 Starting EC2 deployment for AFK Telegram Bot..."

# Check if AWS CLI is configured
if ! aws sts get-caller-identity --profile $PROFILE --region $REGION &>/dev/null; then
    echo "❌ AWS CLI not configured or profile '$PROFILE' not found"
    echo "Please run: aws configure --profile $PROFILE"
    exit 1
fi

echo "✅ AWS credentials verified"

# Create key pair if it doesn't exist
if ! aws ec2 describe-key-pairs --key-names $KEY_NAME --profile $PROFILE --region $REGION &>/dev/null; then
    echo "🔑 Creating new key pair: $KEY_NAME"
    aws ec2 create-key-pair \
        --key-name $KEY_NAME \
        --query 'KeyMaterial' \
        --output text \
        --profile $PROFILE \
        --region $REGION > ${KEY_NAME}.pem
    chmod 400 ${KEY_NAME}.pem
    echo "✅ Key pair created and saved to ${KEY_NAME}.pem"
else
    echo "✅ Key pair $KEY_NAME already exists"
fi

# Create security group if it doesn't exist
if ! aws ec2 describe-security-groups --group-names $SECURITY_GROUP_NAME --profile $PROFILE --region $REGION &>/dev/null; then
    echo "🔒 Creating security group: $SECURITY_GROUP_NAME"
    GROUP_ID=$(aws ec2 create-security-group \
        --group-name $SECURITY_GROUP_NAME \
        --description "Security group for AFK Telegram Bot" \
        --profile $PROFILE \
        --region $REGION \
        --query 'GroupId' \
        --output text)
    
    # Allow SSH
    aws ec2 authorize-security-group-ingress \
        --group-id $GROUP_ID \
        --protocol tcp \
        --port 22 \
        --cidr 0.0.0.0/0 \
        --profile $PROFILE \
        --region $REGION
    
    # Allow Flask server port
    aws ec2 authorize-security-group-ingress \
        --group-id $GROUP_ID \
        --protocol tcp \
        --port 8080 \
        --cidr 0.0.0.0/0 \
        --profile $PROFILE \
        --region $REGION
    
    echo "✅ Security group created with ID: $GROUP_ID"
else
    GROUP_ID=$(aws ec2 describe-security-groups \
        --group-names $SECURITY_GROUP_NAME \
        --profile $PROFILE \
        --region $REGION \
        --query 'SecurityGroups[0].GroupId' \
        --output text)
    echo "✅ Security group already exists with ID: $GROUP_ID"
fi

# Get latest Ubuntu AMI
echo "🔍 Finding latest Ubuntu AMI..."
AMI_ID=$(aws ec2 describe-images \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text \
    --profile $PROFILE \
    --region $REGION)

echo "✅ Using AMI: $AMI_ID"

# Launch EC2 instance
echo "🖥️  Launching EC2 instance..."
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id $AMI_ID \
    --count 1 \
    --instance-type $INSTANCE_TYPE \
    --key-name $KEY_NAME \
    --security-group-ids $GROUP_ID \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=afk-telegram-bot}]" \
    --profile $PROFILE \
    --region $REGION \
    --query 'Instances[0].InstanceId' \
    --output text)

echo "✅ Instance launched: $INSTANCE_ID"

# Wait for instance to be running
echo "⏳ Waiting for instance to be running..."
aws ec2 wait instance-running --instance-ids $INSTANCE_ID --profile $PROFILE --region $REGION
echo "✅ Instance is now running"

# Get public IP
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids $INSTANCE_ID \
    --profile $PROFILE \
    --region $REGION \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo "🌐 Instance public IP: $PUBLIC_IP"

# Wait for SSH to be available
echo "⏳ Waiting for SSH to be available..."
for i in {1..30}; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP "echo 'SSH ready'" &>/dev/null; then
        echo "✅ SSH is ready"
        break
    fi
    echo "Waiting for SSH... ($i/30)"
    sleep 5
done

# Create deployment package
echo "📦 Creating deployment package..."
DEPLOY_FILE="afk-bot-deploy.tar.gz"
tar --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='*.session' \
    --exclude='*.session-journal' \
    --exclude='downloads' \
    --exclude='node_modules' \
    --exclude='.python-version' \
    --exclude='nixpacks.toml' \
    --exclude='railway.json' \
    --exclude='package-lock.json' \
    --exclude='Procfile' \
    -czf $DEPLOY_FILE .

echo "✅ Deployment package created"

# Copy files to EC2
echo "📤 Copying files to EC2..."
scp -o StrictHostKeyChecking=no -i ${KEY_NAME}.pem $DEPLOY_FILE ubuntu@$PUBLIC_IP:/tmp/

# Setup on EC2
echo "⚙️  Setting up bot on EC2..."
ssh -o StrictHostKeyChecking=no -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP << 'ENDSSH'
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
tar -xzf /tmp/afk-bot-deploy.tar.gz
rm /tmp/afk-bot-deploy.tar.gz

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file with placeholders
cat > .env << 'EOF'
API_HASH=your_api_hash_here
API_ID=your_api_id_here
BOT_TOKEN=your_bot_token_here
BOT_USERNAME=your_bot_username_here
MONGODB_URI=your_mongodb_uri_here
OWNER_ID=your_owner_id_here
PYTHON_VERSION=3.11
EOF

# Create necessary directories
mkdir -p downloads

echo "✅ Application setup complete"
ENDSSH

# Create systemd service file
echo "🔧 Creating systemd service..."
SERVICE_FILE="/tmp/afk-bot.service"
cat > $SERVICE_FILE << EOF
[Unit]
Description=AFK Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Copy service file to EC2
scp -o StrictHostKeyChecking=no -i ${KEY_NAME}.pem $SERVICE_FILE ubuntu@$PUBLIC_IP:/tmp/

# Install and enable service
ssh -o StrictHostKeyChecking=no -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP << ENDSSH
set -e

# Move service file to systemd directory
sudo mv /tmp/afk-bot.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable afk-bot.service

# Start the service
sudo systemctl start afk-bot.service

# Check service status
sudo systemctl status afk-bot.service --no-pager

echo "✅ Systemd service installed and started"
ENDSSH

# Cleanup
rm $DEPLOY_FILE $SERVICE_FILE

echo ""
echo "🎉 Deployment complete!"
echo ""
echo "📋 Summary:"
echo "  Instance ID: $INSTANCE_ID"
echo "  Public IP: $PUBLIC_IP"
echo "  Region: $REGION"
echo ""
echo "🔑 Next steps:"
echo "  1. SSH into the instance: ssh -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP"
echo "  2. Edit the .env file: nano /home/ubuntu/afk-bot/.env"
echo "  3. Fill in your actual values for the environment variables"
echo "  4. Restart the service: sudo systemctl restart afk-bot.service"
echo "  5. Check logs: sudo journalctl -u afk-bot.service -f"
echo ""
echo "📝 To monitor the bot:"
echo "  Status: sudo systemctl status afk-bot.service"
echo "  Logs: sudo journalctl -u afk-bot.service -f"
echo "  Restart: sudo systemctl restart afk-bot.service"
echo "  Stop: sudo systemctl stop afk-bot.service"
