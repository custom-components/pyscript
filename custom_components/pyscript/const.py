"""Define pyscript-wide constants."""

DOMAIN = "pyscript"

CONFIG_ENTRY = "config_entry"
UNSUB_LISTENERS = "unsub_listeners"

FOLDER = "pyscript"

CONF_ALLOW_ALL_IMPORTS = "allow_all_imports"
CONF_HASS_IS_GLOBAL = "hass_is_global"

SERVICE_JUPYTER_KERNEL_START = "jupyter_kernel_start"

LOGGER_PATH = "custom_components.pyscript"

ALLOWED_IMPORTS = {
    "black",
    "cmath",
    "datetime",
    "decimal",
    "fractions",
    "homeassistant.const",
    "isort",
    "json",
    "math",
    "number",
    "random",
    "re",
    "statistics",
    "string",
    "time",
    "voluptuous",
}
