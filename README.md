# ZipTrigo Apps

A monorepo containing multiple Django-based microservices for the Ziptrigo platform.
Services are developed independently but share common code and infrastructure.

## Architecture

This repository contains multiple services and shared components:

- **user-service** - Central user authentication, authorization, and profile management service.
- **qr_code** - QR code generation and management service (consumes `user-service` for auth).
- **shared/auth_client** - A shared Python package for services to easily integrate with `user-service`.
- **shared/utils** - Common utilities, models, and settings shared across services.
- **admin** - Project-level administration and maintenance scripts.

### Key Features

- **Service-Oriented Architecture**: Decoupled services that communicate via well-defined APIs and shared authentication.
- **Shared Packages**: Common logic is encapsulated in installable Python packages in the `shared/` directory.
- **Unified Auth**: `user-service` is the single source of truth for users; other services use `auth_client` to verify identity.
- **Independent Deployment**: Each service has its own Dockerfile and can be scaled or deployed separately.
- **Docker Compose**: Orchestration for local development of all services and the database.

## Project Structure

```
ziptrigo-apps/
├── admin/               # Project administration scripts (lint, test, etc.)
├── shared/              # Shared Python packages
│   ├── auth_client/     # User-service integration client
│   └── utils/           # Shared utilities and base settings
├── user-service/        # Authentication & User service
│   ├── config/          # Django configuration
│   ├── users/           # Application logic
│   ├── tests/           # Service-specific tests
│   └── Dockerfile       # Container configuration
├── qr_code/             # QR Code service
│   ├── config/          # Django configuration
│   ├── qr_code/         # Application logic
│   ├── tests/           # Service-specific tests
│   └── Dockerfile       # Container configuration
├── docker-compose.yml   # Local development orchestration
└── WARP.md              # AI agent context
```

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- Git

### Local Development (with Docker)

The easiest way to get started is using Docker Compose:

```bash
docker-compose up --build
```

### Local Development (without Docker)

If running locally, you need to install the shared packages and service-specific requirements.

#### 1. Setup Shared Packages
```bash
pip install -e ./shared/utils
pip install -e ./shared/auth_client
```

#### 2. User Service
```bash
cd user-service
pip install -r requirements.txt  # Or use your preferred env manager
python manage.py migrate
python manage.py runserver 8010
```

#### 3. QR Code Service
```bash
cd qr_code
pip install -r requirements.txt
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

- **User Service**: http://localhost:8010
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

#### User Service (.env.dev)
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

```bash
# All tests
python -m admin.test

# User service tests
cd user-service
pytest

# QR Code service tests
cd qr_code
pytest
```

### Linting and Type Checking

Use the project-level admin scripts:

```bash
python -m admin.lint
```

### Adding Shared Code

Place shared utilities, models, or helpers in the `shared/utils/utils/` directory. Both services can import
from the `utils` package:

```python
from utils.settings.base import COMMON_MIDDLEWARE
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

1. Update `user-service/config/urls.py` - uncomment the prefixed urlpatterns
2. Update `qr_code/config/urls.py` - uncomment the prefixed urlpatterns
3. Configure gateway to route:
   - `/users/*` → User service
   - `/qr-code/*` → QR Code service

## Future Plans

- **Service Communication**: Implement more robust inter-service communication patterns.
- **Shared Authentication**: Enhance `auth_client` for more granular permission checks.
- **Additional Services**: More microservices can be added following the same pattern.
- **API Gateway**: Implement unified entry point for all services.

## Design System

### Color Palette

The Ziptrigo brand uses a sage green color palette derived from the service logos. Use these colors when building web pages and interfaces for consistency across all services.

#### Primary Colors
- **Sage Green**: `#8FA89E` - Main brand color (mid-tone green-gray)
- **Dark Slate**: `#3B4A47` - Dark gray-green for text and accents
- **Light Sage**: `#B5C7BE` - Lighter variant for backgrounds and subtle elements

#### Supporting Colors
- **Deep Charcoal**: `#2C3432` - Darkest tone for primary text and borders
- **Soft Mint**: `#D4E0DA` - Very light green-gray for backgrounds
- **White**: `#FFFFFF` - For contrast and backgrounds

#### Suggested Usage
- **Headers/Primary Text**: Deep Charcoal or Dark Slate
- **Backgrounds (Light Mode)**: White or Soft Mint
- **Backgrounds (Dark Mode)**: Deep Charcoal with Dark Slate accents
- **Buttons/CTAs**: Sage Green with white text
- **Hover States**: Dark Slate
- **Borders/Dividers**: Light Sage or Soft Mint

#### Tailwind CSS Configuration

```css
colors: {
  sage: {
    50: '#f4f7f6',
    100: '#d4e0da',
    200: '#b5c7be',
    300: '#8fa89e',
    400: '#728e84',
    500: '#5a736a',
    600: '#475a53',
    700: '#3b4a47',
    800: '#2c3432',
    900: '#1e2422'
  }
}
```

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
