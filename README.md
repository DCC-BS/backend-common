# backend-common

[![Commit activity](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)](https://img.shields.io/github/commit-activity/m/DCC-BS/backend-common)
[![License](https://img.shields.io/github/license/DCC-BS/backend-common)](https://img.shields.io/github/license/DCC-BS/backend-common)

Common utilities and components for backend services developed by the Data Competence Center Basel-Stadt.

## Overview

`backend-common` is a Python library that provides shared functionality for backend services, including:

- **FastAPI Health Probes**: Kubernetes-ready health check endpoints (liveness, readiness, startup)
- **Structured Logging**: Integration with `structlog` for consistent logging across services
- **Configuration Management**: Environment-based configuration with `python-dotenv`

## Installation

### Basic Installation

```bash
pip install backend-common
```

### With FastAPI Support

```bash
pip install backend-common[fastapi]
```

## Requirements

- Python 3.12 or higher
- Dependencies:
  - `python-dotenv>=1.0.1`
  - `structlog>=25.1.0`

### Optional Dependencies

- FastAPI extras: `aiohttp>=3.13.2`, `fastapi>=0.115,<1.0`

## Features

### FastAPI Health Probes

The library provides Kubernetes-ready health check endpoints that follow best practices for container orchestration:

#### Example Usage

```python
from fastapi import FastAPI
from backend_common.fastapi_health_probes import health_probe_router

app = FastAPI()

# Define external service dependencies
service_dependencies = [
    {
        "name": "database",
        "health_check_url": "http://postgres:5432/health",
        "api_key": None  # Optional API key for authenticated health checks
    },
    {
        "name": "external-api",
        "health_check_url": "https://api.example.com/health",
        "api_key": "your-api-key-here"
    }
]

# Include health probe router
app.include_router(health_probe_router(service_dependencies))
```

#### Available Endpoints

##### 1. Liveness Probe (`GET /health/liveness`)

- **Purpose**: Checks if the application process is running and not deadlocked
- **Kubernetes Action**: If this fails, the container is killed and restarted
- **Response**: Returns uptime in seconds
- **Rule**: Keep it simple. Do NOT check databases or external dependencies here

```json
{
  "status": "up",
  "uptime_seconds": 123.45
}
```

##### 2. Readiness Probe (`GET /health/readiness`)

- **Purpose**: Checks if the app is ready to handle user requests
- **Kubernetes Action**: If this fails, traffic stops being sent to this pod
- **Response**: Returns status of all configured service dependencies
- **Rule**: Check critical dependencies here (databases, external APIs, etc.)

```json
{
  "status": "ready",
  "checks": {
    "database": "healthy",
    "external-api": "healthy"
  }
}
```

If a dependency fails:

```json
{
  "status": "unhealthy",
  "checks": {
    "database": "error: Connection refused",
    "external-api": "unhealthy (status: 503)"
  },
  "error": "Service unavailable"
}
```

##### 3. Startup Probe (`GET /health/startup`)

- **Purpose**: Checks if the application has finished initialization
- **Kubernetes Action**: Blocks liveness/readiness probes until this returns 200
- **Response**: Returns startup timestamp
- **Rule**: Useful for apps that need to load large ML models or caches on boot

```json
{
  "status": "started",
  "timestamp": "2025-12-04T10:30:00.000000+00:00"
}
```

#### Features

- **Automatic Logging Suppression**: Health check endpoints are automatically excluded from access logs to reduce noise
- **Dependency Health Checks**: Readiness probe checks external service dependencies with configurable timeouts (5 seconds default)
- **Authentication Support**: Optional API key support for authenticated health checks
- **Kubernetes-Ready**: HTTP status codes follow Kubernetes conventions (200 = healthy, 503 = unhealthy)

## Development

### Setup

1. Clone the repository:

```bash
git clone https://github.com/DCC-BS/backend-common.git
cd backend-common
```

2. Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install development dependencies:

```bash
pip install -e ".[fastapi]"
pip install -r requirements-dev.txt  # or install dev dependency group
```

### Running Tests

```bash
pytest
```

### Code Quality

This project uses:

- **Ruff**: For linting and formatting
- **Pre-commit**: For automated code quality checks
- **Tox**: For testing across multiple Python versions

Run linting:

```bash
ruff check .
```

Run pre-commit hooks:

```bash
pre-commit run --all-files
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to contribute to this project.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Authors

- **Data Competence Center Basel-Stadt** - [dcc@bs.ch](mailto:dcc@bs.ch)
- **Tobias Bollinger** - [tobias.bollinger@bs.ch](mailto:tobias.bollinger@bs.ch)

## Links

- **Homepage**: https://DCC-BS.github.io/backend-common/
- **Repository**: https://github.com/DCC-BS/backend-common
- **Documentation**: https://DCC-BS.github.io/backend-common/
