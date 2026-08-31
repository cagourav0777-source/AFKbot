# EC2 Deployment Script for AFK Telegram Bot (PowerShell Version)
# This script deploys the bot to an EC2 instance and sets up systemd service

$ErrorActionPreference = "Stop"

# Configuration
$REGION = "ap-south-1"
$PROFILE = "default"
$INSTANCE_TYPE = "t3.micro"
$KEY_NAME = "afk-bot-key"
$SECURITY_GROUP_NAME = "afk-bot-sg"
$APP_DIR = "/home/ubuntu/afk-bot"
$SERVICE_NAME = "afk-bot"

Write-Host "Starting EC2 deployment for AFK Telegram Bot..." -ForegroundColor Green

# Check if AWS CLI is configured
try {
    $callerIdentity = aws sts get-caller-identity --profile $PROFILE --region $REGION 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI not configured or profile '$PROFILE' not found"
    }
    Write-Host "AWS credentials verified" -ForegroundColor Green
} catch {
    Write-Host "AWS CLI not configured or profile '$PROFILE' not found" -ForegroundColor Red
    Write-Host "Please run: aws configure --profile $PROFILE" -ForegroundColor Yellow
    exit 1
}

# Create key pair if it doesn't exist
$keyPairs = aws ec2 describe-key-pairs --key-names $KEY_NAME --profile $PROFILE --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating new key pair: $KEY_NAME" -ForegroundColor Yellow
    $keyMaterial = aws ec2 create-key-pair --key-name $KEY_NAME --query 'KeyMaterial' --output text --profile $PROFILE --region $REGION
    $keyMaterial | Out-File -FilePath "${KEY_NAME}.pem" -Encoding ASCII
    $pemFile = Get-Item "${KEY_NAME}.pem"
    $pemFile.Attributes = 'Hidden'
    Write-Host "Key pair created and saved to ${KEY_NAME}.pem" -ForegroundColor Green
    Write-Host "Keep this file secure! It allows access to your EC2 instance." -ForegroundColor Yellow
} else {
    Write-Host "Key pair $KEY_NAME already exists" -ForegroundColor Green
}

# Create security group if it doesn't exist
$securityGroups = aws ec2 describe-security-groups --group-names $SECURITY_GROUP_NAME --profile $PROFILE --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating security group: $SECURITY_GROUP_NAME" -ForegroundColor Yellow
    $GROUP_ID = aws ec2 create-security-group --group-name $SECURITY_GROUP_NAME --description "Security group for AFK Telegram Bot" --profile $PROFILE --region $REGION --query 'GroupId' --output text
    
    aws ec2 authorize-security-group-ingress --group-id $GROUP_ID --protocol tcp --port 22 --cidr 0.0.0.0/0 --profile $PROFILE --region $REGION | Out-Null
    aws ec2 authorize-security-group-ingress --group-id $GROUP_ID --protocol tcp --port 8080 --cidr 0.0.0.0/0 --profile $PROFILE --region $REGION | Out-Null
    
    Write-Host "Security group created with ID: $GROUP_ID" -ForegroundColor Green
} else {
    $GROUP_ID = aws ec2 describe-security-groups --group-names $SECURITY_GROUP_NAME --profile $PROFILE --region $REGION --query 'SecurityGroups[0].GroupId' --output text
    Write-Host "Security group already exists with ID: $GROUP_ID" -ForegroundColor Green
}

# Get latest Ubuntu AMI
Write-Host "Finding latest Ubuntu AMI..." -ForegroundColor Yellow
$AMI_ID = aws ec2 describe-images --owners 099720109477 --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" "Name=state,Values=available" --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text --profile $PROFILE --region $REGION
Write-Host "Using AMI: $AMI_ID" -ForegroundColor Green

# Launch EC2 instance
Write-Host "Launching EC2 instance..." -ForegroundColor Yellow
$INSTANCE_ID = aws ec2 run-instances --image-id $AMI_ID --count 1 --instance-type $INSTANCE_TYPE --key-name $KEY_NAME --security-group-ids $GROUP_ID --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=afk-telegram-bot}]" --profile $PROFILE --region $REGION --query 'Instances[0].InstanceId' --output text
Write-Host "Instance launched: $INSTANCE_ID" -ForegroundColor Green

# Wait for instance to be running
Write-Host "Waiting for instance to be running..." -ForegroundColor Yellow
aws ec2 wait instance-running --instance-ids $INSTANCE_ID --profile $PROFILE --region $REGION
Write-Host "Instance is now running" -ForegroundColor Green

# Get public IP
$PUBLIC_IP = aws ec2 describe-instances --instance-ids $INSTANCE_ID --profile $PROFILE --region $REGION --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
Write-Host "Instance public IP: $PUBLIC_IP" -ForegroundColor Green

# Wait for SSH to be available
Write-Host "Waiting for SSH to be available..." -ForegroundColor Yellow
$maxAttempts = 30
$attempt = 0
$sshReady = $false

while ($attempt -lt $maxAttempts -and -not $sshReady) {
    $attempt++
    try {
        $sshTest = ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes -i "$($KEY_NAME).pem" ubuntu@$PUBLIC_IP "echo SSH ready" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $sshReady = $true
            Write-Host "SSH is ready" -ForegroundColor Green
            break
        }
    } catch {
        # Continue trying
    }
    Write-Host "Waiting for SSH... ($attempt/$maxAttempts)" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}

if (-not $sshReady) {
    Write-Host "SSH did not become available within expected time" -ForegroundColor Red
    Write-Host "Please manually check the instance status and try connecting later" -ForegroundColor Yellow
    exit 1
}

# Create deployment package
Write-Host "Creating deployment package..." -ForegroundColor Yellow
$DEPLOY_FILE = "afk-bot-deploy.tar.gz"

$tempDir = "temp_deploy"
if (Test-Path $tempDir) {
    Remove-Item $tempDir -Recurse -Force
}
New-Item -ItemType Directory -Path $tempDir | Out-Null

# Copy files excluding unwanted ones
$excludeItems = @('.git', '__pycache__', '*.pyc', '.env', '*.session', '*.session-journal', 'downloads', 'node_modules', '.python-version', 'nixpacks.toml', 'railway.json', 'package-lock.json', 'Procfile')

Get-ChildItem -Path . | ForEach-Object {
    $item = $_
    $shouldExclude = $false
    foreach ($pattern in $excludeItems) {
        if ($item.Name -like $pattern) {
            $shouldExclude = $true
            break
        }
    }
    if (-not $shouldExclude -and $item.Name -ne $tempDir -and $item.Name -ne $DEPLOY_FILE) {
        Copy-Item -Path $item.FullName -Destination $tempDir -Recurse -Force
    }
}

# Create tar.gz using 7-Zip if available, otherwise we'll use a different method
$sevenZipPath = "C:\Program Files\7-Zip\7z.exe"
if (Test-Path $sevenZipPath) {
    & $sevenZipPath a -tgzip $DEPLOY_FILE $tempDir\* | Out-Null
    Write-Host "Deployment package created using 7-Zip" -ForegroundColor Green
} else {
    Write-Host "7-Zip not found. Using alternative method..." -ForegroundColor Yellow
    Compress-Archive -Path "$tempDir\*" -DestinationPath "afk-bot-deploy.zip" -Force
    $DEPLOY_FILE = "afk-bot-deploy.zip"
    Write-Host "Deployment package created as ZIP" -ForegroundColor Green
}

# Copy files to EC2
Write-Host "Copying files to EC2..." -ForegroundColor Yellow
scp -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" $DEPLOY_FILE "ubuntu@$($PUBLIC_IP):/tmp/"

# Copy setup script to EC2
scp -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "setup.sh" "ubuntu@$($PUBLIC_IP):/tmp/"

# Execute setup script
Write-Host "Running setup script on EC2..." -ForegroundColor Yellow
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "chmod +x /tmp/setup.sh; /tmp/setup.sh"
Write-Host "Application setup complete" -ForegroundColor Green

# Create systemd service file
Write-Host "Creating systemd service..." -ForegroundColor Yellow
$serviceContent = @"
[Unit]
Description=AFK Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/afk-bot
Environment="PATH=/home/ubuntu/afk-bot/venv/bin"
EnvironmentFile=/home/ubuntu/afk-bot/.env
ExecStart=/home/ubuntu/afk-bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"@

$serviceContent | Out-File -FilePath "afk-bot.service" -Encoding ASCII

# Copy service file to EC2
scp -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "afk-bot.service" "ubuntu@$($PUBLIC_IP):/tmp/"

# Install and enable service
Write-Host "Installing and starting systemd service..." -ForegroundColor Yellow
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "sudo mv /tmp/afk-bot.service /etc/systemd/system/"
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "sudo systemctl daemon-reload"
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "sudo systemctl enable afk-bot.service"
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "sudo systemctl start afk-bot.service"
ssh -o StrictHostKeyChecking=no -i "$($KEY_NAME).pem" "ubuntu@$($PUBLIC_IP)" "sudo systemctl status afk-bot.service --no-pager"
Write-Host "Systemd service installed and started" -ForegroundColor Green

# Cleanup
Remove-Item $DEPLOY_FILE -Force -ErrorAction SilentlyContinue
Remove-Item "afk-bot.service" -Force -ErrorAction SilentlyContinue
Remove-Item "setup.sh" -Force -ErrorAction SilentlyContinue
Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Deployment complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Summary:" -ForegroundColor Cyan
Write-Host "  Instance ID: $INSTANCE_ID"
Write-Host "  Public IP: $PUBLIC_IP"
Write-Host "  Region: $REGION"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. SSH into the instance: ssh -i $($KEY_NAME).pem ubuntu@$($PUBLIC_IP)"
Write-Host "  2. Edit the .env file: nano /home/ubuntu/afk-bot/.env"
Write-Host "  3. Fill in your actual values for the environment variables"
Write-Host "  4. Restart the service: sudo systemctl restart afk-bot.service"
Write-Host "  5. Check logs: sudo journalctl -u afk-bot.service -f"
Write-Host ""
Write-Host "To monitor the bot:" -ForegroundColor Cyan
Write-Host "  Status: sudo systemctl status afk-bot.service"
Write-Host "  Logs: sudo journalctl -u afk-bot.service -f"
Write-Host "  Restart: sudo systemctl restart afk-bot.service"
Write-Host "  Stop: sudo systemctl stop afk-bot.service"
