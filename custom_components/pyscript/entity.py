"""Entity Classes"""
from homeassistant.helpers.restore_state import RestoreEntity


class PyscriptEntity(RestoreEntity):
    """Generic Pyscript Entity"""

    def set_state(self, state):
        """Set the state"""
        self._attr_state = state

    def set_attributes(self, attributes):
        """Set Attributes"""
        self._attr_extra_state_attributes = attributes
