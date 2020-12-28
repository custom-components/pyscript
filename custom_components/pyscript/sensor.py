from .const import DOMAIN
from .entity_manager import EntityManager, PyscriptEntity

PLATFORM = 'sensor'

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    EntityManager.register_platform('sensor', async_add_entities, PyscriptSensor)

async def async_setup_entry(hass, config_entry, async_add_entities):
    return await async_setup_platform(
        hass, config_entry.data, async_add_entities, discovery_info=None
    )


class PyscriptSensor(PyscriptEntity):
    platform = PLATFORM

