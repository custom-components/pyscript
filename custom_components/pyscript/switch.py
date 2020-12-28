from .const import DOMAIN
from .entity_manager import EntityManager, PyscriptEntity

PLATFORM = 'switch'

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptSwitch)

async def async_setup_entry(hass, config_entry, async_add_entities):
    return await async_setup_platform(
        hass, config_entry.data, async_add_entities, discovery_info=None
    )


class PyscriptSwitch(PyscriptEntity):
    platform = PLATFORM

    def init(self):
        self._turn_on_handler = None
        self._turn_off_handler = None

    def on_turn_on(self, func):
        self._turn_on_handler = func

    def on_turn_off(self, func):
        self._turn_off_handler = func

    async def async_turn_on(self, **kwargs):
        if self._turn_on_handler is None:
            return

        await self._turn_on_handler.call(self.ast_ctx, self, **kwargs)

    async def async_turn_off(self, **kwargs):
        if self._turn_off_handler is None:
            return

        await self._turn_off_handler.call(self.ast_ctx, self, **kwargs)