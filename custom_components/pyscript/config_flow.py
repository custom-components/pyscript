"""Config flow for pyscript."""
import json
from typing import Any, Dict

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import callback

from .const import CONF_ALLOW_ALL_IMPORTS, CONF_HASS_IS_GLOBAL, CONF_INSTALLED_PACKAGES, DOMAIN

CONF_BOOL_ALL = {CONF_ALLOW_ALL_IMPORTS, CONF_HASS_IS_GLOBAL}

PYSCRIPT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ALLOW_ALL_IMPORTS, default=False): bool,
        vol.Optional(CONF_HASS_IS_GLOBAL, default=False): bool,
    },
    extra=vol.ALLOW_EXTRA,
)


class PyscriptOptionsConfigFlow(config_entries.OptionsFlow):
    """Handle a pyscript options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize pyscript options flow."""
        self.config_entry = config_entry
        self._show_form = False

    async def async_step_init(self, user_input: Dict[str, Any] = None) -> Dict[str, Any]:
        """Manage the pyscript options."""
        if self.config_entry.source == SOURCE_IMPORT:
            self._show_form = True
            return await self.async_step_no_ui_configuration_allowed()

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(
                    {
                        vol.Optional(name, default=self.config_entry.data.get(name, False)): bool
                        for name in CONF_BOOL_ALL
                    },
                    extra=vol.ALLOW_EXTRA,
                ),
            )

        if any(
            name not in self.config_entry.data or user_input[name] != self.config_entry.data[name]
            for name in CONF_BOOL_ALL
        ):
            updated_data = self.config_entry.data.copy()
            updated_data.update(user_input)
            self.hass.config_entries.async_update_entry(entry=self.config_entry, data=updated_data)
            return self.async_create_entry(title="", data={})

        self._show_form = True
        return await self.async_step_no_update()

    async def async_step_no_ui_configuration_allowed(
        self, user_input: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Tell user no UI configuration is allowed."""
        if self._show_form:
            self._show_form = False
            return self.async_show_form(step_id="no_ui_configuration_allowed", data_schema=vol.Schema({}))

        return self.async_create_entry(title="", data={})

    async def async_step_no_update(self, user_input: Dict[str, Any] = None) -> Dict[str, Any]:
        """Tell user no update to process."""
        if self._show_form:
            self._show_form = False
            return self.async_show_form(step_id="no_update", data_schema=vol.Schema({}))

        return self.async_create_entry(title="", data={})


class PyscriptConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a pyscript config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PyscriptOptionsConfigFlow:
        """Get the options flow for this handler."""
        return PyscriptOptionsConfigFlow(config_entry)

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
        # Convert OrderedDict to dict
        import_config = json.loads(json.dumps(import_config))

        # Check if import config entry matches any existing config entries
        # so we can update it if necessary
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            entry = entries[0]
            updated_data = entry.data.copy()

            # Update values for all keys, excluding `allow_all_imports` for entries
            # set up through the UI.
            for key, val in import_config.items():
                if entry.source == SOURCE_IMPORT or key not in CONF_BOOL_ALL:
                    updated_data[key] = val

            # Remove values for all keys in entry.data that are not in the imported config,
            # excluding `allow_all_imports` for entries set up through the UI.
            for key in entry.data:
                if (
                    (entry.source == SOURCE_IMPORT or key not in CONF_BOOL_ALL)
                    and key != CONF_INSTALLED_PACKAGES
                    and key not in import_config
                ):
                    updated_data.pop(key)

            # Update and reload entry if data needs to be updated
            if updated_data != entry.data:
                self.hass.config_entries.async_update_entry(entry=entry, data=updated_data)
                return self.async_abort(reason="updated_entry")

            return self.async_abort(reason="already_configured")

        return await self.async_step_user(user_input=import_config)
