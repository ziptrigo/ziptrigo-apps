from django.apps import AppConfig


class FileTransferConfig(AppConfig):
    name = 'apps.file_transfer'
    label = 'file_transfer'

    def ready(self):
        from apps.core.products import ProductApp, register

        register(
            ProductApp(
                label=self.label,
                name='File Transfer',
                description='Send large files to anyone with a link that expires.',
                url_name='file_transfer:index',
                icon='fas fa-paper-plane',
            )
        )
