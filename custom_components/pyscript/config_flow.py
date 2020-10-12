"""Config flow for pyscript."""
import json
from typing import Any, Dict

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT

from .const import CONF_ALLOW_ALL_IMPORTS, DOMAIN

PYSCRIPT_SCHEMA = vol.Schema(
    {vol.Optional(CONF_ALLOW_ALL_IMPORTS, default=False): bool}, extra=vol.ALLOW_EXTRA,
)


class PyscriptConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a pyscript config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input: Dict[str, Any] = None) -> Dict[str, Any]:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            if len(self.hass.config_entries.async_entries(DOMAIN)) > 0:
                return self.async_abort(reason="single_instance_allowed")

            await self.async_set_unique_id(DOMAIN)
            return self.async_create_entry(title=DOMAIN, data=user_input)

        return self.async_show_form(step_id="user", data_schema=PYSCRIPT_SCHEMA)

    async def async_step_import(self, import_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """Import a config entry from configuration.yaml."""
        import_config = json.loads(json.dumps(import_config))

        # Check if import config entry matches any existing config entries
        # so we can update it if necessary
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            entry = entries[0]
            updated_data = entry.data.copy()

            # Update values for all keys, excluding `allow_all_imports` for entries
            # set up through the UI.
            for k, v in import_config.items():
                if entry.source == SOURCE_IMPORT or k != CONF_ALLOW_ALL_IMPORTS:
                    updated_data[k] = v

            # Remove values for all keys in entry.data that are not in the imported config,
            # excluding `allow_all_imports` for entries set up through the UI.
            for key in entry.data:
                if (
                    entry.source == SOURCE_IMPORT or key != CONF_ALLOW_ALL_IMPORTS
                ) and key not in import_config:
                    updated_data.pop(key)

            # Update and reload entry if data needs to be updated
            if updated_data != entry.data:
                self.hass.config_entries.async_update_entry(entry=entry, data=updated_data)
                return self.async_abort(reason="updated_entry")

            return self.async_abort(reason="already_configured_service")

        return await self.async_step_user(user_input=import_config)
