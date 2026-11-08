# Use NVIDIA PyTorch container with stable PyTorch (24.09 has PyTorch 2.4)
FROM nvcr.io/nvidia/pytorch:24.09-py3

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js for building frontend
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt requirements-dev.txt ./

# Pre-process requirements to exclude packages already in base image
# Exclude torch ecosystem packages that ship with the NVIDIA base image
# to prevent version conflicts (e.g. torchvision::nms operator errors)
RUN grep -vE "^torch$|^torchaudio$|^torchvision$|^numpy$|^pyaudio$" requirements.txt > requirements.docker.txt

# Remove NVIDIA's defunct NGC PyPi index from pip configuration files
RUN rm -f /etc/pip.conf /etc/xdg/pip/pip.conf /root/.config/pip/pip.conf /root/.pip/pip.conf /usr/pip.conf

# Upgrade pip
RUN python -m pip install --upgrade pip

# Freeze the base image's torch ecosystem into a constraints file
# so pip won't overwrite them when installing nemo_toolkit dependencies
RUN pip list --format=freeze | grep -iE "^(torch|torchvision|torchaudio|torchmetrics|lightning)==" > /tmp/constraints.txt || true

# Install Python dependencies with constraints to protect torch ecosystem
# Force NumPy 1.x AFTER all other installs for NeMo compatibility
RUN pip install --no-cache-dir -c /tmp/constraints.txt -r requirements.docker.txt \
    && pip install --no-cache-dir --force-reinstall "numpy<2.0" "setuptools<78"

# Build frontend
COPY webui/ ./webui/
WORKDIR /app/webui
RUN npm ci && npm run build

# Copy server code
WORKDIR /app
COPY server/ ./server/

# Create logs directory
RUN mkdir -p /app/logs

# Expose port
EXPOSE 7777

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=0

# Run the server
WORKDIR /app/server
CMD ["uvicorn", "listen:app", "--host", "0.0.0.0", "--port", "7777"]


