# EC2 Deployment Guide for AFK Telegram Bot

This guide will help you deploy the AFK Telegram Bot to AWS EC2 with systemd for 24/7 background operation.

## Prerequisites

1. **AWS CLI** installed and configured with your `default` profile in region `ap-south-1`
2. **SSH client** installed (for Windows, you can use OpenSSH or Git Bash)
3. **AWS credentials** with permissions to create EC2 instances, security groups, and key pairs

## Deployment Options

### Option 1: Automated Deployment (Recommended)

#### On Windows (PowerShell)
```powershell
.\deploy_to_ec2.ps1
```

#### On Linux/Mac/WSL (Bash)
```bash
chmod +x deploy_to_ec2.sh
./deploy_to_ec2.sh
```

The automated script will:
- Create an EC2 key pair (if it doesn't exist)
- Create a security group with SSH (port 22) and Flask server (port 8080) access
- Launch a t3.micro Ubuntu 22.04 instance in ap-south-1
- Install Python 3.11 and dependencies
- Set up the bot application
- Create and enable a systemd service for 24/7 operation
- Create a `.env` file with placeholder values

### Option 2: Manual Deployment

If you prefer manual deployment or need to customize the setup:

1. **Create EC2 Key Pair**
   ```bash
   aws ec2 create-key-pair --key-name afk-bot-key --query 'KeyMaterial' --output text --profile default --region ap-south-1 > afk-bot-key.pem
   chmod 400 afk-bot-key.pem
   ```

2. **Create Security Group**
   ```bash
   aws ec2 create-security-group --group-name afk-bot-sg --description "Security group for AFK Telegram Bot" --profile default --region ap-south-1
   aws ec2 authorize-security-group-ingress --group-name afk-bot-sg --protocol tcp --port 22 --cidr 0.0.0.0/0 --profile default --region ap-south-1
   aws ec2 authorize-security-group-ingress --group-name afk-bot-sg --protocol tcp --port 8080 --cidr 0.0.0.0/0 --profile default --region ap-south-1
   ```

3. **Launch EC2 Instance**
   ```bash
   # Get latest Ubuntu AMI
   AMI_ID=$(aws ec2 describe-images --owners 099720109477 --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" "Name=state,Values=available" --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text --profile default --region ap-south-1)
   
   # Launch instance
   INSTANCE_ID=$(aws ec2 run-instances --image-id $AMI_ID --count 1 --instance-type t3.micro --key-name afk-bot-key --security-groups afk-bot-sg --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=afk-telegram-bot}]" --profile default --region ap-south-1 --query 'Instances[0].InstanceId' --output text)
   
   # Wait for instance to be running
   aws ec2 wait instance-running --instance-ids $INSTANCE_ID --profile default --region ap-south-1
   
   # Get public IP
   PUBLIC_IP=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID --profile default --region ap-south-1 --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
   ```

4. **Connect to EC2 and Setup**
   ```bash
   ssh -i afk-bot-key.pem ubuntu@$PUBLIC_IP
   
   # On the EC2 instance:
   sudo apt-get update -y
   sudo apt-get install -y python3.11 python3.11-venv python3-pip git
   
   # Create app directory
   mkdir -p /home/ubuntu/afk-bot
   cd /home/ubuntu/afk-bot
   
   # Copy your bot files (using scp from your local machine)
   # scp -i afk-bot-key.pem -r . ubuntu@$PUBLIC_IP:/home/ubuntu/afk-bot/
   
   # Create virtual environment
   python3.11 -m venv venv
   source venv/bin/activate
   
   # Install dependencies
   pip install --upgrade pip
   pip install -r requirements.txt
   
   # Create .env file with your actual values
   nano .env
   ```

5. **Setup Systemd Service**
   ```bash
   # Copy the service file
   sudo cp afk-bot.service /etc/systemd/system/
   
   # Reload systemd
   sudo systemctl daemon-reload
   
   # Enable service to start on boot
   sudo systemctl enable afk-bot.service
   
   # Start the service
   sudo systemctl start afk-bot.service
   ```

## Post-Deployment Configuration

After deployment, you need to configure the environment variables:

1. **SSH into the instance**
   ```bash
   ssh -i afk-bot-key.pem ubuntu@<PUBLIC_IP>
   ```

2. **Edit the .env file**
   ```bash
   nano /home/ubuntu/afk-bot/.env
   ```

3. **Fill in your actual values**
   ```env
   API_HASH=your_actual_api_hash
   API_ID=your_actual_api_id
   BOT_TOKEN=your_actual_bot_token
   BOT_USERNAME=your_actual_bot_username
   MONGODB_URI=your_actual_mongodb_uri
   OWNER_ID=your_actual_owner_id
   PYTHON_VERSION=3.11
   ```

4. **Restart the service**
   ```bash
   sudo systemctl restart afk-bot.service
   ```

## Monitoring and Management

### Check Service Status
```bash
sudo systemctl status afk-bot.service
```

### View Logs
```bash
sudo journalctl -u afk-bot.service -f
```

### Restart Service
```bash
sudo systemctl restart afk-bot.service
```

### Stop Service
```bash
sudo systemctl stop afk-bot.service
```

### Start Service
```bash
sudo systemctl start afk-bot.service
```

## Environment Variables

| Variable | Required | Description |
| :--- | :--- | :--- |
| `API_ID` | Yes | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Yes | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `BOT_USERNAME` | Yes | Bot username without `@` |
| `MONGODB_URI` | Yes | MongoDB connection string |
| `OWNER_ID` | Yes | Numeric Telegram ID of the bot owner |
| `PYTHON_VERSION` | No | Python version (default: 3.11) |

## Security Considerations

1. **Key Pair Security**: The `.pem` key file allows access to your EC2 instance. Keep it secure and never commit it to version control.
2. **Security Groups**: The script opens ports 22 (SSH) and 8080 (Flask) to the world. Consider restricting this to specific IP addresses in production.
3. **Environment Variables**: The `.env` file contains sensitive information. Ensure it's properly secured and not exposed.
4. **MongoDB URI**: Use a strong password and consider using MongoDB Atlas or a secured MongoDB instance.

## Troubleshooting

### SSH Connection Refused
- Wait a few minutes after instance launch for the instance to fully initialize
- Check that the security group allows SSH (port 22) from your IP
- Verify you're using the correct key pair

### Service Won't Start
- Check the service logs: `sudo journalctl -u afk-bot.service -f`
- Verify the .env file is properly configured
- Ensure all Python dependencies are installed
- Check that MongoDB is accessible

### Bot Not Responding
- Verify the BOT_TOKEN is correct
- Check if the bot is running: `sudo systemctl status afk-bot.service`
- Review logs for any error messages

## Cost Considerations

- **EC2 Instance**: t3.micro in ap-south-1 costs approximately $0.0115/hour (~$8.38/month)
- **Data Transfer**: Additional costs may apply for data transfer out
- **Consider using AWS Free Tier** if you're eligible (12 months of free t2.micro/t3.micro)

## Cleanup

To delete the deployment and stop incurring costs:

```bash
# Terminate EC2 instance
aws ec2 terminate-instances --instance-ids <INSTANCE_ID> --profile default --region ap-south-1

# Delete security group
aws ec2 delete-security-group --group-name afk-bot-sg --profile default --region ap-south-1

# Delete key pair (optional)
aws ec2 delete-key-pair --key-name afk-bot-key --profile default --region ap-south-1
```
