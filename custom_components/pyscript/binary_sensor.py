"""Pyscript Binary Sensor Entity"""
from .entity_manager import EntityManager, PyscriptEntity
from homeassistant.components.binary_sensor import BinarySensorEntity

PLATFORM = "binary_sensor"


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Initialize Pyscript Binary Sensor Platform"""
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptBinarySensor)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Initialize Pyscript Binary Sensor Config"""
    return await async_setup_platform(hass, config_entry.data, async_add_entities, discovery_info=None)


class PyscriptBinarySensor(PyscriptEntity, BinarySensorEntity):
    """A Pyscript Binary Sensor Entity"""
    platform = PLATFORM

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._device_class = None


    # USED BY HOME ASSISTANT
    ##############################

    @property
    def is_on(self):
        """Return true if binary sensor is on"""
        if self._state == 'on':
            return True
        return False

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    # OVERRIDDEN FROM PyscriptEntity
    ################################ 

    def set_state(self, state):
        """Handle State Validation"""
        if state is True:
            state = "on"

        if state is False:
            state = "off"

        state = state.lower()

        if state not in ("on", "off"):
            raise ValueError('BinarySensor state must be "on" or "off"')

        super().set_state(state)

    # USED IN PYSCRIPT
    ######################################

    async def set_device_class(self, device_class):
        """Set Device Class of Entity"""
        self._device_class = device_class
        await self.async_update()