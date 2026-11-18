from datetime import timedelta

from django.apps import AppConfig


class QrCodeConfig(AppConfig):
    name = 'apps.qr_code'
    label = 'qr_code'
    verbose_name = 'QR codes'

    def ready(self):
        from apps.core.products import ProductApp, register

        from .services.costs import cost_section

        register(
            ProductApp(
                label=self.label,
                name='QR Codes',
                description='Generate QR codes for URLs and text, with trackable short links.',
                url_name='qr_code:dashboard',
                icon='fas fa-qrcode',
                costs=cost_section,
            )
        )
        self._register_jobs()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='qr_code.purge_report_ips',
                func=jobs.purge_report_ips,
                description=(
                    'Removes the IP address from reports of shared QR codes older than 90 days.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
