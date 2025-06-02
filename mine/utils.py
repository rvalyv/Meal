from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

def send_stock_update(ingredient):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "ingredients_stock",
        {
            "type": "ingredient_stock_update",
            "content": {
                "ingredient_id": ingredient.id,
                "stock_quantity": ingredient.stock_quantity,
                "name": ingredient.name,
            }
        }
    )
