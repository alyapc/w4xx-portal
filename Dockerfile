FROM python:3.11-slim

# Install system dependencies including ffmpeg, git, and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps discord-native-voice==0.1.1

# Copy application files
COPY . .

# Download soundboard vault tracks from high-speed storage
RUN mkdir -p uploads && \
    curl -f -L -s -o "uploads/KUTHAA_MAATA_-_Gana_Bulkan.mp3" "https://huggingface.co/fhhsddbhskh/w4xx-portal-code/resolve/main/uploads/KUTHAA_MAATA_-_Gana_Bulkan.mp3" && \
    curl -f -L -s -o "uploads/Veezha-Veera-Bass-SnapYT.App.m4a" "https://huggingface.co/fhhsddbhskh/w4xx-portal-code/resolve/main/uploads/Veezha-Veera-Bass-SnapYT.App.m4a" && \
    curl -f -L -s -o "uploads/WATTARAM__FULL_VIDEO_SONG_OUT_NOW__GANA_BADRI_x_BULKAN__RENI_LAMB__-_Gana_Badri.mp3" "https://huggingface.co/fhhsddbhskh/w4xx-portal-code/resolve/main/uploads/WATTARAM__FULL_VIDEO_SONG_OUT_NOW__GANA_BADRI_x_BULKAN__RENI_LAMB__-_Gana_Badri.mp3"

# Port (Render passes PORT environment variable dynamically)
ENV PORT=10000
EXPOSE 10000

# Run the backend server
CMD ["python", "server.py"]
