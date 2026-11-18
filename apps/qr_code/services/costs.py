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
                'Share a QR code with a link',
                'Anyone who has the link can see and download the code. Revoke it when you like.',
            ),
        ]
    )
