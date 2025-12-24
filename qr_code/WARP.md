# QR Code Generator Project

## Project Overview
A Python project for generating and manipulating QR codes.

## Goals
- Create a flexible QR code generation service
- Support main QR code format
- Provide API, CLI and programmatic interfaces

## Tech Stack
- Python 3.13
- Django 6.0 + Django Ninja for async API (JWT auth, htmx + Tailwind templates)
- Django Ninja JWT for Bearer token authentication (access + refresh tokens)
- Pydantic v2 for request/response validation and serialization
- Async/await throughout the API layer
- typer for CLI interface
- segno package for QR code generation
- Email: configurable via `EMAIL_BACKENDS` (comma-separated); SES and console backends supported
- RDBMS for data storage (SQLite dev, PostgreSQL production-ready)

## App name and project structure
The app name is `qr_code`.
The app code is located under `src/qr_code`.
All references should reflect this structure.
Database tables related to the app have the prefix `qr_code`.
Models are stored under `src/qr_code/models`, each model in its own file.
API views live under `src/qr_code/api`, services under `src/qr_code/services`, and HTML templates under `src/qr_code/templates`.

## Current Status
**Django Ninja Migration Complete!** Migrated from Django REST Framework to Django Ninja with full async support.

### Migration Highlights
- ✅ Replaced DRF with Django Ninja async API
- ✅ Migrated from session auth to JWT Bearer tokens (django-ninja-jwt)
- ✅ All API endpoints converted to async with Pydantic v2 schemas
- ✅ JWT tokens for email confirmation and password reset (stateless)
- ✅ Frontend updated to use JWT with localStorage and automatic token refresh
- ✅ Services converted to async (email, password reset, QR generation)
- ✅ Fresh database migrations (old TimeLimitedToken model removed)
- ✅ Test infrastructure updated for Django Ninja async client

### Features
- **Authentication**: JWT-based with `/api/auth/*` endpoints
  - Login returns access + refresh tokens (60min / 7 days)
  - Automatic token refresh on 401 errors
  - Email confirmation and password reset use JWT tokens
- **Frontend**: `auth.js` handles JWT storage in localStorage
  - htmx requests automatically include Authorization header
  - Login/register forms use Auth.login() and Auth.signup()
  - Logout clears tokens and redirects
- **API Layer**: Full async/await with Django Ninja
  - All endpoints use async def
  - Pydantic v2 schemas for validation
  - Auto-generated OpenAPI docs at `/api/docs`
- **Dashboard**: `/dashboard/` lists QR codes with search, sort, and actions
- **QR Management**: Create, edit (name only), preview, soft delete
- **URL Shortening**: Built-in with `/api/go/{short_code}` redirect

## Next Steps
1. Update existing tests to use Django Ninja async test client
2. Test JWT authentication flow (login, token refresh, protected endpoints)
3. Test email confirmation with JWT tokens
4. Test password reset with JWT tokens
5. Test async QR code CRUD operations
6. Manual testing of web UI with JWT auth
7. Remove old DRF files (auth.py, qrcode.py, serializers.py)

## Notes
### Email configuration
Email backends are configured via `EMAIL_BACKENDS` (comma-separated). The app will send the same email through all configured backends.
Examples:
- Development: `EMAIL_BACKENDS=console`
- Production: `EMAIL_BACKENDS=ses`
- Mixed: `EMAIL_BACKENDS=ses,console`

SES configuration (when using the `ses` backend):
- `AWS_REGION`
- `AWS_SES_SENDER`

### Coding guidelines
- Use PEP8
- Use docstrings
- Use type hints. Type hints to use python 3.13 standards, ex. `str | None` instead of
  `Optional[str]` and `list[str]` instead of `List[str]`. Functions that don't return anything
  (or return `None`) should not have a return type hint.
- Use static type checking
- Use black
- Use isort
- Use flake8
- Use mypy
- Use single quotes for strings, except when triple quotes are necessary (ex: docstrings), in which
  case use double quotes
- Use 4 spaces for indentation
- Use 100 characters per line

### Frontend design and behavior preferences
Frontend design language is minimal with a green-gray color palette from `src/qr_code/static/images/logo_128x128.png`.
All pages support light/dark mode including login and register.
Form validation includes existence checks and email format validation before backend calls.
On valid login, redirect to the dashboard page.
Include a 'remember me' checkbox with corresponding behavior.
User-facing error messages are simple; developer-facing errors are verbose and technical.
CSS stack uses Tailwind.

The QR dashboard and editor follow this stack:
- `dashboard.html` shows the user's QR codes with search, clear search button, and a "Generate QR code" button.
  - Each row has a dropdown menu (three-dots icon) with Edit option.
- `qrcode_editor.html` handles both create and edit modes:
  - Create mode (`/qrcodes/create/`): name, text/URL, format (PNG/SVG/PDF), short URL toggle, preview, and save.
  - Edit mode (`/qrcodes/edit/<id>/`): editable name, read-only content display, QR preview, and save.
- Preview uses `POST /api/qrcodes/preview` (no DB row).
- Create saves via `POST /api/qrcodes/`, edit updates via `PUT /api/qrcodes/<id>/`.
- Both redirect back to `/dashboard/` after successful save.

### Documentation
Create documentation in MD format under the `docs` directory, all lowercased files.
Only README.md remains in the root directory, uppercased.

### Functionality
1. Create QR code
When creating a QR code, the user should be able to specify the following:
- QR code type (URL or TEXT) - required field to categorize the content
- QR code format
- QR code content
- QR code size
- QR code error correction level
- QR code border
- QR code background color
- QR code foreground color
- QR code logo
- QR code logo size
- QR code data
- If the data is a url, then the user should be able to select whether the
  QR code will have that exact url, or a shortened url. The shortened url
  should be generated using functionality in this project that will point to
  an api endpoint that will then retrieve the original url from the DB.

2. API interface
The backend service is implemented in Django. The APIs are:
   2.1. Create QR code: A POST endpoint with the payload described above.
   2.2. Edit QR code: A PUT endpoint that allows updating the QR code name only.
        Other fields (content, format, etc.) are read-only.
   2.3. Retrieve QR code: An endpoint that will save in the DB the number
        of times the QR code was read (ie, the endpoint was called) and
        forward the user to the original url.

3. CLI interface
The CLI interface will be implemented using the typer library and have a
similar interface to the API.

4. Email confirmation
- On registration via `POST /api/signup`, a confirmation email is sent (does not auto-login)
- HTML: `/confirm-email/<token>/` validates token and redirects to success or expired page
- `/confirm-email/success/` shows confirmation success with Login button
- Expired confirmation page offers resend functionality via `POST /api/resend-confirmation`
- Tokens are single-use and time-limited by `EMAIL_CONFIRMATION_TOKEN_TTL_HOURS` (default 48)
- Login is blocked for unconfirmed users with error message

5. Forgot password
- HTML: `/forgot-password/` to request email; `/reset-password/<token>/` to set a new password
- API: `POST /api/forgot-password` (accepts `email`) and `POST /api/reset-password`
  (accepts `token`, `password`, `password_confirm`); responses avoid leaking whether the
  email exists
- Tokens are single-use and time-limited by `PASSWORD_RESET_TOKEN_TTL_HOURS` (default 4)

### Authentication
JWT Bearer token authentication using django-ninja-jwt with email confirmation.

**Token Management**:
- POST /api/token/pair - Get access + refresh tokens (60min / 7 days)
- POST /api/token/refresh - Refresh access token
- POST /api/token/verify - Verify token validity

**Auth Endpoints**:
- POST /api/auth/signup (name, email, password) - Creates user and sends JWT confirmation email
- POST /api/auth/login (email, password) - Returns JWT tokens (requires confirmed email)
- GET /api/auth/me - Get current user (requires JWT)
- POST /api/auth/confirm-email (token) - Confirms email using JWT token
- POST /api/auth/resend-confirmation (email) - Resends JWT confirmation email
- POST /api/auth/forgot-password (email) - Sends JWT password reset email
- POST /api/auth/reset-password (token, password, password_confirm) - Resets password using JWT
- PUT /api/auth/account - Update user profile (requires JWT)
- POST /api/auth/change-password - Change password (requires JWT)

**Implementation Details**:
- JWT tokens stored in localStorage (frontend)
- Authorization header: `Bearer <access_token>`
- Automatic token refresh on 401 errors
- Email confirmation tokens: Custom `EmailConfirmationToken` class (48h lifetime)
- Password reset tokens: Custom `PasswordResetToken` class (4h lifetime)
- No database storage for tokens (stateless JWT)

### QR Code Management
**All endpoints require JWT Bearer token in Authorization header**

- GET /api/qrcodes/ - List user's QR codes (async, Pydantic response)
- POST /api/qrcodes/ - Create QR code (async QR generation)
- GET /api/qrcodes/{id} - Get QR code details
- PUT /api/qrcodes/{id} - Update QR code name
- PATCH /api/qrcodes/{id} - Partial update
- DELETE /api/qrcodes/{id} - Soft delete (204 response)
- POST /api/qrcodes/preview - Generate preview (async)
- GET /api/go/{short_code} - Public redirect endpoint

**Implementation**:
- All endpoints use async def
- Pydantic schemas for validation (QRCodeCreateSchema, QRCodeUpdateSchema, QRCodeSchema)
- Ownership validation via JWT user context
- Async QR generation with sync_to_async
- Soft delete with deleted_at timestamp
- QR Code Types: url or text (QRCodeType TextChoices)

### HTML Pages
|- `/dashboard/` - List QR codes with search (with clear button), sort, and dropdown actions per row
  - Dropdown menu includes Edit and Delete (with confirmation modal) actions
  - Delete confirmation modal displays QR code name and has Cancel/Delete buttons
  - Header includes account settings link
|- `/account/` - Account settings page for authenticated users with:
  - **Profile Information** section: editable name and email, read-only username; changing email requires confirmation
  - **Change Password** section: current password verification with password strength validation (min 6 chars, 1 digit)
  - Password visibility toggle buttons for all password fields
  - Link back to dashboard
- `/qrcodes/create/` - Create new QR code with qr_type selection (name 'qrcode-create')
- `/qrcodes/edit/<uuid:qr_id>/` - Edit existing QR code name; qr_type is read-only (name 'qrcode-edit')
- Both create/edit use `qrcode_editor.html` template with conditional rendering based on `qrcode` context

### Testing
Tests are located in `tests/` directory using pytest.
- test_api.py includes tests for:
  - QR code creation with various options
  - QR code listing and retrieval
  - QR code updates (name only, ownership validation, empty name validation)
  - Soft delete behavior (deleted_at field, 404 responses, list filtering)
  - Soft-deleted QR codes not appearing in list endpoint
  - Soft-deleted QR codes returning 404 on detail/update operations
  - Double-delete idempotency
  - Redirect endpoint behavior for soft-deleted QR codes
  - Authentication and authorization
  - URL shortening functionality
