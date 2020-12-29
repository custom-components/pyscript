from .const import DOMAIN
from .entity_manager import EntityManager, PyscriptEntity

PLATFORM = 'sensor'

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptSensor)

async def async_setup_entry(hass, config_entry, async_add_entities):
    return await async_setup_platform(
        hass, config_entry.data, async_add_entities, discovery_info=None
    )


class PyscriptSensor(PyscriptEntity):
    platform = PLATFORM

    def init(self):
        self._device_class = None
        self._unit_of_measurement = None

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement for the sensor."""
        return self._unit_of_measurement

    # TO BE USED IN PYSCRIPT
    ######################################

    async def set_device_class(self, device_class):
        self._device_class = device_class
        await self.async_update()

    async def set_unit(self, unit):
        self._unit_of_measurement = unit
        await self.async_update()