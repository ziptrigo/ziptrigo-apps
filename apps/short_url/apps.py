from django.apps import AppConfig


class ShortUrlConfig(AppConfig):
    name = 'apps.short_url'
    label = 'short_url'
    verbose_name = 'Short URLs'

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import pre_delete
        from django.dispatch import receiver

        from apps.core.products import ProductApp, register

        # `weak=False`, as for receipts' receiver: a local closure would otherwise be garbage
        # collected as soon as `ready()` returns.
        @receiver(
            pre_delete,
            sender=get_user_model(),
            dispatch_uid='short_url.retire_codes_on_user_delete',
            weak=False,
        )
        def _retire_codes_on_user_delete(sender, instance, **kwargs):
            from .services.codes import retire_codes_of

            retire_codes_of(instance)

        register(
            ProductApp(
                label=self.label,
                name='Short URLs',
                description='Shorten a link, track it, and share it as a QR code.',
                url_name='short_url:dashboard',
                icon='fas fa-link',
            )
        )
