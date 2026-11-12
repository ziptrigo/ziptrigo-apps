from datetime import timedelta

from django.apps import AppConfig


class NotesConfig(AppConfig):
    name = 'apps.notes'
    label = 'notes'
    verbose_name = 'Notes'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='Notes',
                description='Markdown notes with code blocks, tags and share links.',
                url_name='notes:list',
                icon='fas fa-note-sticky',
            )
        )
        self._register_jobs()

    def _register_jobs(self):
        from apps.core.scheduler import JobSpec
        from apps.core.scheduler import register as register_job

        from . import jobs

        register_job(
            JobSpec(
                name='notes.purge_trash',
                func=jobs.purge_trash,
                description=(
                    'Permanently deletes notes that have been in the Trash longer '
                    'than the Trash period in the Notes settings.'
                ),
                interval=timedelta(hours=24),
                lease=timedelta(hours=1),
            )
        )
