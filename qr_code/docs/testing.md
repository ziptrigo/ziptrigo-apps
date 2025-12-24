# Testing Documentation

## Overview

This project uses **pytest** with **pytest-django** for comprehensive testing. We have achieved **98% code coverage** with 47 tests covering models, services, API endpoints, and integration workflows.

## Test Framework

- **pytest**: Modern, powerful testing framework
- **pytest-django**: Django integration for pytest
- **pytest-cov**: Code coverage reporting
- **factory-boy**: Test data generation (available for future use)

## Running Tests

### Run All Tests
```powershell
pytest
```

### Run Tests with Verbose Output
```powershell
pytest -v
```

### Run Specific Test File
```powershell
pytest tests/test_models.py
```

### Run Specific Test Class
```powershell
pytest tests/test_models.py::TestQRCodeModel
```

### Run Specific Test
```powershell
pytest tests/test_models.py::TestQRCodeModel::test_create_qrcode
```

### Run Tests by Marker
```powershell
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run everything except slow tests
pytest -m "not slow"
```

### Generate Coverage Report
```powershell
# Terminal report (uses coverage config from pyproject.toml)
pytest --cov=src --cov-report=term-missing

# HTML report
pytest --cov=src --cov-report=html
# Then open htmlcov/index.html
```

## Test Organization

```
tests/
├── __init__.py
├── test_models.py              # Unit tests for QRCode model
├── test_services.py            # Unit tests for QR code generation
├── test_api.py                 # Integration tests for API endpoints
└── test_setup_integration.py  # End-to-end workflow tests
```

## Test Coverage

Current coverage: **98%**

| Module | Coverage | Notes |
|--------|----------|-------|
| models.py | 98% | 1 line missed (edge case in save) |
| serializers.py | 98% | 1 line missed (serializer method) |
| services.py | 100% | Full coverage |
| urls.py | 100% | Full coverage |
| views.py | 97% | 1 line missed (error response) |
| admin.py | 91% | Admin display method |
| urls.py | 100% | Full coverage |

## Test Categories

### 1. Model Tests (`test_models.py`)

Tests for the QRCode model functionality:

- ✅ Basic model creation
- ✅ Default values
- ✅ URL shortening
- ✅ Short code generation and uniqueness
- ✅ Redirect URL generation
- ✅ Scan count tracking
- ✅ Format and error correction choices
- ✅ String representation

**Example:**
```python
def test_create_qrcode(user):
    qr = QRCode.objects.create(
        content='https://example.com',
        created_by=user,
        qr_format='png',
        image_file='test.png'
    )
    assert qr.content == 'https://example.com'
    assert qr.scan_count == 0
```

### 2. Service Tests (`test_services.py`)

Tests for QR code generation service:

- ✅ PNG generation
- ✅ SVG generation  
- ✅ PDF generation
- ✅ Custom colors
- ✅ Transparent backgrounds
- ✅ Custom sizes and borders
- ✅ All error correction levels
- ✅ Color parsing

**Example:**
```python
def test_generate_png_qrcode(user):
    qr = QRCode.objects.create(...)
    image_path = QRCodeGenerator.generate_qr_code(qr)
    assert image_path.endswith('.png')
    assert Path(settings.MEDIA_ROOT) / image_path).exists()
```

### 3. API Tests (`test_api.py`)

Tests for REST API endpoints:

**QRCode API:**
- ✅ Create with URL
- ✅ Create with data
- ✅ Create with URL shortening
- ✅ Authentication required
- ✅ Validation (missing data, both url and data)
- ✅ List QR codes
- ✅ User filtering
- ✅ Retrieve specific QR code
- ✅ Delete QR code
- ✅ Custom colors
- ✅ All formats

**Redirect Endpoint:**
- ✅ Valid short code redirect
- ✅ Scan count increment
- ✅ Invalid short code (404)
- ✅ Public access (no auth required)

**JWT Authentication:**
- ✅ Obtain token with valid credentials
- ✅ Invalid credentials (401)
- ✅ Refresh token

**Example:**
```python
def test_create_qrcode_with_url(authenticated_client):
    url = reverse('qrcode-list')
    data = {'url': 'https://example.com', 'qr_format': 'png'}
    response = authenticated_client.post(url, data, format='json')
    assert response.status_code == status.HTTP_201_CREATED
```

### 4. Integration Tests (`test_setup_integration.py`)

End-to-end workflow tests:

- ✅ Complete QR generation workflow
- ✅ URL shortening workflow
- ✅ Scan tracking workflow

**Example:**
```python
def test_complete_qr_generation_workflow():
    user = User.objects.create_user(...)
    qr = QRCode.objects.create(...)
    image_path = QRCodeGenerator.generate_qr_code(qr)
    assert image_path is not None
```

## Test Fixtures

Reusable fixtures defined in `conftest.py`:

```python
@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        username='testuser',
        password='testpass123'
    )

@pytest.fixture
def authenticated_client(api_client, user):
    """Provide an authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client

@pytest.fixture
def qr_code(user):
    """Create a test QR code."""
    return QRCode.objects.create(...)
```

## Markers

Custom pytest markers for organizing tests:

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slow-running tests

## Configuration

### pyproject.toml

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings"
python_files = ["tests.py", "test_*.py", "*_tests.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
testpaths = ["tests"]
addopts = [
  "--strict-markers",
  "--reuse-db",
  "--no-migrations",
  "-v",
]
markers = [
  "unit: Unit tests",
  "integration: Integration tests",
  "slow: Slow running tests",
]
```

### Database

Tests use pytest-django's database fixtures:
- `db` - Standard database access
- `transactional_db` - For tests requiring transactions
- `--reuse-db` flag to reuse test database between runs (faster)
- `--no-migrations` flag to use existing migrations (faster)

## Code Style

Run formatters before committing:

```powershell
isort .
black .
```

## Best Practices

### 1. Test Isolation
Each test is independent and doesn't rely on others:
```python
def test_something(user):  # Fresh user for each test
    qr = QRCode.objects.create(...)  # Fresh data
    # Test logic
```

### 2. Descriptive Names
```python
def test_create_qrcode_with_url_shortening():  # Clear what it tests
    ...
```

### 3. AAA Pattern
Arrange, Act, Assert:
```python
def test_increment_scan_count(qr_code):
    # Arrange
    initial_count = qr_code.scan_count
    
    # Act
    qr_code.increment_scan_count()
    
    # Assert
    assert qr_code.scan_count == initial_count + 1
```

### 4. Use Fixtures
Leverage pytest fixtures for setup:
```python
def test_with_fixture(authenticated_client, qr_code):
    # authenticated_client and qr_code are ready to use
    ...
```

## Continuous Integration

For CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Run tests
  run: |
pytest --cov=src --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v3
```

## Writing New Tests

### 1. Create Test File
```python
# tests/test_new_feature.py
import pytest
from qrcodes.models import QRCode

@pytest.mark.django_db
class TestNewFeature:
    def test_something(self, user):
        # Test logic
        assert True
```

### 2. Use Appropriate Markers
```python
@pytest.mark.unit
def test_pure_function():
    ...

@pytest.mark.integration
def test_api_endpoint(authenticated_client):
    ...
```

### 3. Test Edge Cases
```python
def test_url_shortening_with_existing_code(user):
    # Test collision handling
    ...

def test_invalid_format():
    # Test validation
    ...
```

## Debugging Tests

### Run with Print Statements
```powershell
pytest -s  # Don't capture output
```

### Run with PDB
```python
def test_something():
    import pdb; pdb.set_trace()
    # Test logic
```

### Run Last Failed
```powershell
pytest --lf  # Run only last failed tests
```

### Run Failed First
```powershell
pytest --ff  # Run failed tests first, then others
```

## Performance

### Current Performance
- 47 tests run in ~22 seconds
- Using `--reuse-db` and `--no-migrations` for speed
- Most time spent in QR code generation (file I/O)

### Optimization Tips
```powershell
# Parallel execution (requires pytest-xdist)
pytest -n auto

# Only run changed tests
pytest --testmon
```

## Common Issues

### Issue: Tests fail with database errors
**Solution**: Ensure migrations are applied:
```powershell
python manage.py migrate
```

### Issue: Coverage not showing
**Solution**: Run with coverage flags:
```powershell
pytest --cov=qrcodes --cov-report=term
```

### Issue: Slow test runs
**Solution**: Use database reuse:
```powershell
pytest --reuse-db --no-migrations
```

## Test Metrics

- **Total Tests**: 47
- **Coverage**: 98%
- **Average Runtime**: ~22 seconds
- **Test Files**: 4
- **Test Classes**: 7
- **Pass Rate**: 100%

## Future Enhancements

- [ ] Add performance/load tests
- [ ] Add tests for CLI interface
- [ ] Add mutation testing
- [ ] Add property-based testing with Hypothesis
- [ ] Add visual regression tests for QR codes
- [ ] Add security/penetration tests
