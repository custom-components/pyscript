"""Pyscript Entity Manager"""
from homeassistant.helpers.entity import Entity
from .const import LOGGER_PATH
import logging

_LOGGER = logging.getLogger(LOGGER_PATH + ".entity_manager")


class EntityManager:
    """Entity Manager."""
    hass = None

    platform_adders = {}
    platform_classes = {}
    registered_entities = {}

    @classmethod
    def init(cls, hass):
        """Initialize Class Variables"""
        cls.hass = hass

    @classmethod
    def register_platform(cls, platform, adder, entity_class):
        """Register platform from Home Assistant"""
        _LOGGER.debug(
            "Platform %s Registered",
            platform,
        )
        cls.platform_adders[platform] = adder
        cls.platform_classes[platform] = entity_class
        cls.registered_entities[platform] = {}

    @classmethod
    async def get(cls, ast_ctx, platform, unique_id):
        """Get an Entity from pyscript"""
        await cls.wait_platform_registered(platform)
        if platform not in cls.registered_entities or unique_id not in cls.registered_entities[platform]:
            await cls.create(ast_ctx, platform, unique_id)

        return cls.registered_entities[platform][unique_id]

    @classmethod
    async def create(cls, ast_ctx, platform, unique_id):
        """Create entity from pyscript."""
        await cls.wait_platform_registered(platform)
        new_entity = cls.platform_classes[platform](cls.hass, ast_ctx, unique_id)
        cls.platform_adders[platform]([new_entity])
        cls.registered_entities[platform][unique_id] = new_entity

    @classmethod
    async def wait_platform_registered(cls, platform):
        """Wait for platform registration."""
        if platform not in cls.platform_classes:
            raise KeyError(f"Platform {platform} not registered.")

        return True


class PyscriptEntity(Entity):
    """Base Class for all Pyscript Entities"""
    def __init__(self, hass, ast_ctx, unique_id):
        self._added = False
        self.hass = hass
        self.ast_ctx = ast_ctx

        self._unique_id = f"{self.platform}_{unique_id}"

        self._state = None
        self._attributes = {}

        self._icon = None
        self._name = unique_id

        _LOGGER.debug(
            "Entity Initialized %s",
            self._unique_id,
        )

    # USED BY HOME ASSISTANT
    ####################################

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return attributes for the sensor."""
        attributes = dict(self._attributes)
        attributes.update({
            "_unique_id": self._unique_id,
            "_global_ctx": self.ast_ctx.name,
        })
        return attributes

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
        """Called when Home Assistant adds the entity to the registry"""    
        self._added = True
        await self.async_update()
        _LOGGER.debug(
            "Entity %s Added to Hass as %s",
            self._unique_id,
            self.entity_id,
        )

    # USED INTERNALLY
    #####################################

    async def async_update(self):
        """Request an entity update from Home Assistant"""
        if self._added:
            self.async_write_ha_state()


    # OPTIONALLY OVERRIDDEN IN EXTENDED CLASSES
    #####################################

    def set_state(self, state):
        """Set the State"""
        self._state = state

    def set_attribute(self, attribute, value):
        """Set a single attribute"""
        self._attributes[attribute] = value

    def set_all_attributes(self, attributes):
        """Set all Attributes and clear existing values"""
        self._attributes = attributes


    # TO BE USED IN PYSCRIPT
    ######################################

    async def set(self, state=None, new_attributes=None, **kwargs):
        """Set state and/or attributes from pyscript"""
        if state is not None:
            self.set_state(state)

        if new_attributes is not None:
            self.set_all_attributes(new_attributes)

        for attribute_name in kwargs:
            self.set_attribute(attribute_name, kwargs[attribute_name])

        _LOGGER.debug(
            "%s (%s) state is now %s (%s)",
            self._unique_id,
            self.entity_id,
            self._state,
            self._attributes,
        )

        await self.async_update()

    async def set_name(self, name):
        """set name of entity"""
        self._name = name
        await self.async_update()

    async def set_icon(self, icon):
        """set icon of entity"""
        self._icon = icon
        await self.async_update()
