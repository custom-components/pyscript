"""Config flow for Vizio."""
import copy

from homeassistant import config_entries

from . import PYSCRIPT_SCHEMA
from .const import CONF_ALLOW_ALL_IMPORTS, DOMAIN


class PyscriptConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a pyscript config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            if await self.async_set_unique_id(unique_id=DOMAIN, raise_on_progress=True):
                self.async_abort(reason="single_instance_allowed")

            return self.async_create_entry(title=DOMAIN, data=user_input)

        return self.async_show_form(step_id="user", data_schema=PYSCRIPT_SCHEMA)

    async def async_step_import(self, import_config):
        """Import a config entry from configuration.yaml."""
        # Check if import config entry matches any existing config entries
        # so we can update it if necessary
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            entry = entries[0]
            if entry.data.get(CONF_ALLOW_ALL_IMPORTS, False) != import_config.get(
                CONF_ALLOW_ALL_IMPORTS, False
            ):
                updated_data = copy.copy(entry.data)
                updated_data[CONF_ALLOW_ALL_IMPORTS] = import_config.get(CONF_ALLOW_ALL_IMPORTS, False)
                self.hass.config_entries.async_update_entry(entry=entry, data=updated_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="updated_entry")

            return self.async_abort(reason="already_configured_service")

        return await self.async_step_user(user_input=import_config)
