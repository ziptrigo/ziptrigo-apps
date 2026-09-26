from django.apps import AppConfig


class QrCodeConfig(AppConfig):
    name = 'apps.qr_code'
    label = 'qr_code'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='QR Codes',
                description='Generate QR codes for URLs and text, with trackable short links.',
                url_name='qr_code:dashboard',
                icon='fas fa-qrcode',
            )
        )
