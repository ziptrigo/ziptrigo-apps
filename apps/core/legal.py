"""The site's legal documents (issue #96): who operates it, how to reach them, and which version
of the Privacy Policy and Terms of Use is in force.

The texts are Markdown templates (`core/templates/core/legal/privacy.md` and `terms.md`) rendered
by `apps.core.views.legal`. **Bump `TERMS_VERSION` (and `EFFECTIVE_DATE`) whenever either document
materially changes**: every signed-in user whose recorded version differs is asked to accept again
on their next visit (`apps.accounts.services.terms`, `apps.accounts.middleware`). A wording or typo
fix needs no bump.
"""

from datetime import date

#: The version users accept, recorded on `accounts.User.terms_version`. A date string.
TERMS_VERSION = '2026-11-09'
#: The date both documents show as "Effective date".
EFFECTIVE_DATE = date(2026, 11, 9)

#: The service is run by an individual, not a company (the documents say so).
OPERATOR_NAME = 'Joao Coelho'
OPERATOR_COUNTRY = 'United States'
#: Governing law and courts of the Terms.
GOVERNING_LAW = 'the State of Texas, United States'
CONTACT_EMAIL = 'info@ziptrigo.com'


def context() -> dict[str, object]:
    """What the two documents' templates are rendered with."""
    return {
        'operator_name': OPERATOR_NAME,
        'operator_country': OPERATOR_COUNTRY,
        'governing_law': GOVERNING_LAW,
        'contact_email': CONTACT_EMAIL,
        'effective_date': EFFECTIVE_DATE,
        'terms_version': TERMS_VERSION,
    }
