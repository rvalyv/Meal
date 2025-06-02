"""
ASGI config for simple project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
import mine.routing  # Import your app's routing here

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project_name.settings')

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,  # Handle HTTP requests normally
    "websocket": AuthMiddlewareStack(  # Handle WebSocket connections with auth
        URLRouter(
            mine.routing.websocket_urlpatterns  # Point to your app's WebSocket URLs
        )
    ),
})
