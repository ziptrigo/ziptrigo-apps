"""What QR codes cost, for the Costs page (`apps.core.costs`): nothing."""

from apps.core.costs import CostSection, free


def cost_section(user: object) -> CostSection:
    return CostSection(
        lines=[
            free(
                'Create, edit and download QR codes',
                'Tracked codes and their scan counts included.',
            ),
            free(
                'Share a QR code by link or email',
                'Anyone who has the link can see and download the code. Revoke it when you like. '
                'Emailing a link is free too, up to 30 emails an hour.',
            ),
        ]
    )
