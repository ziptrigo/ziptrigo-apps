"""What World Time costs, for the Costs page (`apps.core.costs`): nothing."""

from apps.core.costs import CostSection, free


def cost_section(user: object) -> CostSection:
    return CostSection(
        lines=[free('Tabs, locations and the time grid', 'World Time is free.')],
    )
