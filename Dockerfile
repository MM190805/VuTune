FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

# Install ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy the app files
COPY . /app

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Make start script executable
RUN chmod +x start.sh

# Start the application
CMD ["./start.sh"]
