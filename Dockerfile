FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOME=/home/user

# Install system dependencies (ffmpeg, curl, ca-certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

# Copy requirement list and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create data & download directories with proper permissions
RUN mkdir -p /home/user/app/downloads /data && \
    chown -R user:user /home/user/app /data

# Switch to non-root user
USER user

# Default Environment variables (To be set via Container/Host Environment)
ENV COMPLETED_TXT_PATH="completed_messages.txt"
ENV CHECK_INTERVAL="300"
ENV DOWNLOAD_DIR="./downloads"

# Command to execute worker
CMD ["python", "-u", "telegram_ia_sync.py"]
