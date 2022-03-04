from homeassistant.helpers.restore_state import RestoreEntity


class PyscriptEntity(RestoreEntity):
    def set_state(self, state):
        self._attr_state = state

    def set_attributes(self, attributes):
        self._attr_extra_state_attributes = attributes
