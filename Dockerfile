FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (including dos2unix to prevent Windows line-ending errors)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Convert Windows line endings to Unix and make run.sh executable
RUN dos2unix run.sh && chmod +x run.sh

# Expose ports (FastAPI on 8000, Streamlit on 8501)
EXPOSE 8000
EXPOSE 8501

# Start all processes using the startup script
CMD ["./run.sh"]