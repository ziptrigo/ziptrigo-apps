# Ziptrigo Apps - AI Agent Context

This document provides context for AI agents (like Warp Agent) working on the ziptrigo-apps monorepo.

## Project Overview

**ziptrigo-apps** is a monorepo containing two Django microservices that are developed together but deployed independently. The repository was created by merging two separate repositories using git subtree, preserving commit history from both.

### Services

1. **users** (Port 8010)
   - User authentication and authorization service
   - Uses django-ninja and django-ninja-jwt
   - Custom User model with email-based authentication
   - JWT token-based API authentication
   - Located in: `users/`

2. **qr_code** (Port 8020)
   - QR code generation and management service
   - Uses django-ninja-extra and django-ninja-jwt
   - Custom User model (separate from users service)
   - File uploads (media) and email functionality (AWS SES)
   - Located in: `qr_code/`

## Architecture Principles

### Monorepo Benefits
- **Shared Code**: Common utilities and settings in `common/` directory
- **Unified Development**: Both services in one repository for easier coordination
- **Atomic Changes**: Changes affecting both services can be committed together
- **Preserved History**: Git history from both original repositories maintained via subtree

### Independent Deployment
- Each service has its own Dockerfile for separate container builds
- Separate dependencies managed via `requirements/*.txt` files
- Each service maintains its own SECRET_KEY and configuration
- Services use external databases (not shared)

### Future Architecture
- QR Code service will authenticate against Users service (not yet implemented)
- API Gateway will provide unified entry point with `/users/` and `/qr-code/` prefixes
- More microservices can be added following the same pattern

## Key Files and Directories

```
ziptrigo-apps/
├── common/                          # Shared code
│   ├── __init__.py
│   └── settings/
│       ├── __init__.py
│       └── base.py                  # Shared Django settings (middleware, validators, etc.)
│
├── users/                           # Users service
│   ├── config/
│   │   ├── settings.py             # Imports from common.settings.base, users-specific config
│   │   ├── urls.py                 # URL routing with /users/ prefix support
│   │   ├── wsgi.py
│   │   └── asgi.py
│   ├── src/users/                  # Application code
│   ├── tests/                      # Tests (pytest)
│   ├── admin/                      # Admin utilities (lint, test commands)
│   ├── Dockerfile                  # Multi-stage build for minimal image size
│   ├── manage.py
│   └── .env.dev                    # Development environment variables
│
├── qr_code/                        # QR Code service
│   ├── config/
│   │   ├── settings.py            # Imports from common.settings.base, qr_code-specific config
│   │   ├── urls.py                # URL routing with /qr-code/ prefix support
│   │   ├── wsgi.py
│   │   └── asgi.py
│   ├── src/qr_code/               # Application code
│   ├── tests/                     # Tests (pytest)
│   ├── admin/                     # Admin utilities (lint, test commands)
│   ├── media/                     # User-uploaded media files
│   ├── staticfiles/               # Collected static files
│   ├── Dockerfile                 # Multi-stage build for minimal image size
│   ├── manage.py
│   └── .env.dev                   # Development environment variables
│
├── requirements/                   # Python dependencies
│   ├── base.txt                   # Shared: Django, Jazzmin
│   ├── users.txt                  # Users-specific: django-ninja, django-ninja-jwt
│   └── qr_code.txt               # QR Code-specific: django-ninja-extra, whitenoise, etc.
│
├── docker-compose.yml             # Orchestrates both services
├── README.md                      # User-facing documentation
└── WARP.md                       # This file (AI agent context)
```

## Development Patterns

### Settings Architecture
- **common/settings/base.py**: Contains shared Django configuration
  - Common middleware, password validators, internationalization
  - Template context processors, installed apps (base Django + Jazzmin)
- **Service settings**: Import from common base and extend/override
  - Each service adds `sys.path.insert(0, str(PROJECT_ROOT.parent))` for common imports
  - Each service defines its own SECRET_KEY, INSTALLED_APPS, DATABASES
  - Use pattern: `from common.settings.base import COMMON_MIDDLEWARE`

### Django Configuration
- Both services use Django 6.0+
- Python 3.12+ with type hints (Python 3.12 syntax: `str | None` instead of `Optional[str]`)
- PEP 8 convention with 100-column limit
- Single-quoted strings except triple-double-quoted docstrings

### URL Configuration
- Services currently run standalone on different ports (8010, 8020)
- URL files prepared for API gateway with commented-out prefixed patterns
- To enable prefixes: uncomment `path('users/', include(base_patterns))` pattern

### Docker Setup
- Multi-stage builds: builder stage + slim runtime stage
- Python 3.13-slim base images for minimal size
- Dependencies installed with `--user` flag in builder, copied to runtime
- PYTHONPATH set to `/app` for common imports
- Volumes mount both service code and common code

### Testing
- Each service uses pytest
- Test structure: `tests/api/`, `tests/unit/`, `tests/common/`
- Factories for test data (e.g., `tests/factories.py` in users)
- Run tests from service directory: `cd users && pytest`

### Admin Utilities
- Both services have `admin/` directory with invoke/typer-based utilities
- Common commands: `server.py`, `test.py`, `lint.py`, `pip.py`
- QR Code has additional: `aws.py`, `db.py`, `email.py`, `openapi.py`, `qrcode.py`

## Common Gotchas

1. **Import Path**: Services must add parent directory to sys.path to import from common
   ```python
   sys.path.insert(0, str(PROJECT_ROOT.parent))
   from common.settings.base import COMMON_MIDDLEWARE
   ```

2. **Separate SECRET_KEYs**: Each service has its own SECRET_KEY, don't share

3. **External Databases**: No database containers in docker-compose, services expect external DBs

4. **QR Code Environment**: QR Code service uses custom environment selection logic via `src.qr_code.common.environment.select_env()`

5. **Static Files**: Users service uses basic Django static files, QR Code uses WhiteNoise

6. **Media Files**: Only QR Code service handles media files (user uploads)

## When Making Changes

### Adding Shared Code
1. Place in `common/` directory
2. Create proper `__init__.py` files for packages
3. Update both services to import if needed
4. Consider backward compatibility

### Adding Service-Specific Code
1. Work within the service directory (`users/` or `qr_code/`)
2. Follow existing patterns (src/, tests/, admin/)
3. Update service-specific settings/urls as needed
4. Update service Dockerfile if new dependencies

### Adding Dependencies
1. Determine if shared or service-specific
2. Add to appropriate requirements file:
   - `requirements/base.txt` for shared
   - `requirements/users.txt` or `requirements/qr_code.txt` for service-specific
3. Use `-r base.txt` in service files to include shared deps
4. Rebuild Docker images: `docker-compose build`

### Running Services
- **Docker**: `docker-compose up` (both) or `docker-compose up users` (one)
- **Local**: `cd users && python manage.py runserver 8010`
- **Tests**: `cd users && pytest`

## Migration Guide (for reference)

This repo was created via:
1. `git subtree add --prefix=users users-repo/main --squash`
2. `git subtree add --prefix=qr_code qr-code-repo/main --squash`
3. Created `common/` for shared code
4. Updated settings to import from common base
5. Created unified requirements structure
6. Created Dockerfiles and docker-compose.yml
7. Updated URL configurations for future API gateway

## Future Work

- [ ] Implement cross-service authentication (QR Code → Users)
- [ ] Add API Gateway (nginx/Traefik) with proper routing
- [ ] Extract more shared utilities to common/
- [ ] Migrate from requirements.txt to unified pyproject.toml
- [ ] Add shared database utilities if services need to share data
- [ ] Consider shared logging/monitoring infrastructure

## Contact

For questions about this architecture, refer to commit history or original service documentation in respective directories.
