from django.apps import AppConfig


class MineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mine'

    def ready(self):
        import mine.signals