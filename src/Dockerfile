FROM python:3.11-slim

WORKDIR /app

# Set environment variables to avoid encoding issues
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies with better error handling
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run the main application (simplified - just the media app)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
