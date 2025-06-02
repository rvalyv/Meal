from channels.generic.websocket import AsyncJsonWebsocketConsumer

class IngredientStockConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.accept()
        # Можно подписать на группу для рассылок всем клиентам
        await self.channel_layer.group_add("ingredients_stock", self.channel_name)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("ingredients_stock", self.channel_name)

    async def ingredient_stock_update(self, event):
        # Отправляем клиенту обновление
        await self.send_json(event["content"])
