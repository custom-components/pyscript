from homeassistant.helpers.entity import Entity
from .const import DOMAIN, LOGGER_PATH
import logging

_LOGGER = logging.getLogger(LOGGER_PATH + ".entity_manager")


class EntityManager:

    hass = None

    platform_adders = {}
    platform_classes = {}
    registered_entities = {}


    @classmethod
    def init(cls, hass):
        cls.hass = hass

    @classmethod
    def register_platform(cls, platform, adder, entity_class):
        cls.platform_adders[platform] = adder
        cls.platform_classes[platform] = entity_class
        cls.registered_entities[platform] = {}

    @classmethod
    def get(cls, platform, name):
        if platform not in cls.registered_entities or name not in cls.registered_entities[platform]:
            cls.create(platform, name)

        return cls.registered_entities[platform][name]

    @classmethod
    def create(cls, platform, name):
        new_entity = cls.platform_classes[platform](cls.hass, name)
        cls.platform_adders[platform]([new_entity])
        cls.registered_entities[platform][name] = new_entity
        

class PyscriptEntity(Entity):

    def __init__(self, hass, unique_id):
        self._added = False
        self.hass = hass

        self._unique_id = f"{DOMAIN}_{self.platform}_{unique_id}"

        self._state = None
        self._attributes = {}
        self._icon = None


        _LOGGER.debug(
            "Entity Initialized %s",
            self._unique_id,
        )

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
        return self._icon

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    async def async_added_to_hass(self):
        self._added = True
        await self.async_update()

    async def async_update(self):
        self.async_write_ha_state()

    def set_state(self, state):
        self._state = state

    def set_attribute(self, attribute, value):
        self._attributes[attribute] = value

    def set_all_attributes(self, attributes={}):
        self._attributes = attributes


    # TO BE USED IN PYSCRIPT
    ######################################

    async def set(self, state=None, new_attributes=None, **kwargs):
        if state is not None:
            self.set_state(state)

        if new_attributes is not None:
            self.set_all_attributes(new_attributes)

        for attribute_name in kwargs:
            self.set_attribute(attribute_name, kwargs[attribute_name])


        _LOGGER.debug(
            "%s state is now %s (%s)",
            self._unique_id,
            self._state,
            self._attributes,
        )

        if self._added:
            await self.async_update()
