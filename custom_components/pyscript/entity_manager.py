from homeassistant.helpers.entity import Entity
from .const import DOMAIN, LOGGER_PATH
import logging
import asyncio

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
        _LOGGER.debug(
            "Platform %s Registered",
            platform,
        )
        cls.platform_adders[platform] = adder
        cls.platform_classes[platform] = entity_class
        cls.registered_entities[platform] = {}

    @classmethod
    async def get(cls, ast_ctx, platform, name):
        await cls.wait_platform_registered(platform)
        if platform not in cls.registered_entities or name not in cls.registered_entities[platform]:
            await cls.create(ast_ctx, platform, name)

        return cls.registered_entities[platform][name]

    @classmethod
    async def create(cls, ast_ctx, platform, name):
        await cls.wait_platform_registered(platform)
        new_entity = cls.platform_classes[platform](cls.hass, ast_ctx, name)
        cls.platform_adders[platform]([new_entity])
        cls.registered_entities[platform][name] = new_entity

    @classmethod
    async def wait_platform_registered(cls, platform):        
        if platform not in cls.platform_classes:
            raise KeyError(f"Platform {platform} not registered.")

        return True
        

class PyscriptEntity(Entity):

    def __init__(self, hass, ast_ctx, unique_id):
        self._added = False
        self.hass = hass
        self.ast_ctx = ast_ctx

        self._unique_id = unique_id

        self._state = None
        self._attributes = {}

        self._icon = None
        self._name = None


        _LOGGER.debug(
            "Entity Initialized %s",
            self._unique_id,
        )

        self.init()

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
    def name(self):
        """Return the name to use in the frontend, if any."""
        return self._name


    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    async def async_added_to_hass(self):
        self._added = True
        await self.async_update()

    async def async_update(self):
        if self._added:
            self.async_write_ha_state()

    # OPTIONALLY OVERRIDDEN IN EXTENDED CLASSES
    #####################################

    def set_state(self, state):
        self._state = state

    def set_attribute(self, attribute, value):
        self._attributes[attribute] = value

    def set_all_attributes(self, attributes={}):
        self._attributes = attributes

    def init(self):
        pass


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

        await self.async_update()

    async def set_name(self, name):
        self._name = name
        await self.async_update()

    async def set_icon(self, icon):
        self._icon = icon
        await self.async_update()