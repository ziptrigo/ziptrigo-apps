# Users Service

Centralized Single Sign-On (SSO) authentication and authorization service for ZipTrigo web applications.

## Overview

The Users service provides:
- **Single Sign-On (SSO)** across multiple first-party web apps
- **Centralized identity, roles, and permissions** management
- **JWT-based authentication** for users
- **Client ID/Secret authentication** for services
- **Credit management system** for user accounts
- **RESTful API** under `/api`

## Tech Stack

- Backend: Django 6.0, Python 3.13, Django Ninja
- Frontend: HTMX (minimal landing page)
- Database: SQLite (development) / PostgreSQL (production)

## Installation

1. Create and activate virtual environment:
```
python -m venv .venv
.venv\Scripts\activate  # Windows
# or
source .venv/bin/activate  # Linux/Mac
```

2. Install dependencies:
```
pip install -r requirements.txt
```

3. Run migrations:
```
python manage.py migrate
```

4. Create superuser:
```
python manage.py createsuperuser --email admin@example.com
```

5. Start development server:
```
python manage.py runserver
```

## API Endpoints

### API Documentation
- `/api/docs` - Interactive Swagger UI documentation (supports Authorize with Bearer tokens)
- `/api/openapi.json` - OpenAPI 3 schema in JSON format

### Authentication

- Auth method: JWT via `Authorization: Bearer <token>`.
- Service-to-service auth: `X-Client-Id` and `X-Client-Secret` headers (for endpoints that use `ServiceAuthentication`).

**Login (open endpoint)**
```
POST /api/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}

Response 200:
{
  "access_token": "eyJ...",
  "expires_in": 1209600,
  "token_type": "Bearer"
}
```

### Services (Admin only)

Create a service to obtain `client_id` and `client_secret` the first time.

- `POST /api/services` - Create a new service
- `GET /api/services` - List all services
- `GET /api/services/{id}` - Get service details
- `PATCH /api/services/{id}` - Update service

### Permissions & Roles (Admin only)

- `POST /api/services/{service_id}/permissions` - Create permission for service
- `GET /api/services/{service_id}/permissions` - List service permissions
- `POST /api/services/{service_id}/roles` - Create role for service
- `GET /api/services/{service_id}/roles` - List service roles

### User Management (Admin only)

- `POST /api/services/{service_id}/users` - Create/assign user to service
- `GET /api/users/{user_id}` - Get user details
- `PATCH /api/users/{user_id}` - Update user
- `DELETE /api/users/{user_id}` - Soft delete user
- `POST /api/users/{user_id}/deactivate` - Deactivate user
- `POST /api/users/{user_id}/reactivate` - Reactivate user
- `GET /api/users/{user_id}/services` - List user's service assignments
- `PATCH /api/users/{user_id}/services/{service_id}` - Update user roles/permissions
- `DELETE /api/users/{user_id}/services/{service_id}` - Remove service assignment

### Credits Management (Admin only)

- `POST /api/users/{user_id}/credits` - Add or remove credits from user account
- `GET /api/users/{user_id}/credits` - Get user's current credit balance

**Transaction Types:**
- `purchase` - User purchased credits
- `spend` - User spent credits
- `adjustment` - Manual adjustment by admin
- `refund` - Refund to user

**Example: Add credits**
```
POST /api/users/{user_id}/credits
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "transaction_type": "purchase",
  "amount": 100,
  "description": "Initial purchase"
}

Response 201:
{
  "id": 1,
  "user_id": "uuid",
  "amount": 100,
  "type": "purchase",
  "description": "Initial purchase",
  "created_at": "2025-12-29T04:13:22Z"
}
```

**Example: Spend credits**
```
POST /api/users/{user_id}/credits
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "transaction_type": "spend",
  "amount": -30,
  "description": "Used for service"
}
```

## Authentication Methods

### User Authentication (JWT)

Include JWT token in Authorization header:
```
Authorization: Bearer <token>
```

### Service Authentication

Include client credentials in headers:
```
X-Client-Id: <client_id>
X-Client-Secret: <client_secret>
```

## JWT Payload Structure

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "iat": 1234567890,
  "exp": 1234567890,
  "global_permissions": ["admin", "manage_users"],
  "global_roles": ["super_admin"],
  "services": {
    "service-uuid": {
      "permissions": ["read", "write"],
      "roles": ["editor"]
    }
  }
}
```

## Configuration
Key settings live in `config/settings.py`.
- Custom user model: `src.users.models.user.User` (set via `AUTH_USER_MODEL`).
- DRF defaults: `IsAuthenticated`; admin endpoints layer `IsAdminUser`.
- JWT:
  - `JWT_SECRET` (set via environment in production)
  - `JWT_ALGORITHM` (default: HS256)
  - `JWT_EXP_DELTA_SECONDS` (default: 2 weeks)

## Testing

Default superuser credentials for local testing (if you created as shown above):
- Email: `admin@example.com`
- Password: `admin123`

Quick manual test sequence:
1) Create a service (admin-only): `POST /api/services` — returns generated `client_id` and `client_secret`.
2) Create or assign a user to that service: `POST /api/services/{service_id}/users`.
3) Login as that user: `POST /api/auth/login` — receive JWT.
4) Call protected endpoints with `Authorization: Bearer <token>`.

Django admin: http://127.0.0.1:8020/admin/

## Project Structure
```
config/                   # Django project config (settings, urls, wsgi/asgi)
src/
  users/                  # Django app (installed as `src.users`)
    models/               # Django models package (one model per file)
      __init__.py         # Re-exports models for `from src.users.models import ...`
      service.py
      permission.py
      role.py
      role_permission.py
      user.py
      user_service_assignment.py
      user_service_role.py
      user_service_permission.py
      user_global_role.py
      user_global_permission.py
    schemas/              # Pydantic v2 schemas split by domain
      __init__.py
      auth.py
      services.py
      users.py
      roles_permissions.py
    routers/              # Django Ninja routers split by domain
      __init__.py
      auth.py
      services.py
      users.py
      roles_permissions.py
    admin.py              # Django admin registrations
    api.py                # Main NinjaAPI instance
    auth.py               # Django Ninja authentication classes
    backends.py           # Django authentication backend(s)
    jwt.py                # JWT build/verify helpers
    templates/            # Minimal UI templates
    static/               # Static assets (if used)
manage.py
pyproject.toml
README.md
WARP.md
```

## License

See LICENSE file for details.
