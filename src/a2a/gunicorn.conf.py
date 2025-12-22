"""
Gunicorn Configuration for A2A Server

Gunicorn (Green Unicorn) is a Python Web Server Gateway Interface (WSGI) HTTP server
for UNIX. It's a pre-fork worker model ported from Ruby's Unicorn project. It supports
ASGI applications through Uvicorn workers, making it ideal for FastAPI applications.

This configuration file sets up Gunicorn to serve the A2A FastAPI application
with proper async support and performance settings.

Key frameworks and components used:
- Gunicorn: Production-grade WSGI/ASGI HTTP server for Python web applications
- Uvicorn: Lightning-fast ASGI server implementation used as Gunicorn worker class
- FastAPI: Modern async web framework for building APIs with automatic documentation
- A2A Protocol: Agent-to-Agent communication protocol for multi-agent coordination
"""
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('A2A_PORT', '8001')}"
backlog = 2048

# Worker processes
workers = int(os.getenv('A2A_WORKERS', multiprocessing.cpu_count() * 2 + 1))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50

# Timeout settings
timeout = 30
keepalive = 2
graceful_timeout = 30

# Process naming
proc_name = 'a2a-server'

# Logging
accesslog = '-'  # stdout
errorlog = '-'   # stderr
loglevel = os.getenv('A2A_LOG_LEVEL', 'info').lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process management
preload_app = True
reload = os.getenv('A2A_DEBUG', 'false').lower() == 'true'

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Performance
forwarded_allow_ips = '*'
proxy_allow_ips = '*'