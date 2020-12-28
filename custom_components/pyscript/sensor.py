from homeassistant.helpers.entity import Entity
from .const import DOMAIN
from .entity_manager import EntityManager

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    EntityManager.register_platform('sensor', async_add_entities, PyscriptSensor)

async def async_setup_entry(hass, config_entry, async_add_entities):
    return await async_setup_platform(
        hass, config_entry.data, async_add_entities, discovery_info=None
    )


class PyscriptSensor(Entity):

    def __init__(self, hass, name):
        self._added = False
        self.hass = hass

        self._name = name
        self._state = None
        self._attributes = {}

        self._unique_id = f"{DOMAIN}_sensor_{self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return attributes for the sensor."""
        return self._attributes

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return "mdi:home"

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    async def async_added_to_hass(self):
        self._added = True
        await self.async_update()

    async def set_state(self, state):
        self._state = state
        if self._added:
            await self.async_update()

    async def async_update(self):
        self.async_write_ha_state()

