class AuraApiClient:

    async def get_frame(self) -> dict:
        ...

    async def update_frame(self, payload: dict):
        ...

    async def next_image(self):
        ...

    async def hide_asset(self, asset_id: str):
        ...

    async def enable_schedule(self):
        ...

    async def disable_schedule(self):
        ...

    async def set_schedule(
        self,
        on_time: datetime.time,
        off_time: datetime.time,
        timezone: str,
    ):
        ...
