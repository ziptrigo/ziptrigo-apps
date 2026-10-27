from django.apps import AppConfig


class ShortUrlConfig(AppConfig):
    name = 'apps.short_url'
    label = 'short_url'
    verbose_name = 'Short URLs'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='Short URLs',
                description='Shorten a link, track it, and share it as a QR code.',
                url_name='short_url:dashboard',
                icon='fas fa-link',
            )
        )
