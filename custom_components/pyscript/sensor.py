"""Pyscript Sensor Entity"""
from .entity_manager import EntityManager, PyscriptEntity
from homeassistant.helpers.entity import Entity

PLATFORM = "sensor"


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Initialize Pyscript Sensor Platform"""
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptSensor)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Initialize Pyscript Sensor Config"""
    return await async_setup_platform(hass, config_entry.data, async_add_entities, discovery_info=None)


# inheriting from Entity here because HASS doesn't have SensorEntity
class PyscriptSensor(PyscriptEntity, Entity):
    """A Pyscript Sensor Entity"""
    platform = PLATFORM

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._device_class = None
        self._unit_of_measurement = None


    # USED BY HOME ASSISTANT
    ##############################

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state    

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement for the sensor."""
        return self._unit_of_measurement


    # USED IN PYSCRIPT
    ######################################

    async def set_device_class(self, device_class):
        """Set device class of entity"""
        self._device_class = device_class
        await self.async_update()

    async def set_unit(self, unit):
        """Set unit_of_measurement of entity"""
        self._unit_of_measurement = unit
        await self.async_update()