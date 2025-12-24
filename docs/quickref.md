# Quick Reference Card

## Setup (First Time)

```powershell
# Activate virtual environment
.venv313\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create admin user
python manage.py createsuperuser

# Test setup
python test_setup.py

# Start server
python manage.py runserver
```

## Server Management

```powershell
# Start development server
python manage.py runserver

# Create migrations (after model changes)
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Django shell
python manage.py shell
```

## CLI Commands

```powershell
# Login
python cli.py login <username> <password>

# Create QR code from URL
python cli.py create --url https://example.com

# Create with URL shortening
python cli.py create --url https://example.com --shorten

# Create from data
python cli.py create --data "Hello World"

# Create with customization
python cli.py create --url https://example.com --format svg --bg-color transparent --size 15

# List QR codes
python cli.py list

# Get QR code details
python cli.py get <qr-id>

# Delete QR code
python cli.py delete <qr-id>
```

## API Endpoints

### Authentication
```
POST /api/token/
POST /api/token/refresh/
```

### QR Codes (Authenticated)
```
POST   /api/qrcodes/        Create QR code
GET    /api/qrcodes/        List QR codes
GET    /api/qrcodes/{id}/   Get QR code
DELETE /api/qrcodes/{id}/   Delete QR code
```

### Redirect (Public)
```
GET /go/{short_code}/       Redirect and track
```

## PowerShell API Examples

### Get Token
```powershell
$response = Invoke-RestMethod -Uri "http://localhost:8010/api/token/" `
  -Method Post `
  -Body (@{username="admin"; password="password"} | ConvertTo-Json) `
  -ContentType "application/json"
$token = $response.access
```

### Create QR Code
```powershell
Invoke-RestMethod -Uri "http://localhost:8010/api/qrcodes/" `
  -Method Post `
  -Headers @{Authorization="Bearer $token"} `
  -Body (@{
    url="https://example.com"
    use_url_shortening=$true
    qr_format="png"
    background_color="white"
  } | ConvertTo-Json) `
  -ContentType "application/json"
```

### List QR Codes
```powershell
Invoke-RestMethod -Uri "http://localhost:8010/api/qrcodes/" `
  -Method Get `
  -Headers @{Authorization="Bearer $token"}
```

## QR Code Parameters

| Parameter | Type | Default | Options |
|-----------|------|---------|---------|
| `url` | string | - | Any valid URL |
| `data` | string | - | Any text data |
| `qr_format` | string | `png` | `png`, `svg`, `pdf` |
| `size` | integer | `10` | 1-50 |
| `error_correction` | string | `M` | `L`, `M`, `Q`, `H` |
| `border` | integer | `4` | 0-20 |
| `background_color` | string | `white` | color name, hex, `transparent` |
| `foreground_color` | string | `black` | color name, hex |
| `use_url_shortening` | boolean | `false` | `true`, `false` |

## File Locations

```
media/qrcodes/           Generated QR code images
db.sqlite3               Database file
~/.qrcode_token          CLI authentication token
.env                     Environment configuration
```

## URLs

```
http://localhost:8010/admin/           Django admin interface
http://localhost:8010/api/qrcodes/     API endpoint
http://localhost:8010/go/{code}/       Short URL redirect
http://localhost:8010/media/qrcodes/   QR code images
```

## Troubleshooting

### Server won't start
```
# Check if migrations are applied
python manage.py showmigrations

# Reapply migrations
python manage.py migrate
```

### CLI login fails
```powershell
# Check server is running
curl http://localhost:8010/api/token/

# Delete old token
Remove-Item ~\.qrcode_token
```

### QR code not generating
```powershell
# Verify media directory exists
New-Item -ItemType Directory -Force -Path media\qrcodes

# Check permissions
Get-Acl media\qrcodes
```

## Configuration

Edit `.env` file:
```env
BASE_URL=http://localhost:8010
API_BASE_URL=http://localhost:8010
DEBUG=True
```

## Formatting & Imports

```
# Sort imports per pyproject.toml
isort .

# Format code per pyproject.toml
black .
```

## Testing

```
# Run setup test
python test_setup.py

# Django tests
python manage.py test

# Check migrations
python manage.py showmigrations
```
