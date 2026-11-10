"""The Privacy Policy and Terms of Use pages (issue #96)."""

import pytest
from django.urls import reverse

from apps.core import legal

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.mark.parametrize(
    ('name', 'path', 'heading'),
    [
        ('core:privacy', '/privacy/', 'Privacy Policy'),
        ('core:terms', '/terms/', 'Terms of Use'),
    ],
)
def test_legal_pages_render_without_a_login(client, name, path, heading):
    assert reverse(name) == path

    response = client.get(path)

    assert response.status_code == 200
    html = response.content.decode()
    assert f'<h1>{heading}</h1>' in html
    assert 'Nov 9, 2026' in html  # the effective date, in the public wording
    assert legal.OPERATOR_NAME in html
    assert f'mailto:{legal.CONTACT_EMAIL}' in html
    # Nothing left to fill in, and the template comment is not part of the page.
    body = html.split('<h1>', 1)[1].split('</article>', 1)[0]
    assert '[' not in body and '{{' not in body
    assert 'TERMS_VERSION' not in html


def test_privacy_policy_has_the_statements_google_verification_looks_for(client):
    html = client.get(reverse('core:privacy')).content.decode()

    assert 'Google API Services User Data Policy' in html
    assert 'Limited Use' in html
    assert 'sessionid' in html and 'csrftoken' in html and 'ft_anon_id' in html
    assert 'do <strong>not</strong> sell' in html


def test_terms_are_as_is_and_link_to_the_privacy_policy(client):
    html = client.get(reverse('core:terms')).content.decode()

    assert (
        '&quot;AS IS&quot; AND &quot;AS AVAILABLE&quot;' in html
        or '"AS IS" AND "AS AVAILABLE"' in html
    )
    assert 'free of charge' in html
    assert 'an individual' in html
    assert 'State of Texas' in html
    # A same-site link: no new tab, no rel.
    assert '<a href="/privacy/">Privacy Policy</a>' in html


@pytest.mark.parametrize('name', ['core:home', 'accounts:login', 'core:privacy'])
def test_footer_links_to_both_documents(client, name):
    html = client.get(reverse(name)).content.decode()
    footer = html.split('<footer', 1)[1]

    assert f'href="{reverse("core:privacy")}"' in footer
    assert f'href="{reverse("core:terms")}"' in footer


def test_legal_pages_refuse_a_post(client):
    assert client.post(reverse('core:terms')).status_code == 405


def test_terms_version_is_the_effective_date():
    assert legal.TERMS_VERSION == legal.EFFECTIVE_DATE.isoformat()
