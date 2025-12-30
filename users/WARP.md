# Users Service Project

## Project Overview
Centralized Single Sign-On (SSO) service for ZipTrigo applications. Provides JWT-based user authentication, service (machine-to-machine) authentication via client_id/client_secret, and a minimal HTMX landing page.

## Goals
- Single source of truth for user identity
- Issue JWTs that client apps can validate
- Provide a clean REST API under `/api`
- Manage service-level credentials for first-party apps
- Manage user credit balances with transaction history

## Tech Stack
- Python 3.13
- Django 6.0 + Django Ninja
- Pydantic v2 (schemas/validation)
- JWT (PyJWT)
- Templates: HTMX (minimal hello page)
- DB: SQLite for dev; PostgreSQL recommended for prod

## App and Project Structure
The Django project config lives in `config/`, and the Django app is `src/users` (installed as `src.users`).
- Models: `src/users/models/` is a package (one model per file). Import via `from src.users.models import ...`.
- Schemas: `src/users/schemas/` contains Pydantic v2 schemas grouped by domain.
- Routers: `src/users/routers/` contains Django Ninja routers grouped by domain.
- API: `src/users/api.py` is the main NinjaAPI instance that registers all routers.
- Auth/JWT helpers: `src/users/auth.py` (Ninja auth classes), `src/users/backends.py`, `src/users/jwt.py`.
- Templates: `src/users/templates/`.

## Current Status
- Custom `User` model with email as username
- User credit system with transaction ledger:
  - `User.credits` field stores current balance (default: 0)
  - `CreditTransaction` model provides immutable audit trail
  - Transaction types: purchase, spend, adjustment, refund
  - Atomic credit updates with database transactions
- JWT utilities: `src/users/tokens.py` builds tokens with user ID and email
- Django Ninja authentication:
  - `JWTAuth` class (Authorization: Bearer <token>)
  - `AdminAuth` class (extends JWTAuth, requires is_staff)
- Pydantic v2 schemas for all request/response validation
- Endpoints (admin-only unless noted):
  - POST `/api/auth/login` (open) — returns JWT for active users
  - Services: POST/GET `/api/services/`, GET/PATCH `/api/services/{id}`
  - Users:
    - POST `/api/users/` - Create user
    - GET/PATCH/DELETE `/api/users/{user_id}` (soft delete)
    - POST `/api/users/{user_id}/deactivate`, `/api/users/{user_id}/reactivate`
  - Credits management:
    - POST `/api/users/{user_id}/credits` — add/remove credits with transaction record
    - GET `/api/users/{user_id}/credits` — get user's current credit balance
- Minimal UI: `/` renders `hello.html`
- API Documentation: `/api/docs` (interactive Swagger UI)

## Configuration
Key settings live in `config/settings.py`:
- Custom user model: `src.users.models.user.User` (set via `AUTH_USER_MODEL`).
- Django Ninja: authentication handled per-endpoint via `auth=` parameter.
- JWT:
  - `JWT_SECRET` (set via env in prod)
  - `JWT_ALGORITHM` (default: HS256)
  - `JWT_EXP_DELTA_SECONDS` (default: 14 days)
- Email-based auth backend: `src.users.backends.EmailBackend`.

## How to Run (dev)
1. Create venv and install deps: `pip install -r admin/requirements/requirements.txt`
2. Migrate: `python manage.py migrate`
3. Create superuser: `python manage.py createsuperuser --email admin@example.com`
4. Run: `python manage.py runserver`
5. Visit API docs: `http://127.0.0.1:8000/api/docs`

## Next Steps
1. Add permission checks and audit logging for admin endpoints
2. Add pagination/filters for list endpoints
3. Add tests (unit + API) and CI pipeline
4. Support key rotation and multiple JWT signing keys (kid)
5. Optional: Admin UI via HTMX

## Coding Guidelines
- PEP8 with 100-char line limit
- Strings are single quotes; docstrings use triple double quotes
- Python 3.12+ typing style (e.g., `str | None`)

## Documentation
- Keep root `README.md` up to date
- Additional docs live in `docs/` (lowercase filenames)
