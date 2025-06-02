from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/ingredients/stock/$', consumers.IngredientStockConsumer.as_asgi()),
]
