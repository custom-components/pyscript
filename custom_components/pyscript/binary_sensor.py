from .const import DOMAIN
from .entity_manager import EntityManager, PyscriptEntity

PLATFORM = 'binary_sensor'

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptBinarySensor)

async def async_setup_entry(hass, config_entry, async_add_entities):
    return await async_setup_platform(
        hass, config_entry.data, async_add_entities, discovery_info=None
    )


class PyscriptBinarySensor(PyscriptEntity):
    platform = PLATFORM

    def init(self):
        self._device_class = None

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    # TO BE USED IN PYSCRIPT
    ######################################        

    def set_state(self, state):
        if state is True:
            state = "on"

        if state is False:
            state = "off"

        state = state.lower()

        if state not in ('on', 'off'):
            raise ValueError('BinarySensor state must be "on" or "off"')

        self._state = state

    async def set_device_class(self, device_class):
        self._device_class = device_class
        await self.async_update()