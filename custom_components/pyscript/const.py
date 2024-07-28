"""Define pyscript-wide constants."""

#
# 2023.7 supports service response; handle older versions by defaulting enum
# Should eventually deprecate this and just use SupportsResponse import
#
try:
    from homeassistant.core import SupportsResponse

    SERVICE_RESPONSE_NONE = SupportsResponse.NONE
    SERVICE_RESPONSE_OPTIONAL = SupportsResponse.OPTIONAL
    SERVICE_RESPONSE_ONLY = SupportsResponse.ONLY
except ImportError:
    SERVICE_RESPONSE_NONE = None
    SERVICE_RESPONSE_OPTIONAL = None
    SERVICE_RESPONSE_ONLY = None

DOMAIN = "pyscript"

CONFIG_ENTRY = "config_entry"
CONFIG_ENTRY_OLD = "config_entry_old"
UNSUB_LISTENERS = "unsub_listeners"

FOLDER = "pyscript"

UNPINNED_VERSION = "_unpinned_version"

ATTR_INSTALLED_VERSION = "installed_version"
ATTR_SOURCES = "sources"
ATTR_VERSION = "version"

CONF_ALLOW_ALL_IMPORTS = "allow_all_imports"
CONF_HASS_IS_GLOBAL = "hass_is_global"
CONF_INSTALLED_PACKAGES = "_installed_packages"

SERVICE_JUPYTER_KERNEL_START = "jupyter_kernel_start"

LOGGER_PATH = "custom_components.pyscript"

REQUIREMENTS_FILE = "requirements.txt"
REQUIREMENTS_PATHS = ("", "apps/*", "modules/*", "scripts/**")

WATCHDOG_TASK = "watch_dog_task"

ALLOWED_IMPORTS = {
    "black",
    "cmath",
    "datetime",
    "decimal",
    "fractions",
    "functools",
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
