from django.apps import AppConfig


class WorldTimeConfig(AppConfig):
    name = 'apps.world_time'
    label = 'world_time'
    verbose_name = 'World Time'

    def ready(self):
        from apps.core.products import ProductApp, register

        from .services.costs import cost_section

        register(
            ProductApp(
                label=self.label,
                name='World Time',
                description='A world clock: compare time zones and find a time that works.',
                url_name='world_time:home',
                icon='fas fa-earth-americas',
                costs=cost_section,
            )
        )
