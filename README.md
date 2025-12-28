# ZipTrigo Apps

A monorepo containing multiple Django-based microservices for the Ziptrigo platform.
Services are developed independently but share common code and infrastructure.

## Architecture

This repository contains two separate Django applications:

- **users** - User authentication and authorization service
- **qr_code** - QR code generation and management service

### Key Features

- **Monorepo Structure**: Both services live in the same repository for easier code sharing and
  unified development
- **Shared Configuration**: Common Django settings in `common/settings/base.py` reduce duplication
- **Independent Deployment**: Each service has its own Dockerfile and can be deployed separately
- **Docker Compose**: Local development orchestration for both services
- **API Gateway Ready**: URL configurations support future integration with an API gateway

## Project Structure

```
ziptrigo-apps/
├── common/               # Shared code across services
│   └── settings/
│       └── base.py       # Shared Django settings
├── users/                # Users service
│   ├── config/           # Django configuration
│   ├── src/              # Application code
│   ├── tests/            # Tests
│   ├── Dockerfile        # Docker configuration
│   └── .env.dev          # Development environment variables
├── qr_code/              # QR Code service
│   ├── config/           # Django configuration
│   ├── src/              # Application code
│   ├── tests/            # Tests
│   ├── Dockerfile        # Docker configuration
│   └── .env.dev          # Development environment variables
├── docker-compose.yml    # Docker Compose configuration
└── WARP.md               # AI agent context
```

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose (for containerized development)
- Git

### Local Development (without Docker)

#### Users Service

```
cd users
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r ../requirements/users.txt
python manage.py migrate
python manage.py runserver 8010
```

#### QR Code Service

```
cd qr_code
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r ../requirements/qr_code.txt
python manage.py migrate
python manage.py runserver 8020
```

### Docker Development

Build and run both services:

```
docker-compose up --build
```

Run individual services:

```
# Users service only
docker-compose up users

# QR Code service only
docker-compose up qr_code
```

### Accessing Services

- **Users Service**: http://localhost:8010
  - Admin: http://localhost:8010/admin/
  - API: http://localhost:8010/api/
  - API Docs: http://localhost:8010/api/docs

- **QR Code Service**: http://localhost:8020
  - Admin: http://localhost:8020/admin/
  - API: http://localhost:8020/api/
  - API Docs: http://localhost:8020/api/docs

## Configuration

### Environment Variables

Each service requires its own environment configuration:

#### Users Service (.env.dev)
- `DEBUG` - Debug mode (True/False)
- `SECRET_KEY` - Django secret key
- `ALLOWED_HOSTS` - Comma-separated list of allowed hosts
- `DATABASE_URL` - Database connection string
- `JWT_SECRET` - JWT signing secret
- `JWT_ALGORITHM` - JWT algorithm (default: HS256)
- `JWT_EXP_DELTA_SECONDS` - JWT expiration time in seconds

#### QR Code Service (.env.dev)
- `DEBUG` - Debug mode (True/False)
- `SECRET_KEY` - Django secret key
- `ENVIRONMENT` - Environment name (dev/prod)
- `DATABASE_URL` - Database connection string
- `BASE_URL` - Base URL for QR code redirects
- `EMAIL_BACKENDS` - Email backend configuration
- `AWS_SES_SENDER` - SES sender email address

### Databases

Both services use external databases. Configure via `DATABASE_URL` environment variable or update
`DATABASES` in the respective `config/settings.py` files.

## Development Workflow

### Running Tests

```
# Users service tests
cd users
pytest

# QR Code service tests
cd qr_code
pytest
```

### Linting and Type Checking

Each service has its own admin utilities for linting and type checking. Check the respective
`admin/` directories for available commands.

### Adding Shared Code

Place shared utilities, models, or helpers in the `common/` directory. Both services can import
from this directory:

```python
from common.settings.base import COMMON_MIDDLEWARE
```

## Deployment

### Production Considerations

1. **Environment Variables**: Use production-ready secrets and configurations
2. **Database**: Connect to production databases (external to Docker)
3. **Static Files**: Configure proper static file serving (WhiteNoise, S3, etc.)
4. **Media Files**: Configure media file storage (S3, cloud storage, etc.)
5. **Migrations**: Run migrations during deployment process
6. **WSGI Server**: Replace `runserver` with gunicorn or uwsgi

### API Gateway Integration

When deploying behind an API gateway (nginx, Traefik, etc.):

1. Update `users/config/urls.py` - uncomment the prefixed urlpatterns
2. Update `qr_code/config/urls.py` - uncomment the prefixed urlpatterns
3. Configure gateway to route:
   - `/users/*` → Users service
   - `/qr-code/*` → QR Code service

## Future Plans

- **Shared Authentication**: QR Code service will use Users service for authentication
- **Additional Services**: More microservices can be added following the same pattern
- **API Gateway**: Implement unified entry point for all services
- **Common Utilities**: Expand shared code for database utilities, logging, etc.

## Contributing

When making changes:

1. Follow existing code patterns and structure
2. Update tests for your changes
3. Run linting and type checking before committing
4. Update documentation as needed

## Git History

This repository was created by merging two separate repositories using git subtree, preserving the
commit history from both:
- Users service original repository
- QR Code service original repository

## License

See LICENSE file in each service directory for details
