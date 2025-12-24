# QR Code Generator - Setup Guide

## Overview
A complete QR code generation service with Django REST API, JWT authentication, URL shortening, and CLI interface.

## Features
- ✅ RESTful API with JWT authentication
- ✅ QR code generation with full customization (colors, size, error correction, format)
- ✅ Multiple output formats: PNG (with transparency), SVG, JPEG
- ✅ URL shortening with redirect tracking
- ✅ Scan analytics (track number of scans)
- ✅ CLI interface using typer
- ✅ PostgreSQL-ready (currently using SQLite)

## Requirements
- Python 3.13
- Virtual environment (.venv313)

## Installation

### 1. Install dependencies
```powershell
.venv313\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure environment
Create a `.env` file based on `.env.example`:
```powershell
cp .env.example .env
```

Edit `.env` and set your configuration (the defaults should work for development).

### 3. Run migrations
```powershell
python manage.py migrate
```

### 4. Create a superuser
```powershell
python manage.py createsuperuser
```

### 5. Create media directory
```powershell
New-Item -ItemType Directory -Force -Path media\qrcodes
```

## Code Style

This project uses Black and isort (configured in pyproject.toml).

```powershell
# Sort imports
isort .

# Format code
black .
```

## Running the Server

```powershell
python manage.py runserver
```

The API will be available at `http://localhost:8010`

## API Endpoints

### Authentication
- **POST** `/api/token/` - Obtain JWT token
  ```json
  {
    "username": "your_username",
    "password": "your_password"
  }
  ```
  Response:
  ```json
  {
    "access": "your-access-token",
    "refresh": "your-refresh-token"
  }
  ```

- **POST** `/api/token/refresh/` - Refresh JWT token

### QR Codes
All QR code endpoints require authentication (Bearer token in Authorization header).

- **POST** `/api/qrcodes/` - Create a new QR code
  ```json
  {
    "url": "https://example.com",
    "use_url_shortening": true,
    "qr_format": "png",
    "size": 10,
    "error_correction": "M",
    "border": 4,
    "background_color": "white",
    "foreground_color": "black"
  }
  ```
  
  Or with custom data:
  ```json
  {
    "data": "Hello World!",
    "qr_format": "svg",
    "background_color": "transparent"
  }
  ```

- **GET** `/api/qrcodes/` - List all your QR codes
- **GET** `/api/qrcodes/{id}/` - Get specific QR code details
- **DELETE** `/api/qrcodes/{id}/` - Delete a QR code

### Redirect Endpoint (Public)
- **GET** `/go/{short_code}/` - Redirect to original URL and track scan

## CLI Usage

### 1. Login
```powershell
python cli.py login your_username your_password
```

### 2. Create QR Code

With URL:
```powershell
python cli.py create --url https://example.com --shorten
```

With custom data:
```powershell
python cli.py create --data "Hello World" --format svg
```

Full options:
```powershell
python cli.py create `
  --url https://example.com `
  --format png `
  --size 15 `
  --error M `
  --border 4 `
  --bg-color transparent `
  --fg-color "#0000FF" `
  --shorten
```

### 3. List QR Codes
```powershell
python cli.py list
```

### 4. Get QR Code Details
```powershell
python cli.py get <qr-code-id>
```

### 5. Delete QR Code
```powershell
python cli.py delete <qr-code-id>
```

## Configuration Options

### QR Code Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | - | URL to encode (mutually exclusive with `data`) |
| `data` | string | - | Any data to encode (mutually exclusive with `url`) |
| `qr_format` | string | `"png"` | Output format: `png`, `svg`, `jpeg` |
| `size` | integer | `10` | Scale factor for QR code size |
| `error_correction` | string | `"M"` | Error correction level: `L` (7%), `M` (15%), `Q` (25%), `H` (30%) |
| `border` | integer | `4` | Border size (quiet zone) |
| `background_color` | string | `"white"` | Background color (name, hex, or `"transparent"`) |
| `foreground_color` | string | `"black"` | Foreground color (name or hex) |
| `use_url_shortening` | boolean | `false` | Enable URL shortening and tracking |

### Colors
- **Named colors**: `white`, `black`, `red`, `blue`, `green`, etc.
- **Hex colors**: `#FF0000`, `#00FF00`, etc.
- **Transparent**: Use `"transparent"` for PNG background transparency

## Database Migration to PostgreSQL

When ready to switch to PostgreSQL:

1. Install PostgreSQL adapter:
```powershell
pip install psycopg2-binary
```

2. Update `config/settings.py`:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'qrcode_db',
        'USER': 'your_username',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

3. Run migrations:
```powershell
python manage.py migrate
```

## Project Structure

```
qr_code/
├── config/              # Django project settings
│   ├── settings.py      # Main configuration
│   ├── urls.py          # Root URL configuration
│   └── wsgi.py          # WSGI configuration
├── src/                 # Main app
│   ├── models.py        # QRCode model
│   ├── serializers.py   # DRF serializers
│   ├── views.py         # API views
│   ├── services.py      # QR code generation service
│   ├── urls.py          # App URL configuration
│   └── admin.py         # Admin configuration
├── media/               # Generated QR code images
│   └── qrcodes/
├── cli.py               # CLI interface
├── manage.py            # Django management script
├── requirements.txt     # Python dependencies
└── .env                 # Environment configuration
```

## Admin Interface

Access the Django admin at `http://localhost:8010/admin/` to:
- View all QR codes
- Monitor scan statistics
- Manage users
- Perform bulk operations

## Troubleshooting

### Media files not accessible
Make sure you've created the media directory:
```powershell
New-Item -ItemType Directory -Force -Path media\qrcodes
```

### CLI authentication fails
1. Verify the server is running
2. Check your username and password
3. Ensure the API_BASE_URL in `.env` is correct

### QR code generation fails
1. Check that Pillow is installed: `pip install Pillow`
2. Verify the media directory exists and is writable

## Next Steps

1. ✅ Basic functionality is complete
2. 🔄 Add logo embedding support (future enhancement)
3. 🔄 Add batch QR code generation
4. 🔄 Add QR code templates
5. 🔄 Add export functionality (bulk download)

## Testing

To test the API with curl:

```powershell
# Get token
$response = Invoke-RestMethod -Uri "http://localhost:8010/api/token/" -Method Post -Body (@{username="admin"; password="your_password"} | ConvertTo-Json) -ContentType "application/json"
$token = $response.access

# Create QR code
Invoke-RestMethod -Uri "http://localhost:8010/api/qrcodes/" -Method Post -Headers @{Authorization="Bearer $token"} -Body (@{url="https://example.com"; use_url_shortening=$true} | ConvertTo-Json) -ContentType "application/json"
```
