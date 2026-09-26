import uuid

from playwright.sync_api import Page, expect


# Disable django-pytest for these tests as they don't use the django test runner
# but interact with a separate server process.
def test_create_account(page: Page, site_server: str):
    # Use a unique email to avoid conflicts
    email = f'testuser_{uuid.uuid4().hex[:8]}@example.com'

    # Go to the registration page
    page.goto(f'{site_server}/account/register/')

    # Fill in the registration form
    page.fill('input[name="name"]', 'Test User')
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', 'Password123!')

    # Click the create account button
    page.click('button[type="submit"]')

    # Check if we are redirected to the account created page
    expect(page).to_have_url(f'{site_server}/account/created/')
    expect(page.locator('h1')).not_to_contain_text('Create account')
    expect(page.locator('h1')).to_contain_text('Account created')
