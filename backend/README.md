# Agent Memory System - Backend

FastAPI backend for the Agent Memory System.

## Setup

### Prerequisites
- Python 3.13+
- Poetry

### Installation

1. Install dependencies:
```bash
poetry install
```

2. Activate virtual environment:
```bash
poetry shell
```

## Running the Application

### Development mode with auto-reload:
```bash
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production mode:
```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Documentation

Once the application is running, you can access:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI Schema**: http://localhost:8000/openapi.json

## Health Check

Check the health status of the API:
```bash
curl http://localhost:8000/api/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "timestamp": "2026-06-24T...",
  "service": "agent-memory-backend"
}
```

## Project Structure

```
backend/
├── app/
│   ├── api/          # API route handlers
│   ├── core/         # Core configuration and utilities
│   ├── models/       # Pydantic models and database models
│   ├── services/     # Business logic services
│   └── main.py      # FastAPI application entry point
├── tests/            # Test files
└── pyproject.toml   # Poetry configuration
```

## Dependencies

- **FastAPI**: Web framework
- **Uvicorn**: ASGI server
- **LangChain**: LLM framework
- **LlamaIndex**: Data framework for LLM applications
- **Redis**: Caching and session storage
- **PostgreSQL**: Primary database (psycopg2-binary)
- **Milvus**: Vector database (pymilvus)
- **python-jose**: JWT token handling
- **passlib**: Password hashing


登录账户
用户名: admin
密码: Admin123!