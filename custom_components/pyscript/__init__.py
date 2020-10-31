"""Component to allow running Python scripts."""

import glob
import json
import logging
import os
import sys

import pkg_resources
import voluptuous as vol

from homeassistant.config import async_hass_config_yaml
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
    SERVICE_RELOAD,
)
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreStateData
from homeassistant.loader import bind_hass
from homeassistant.requirements import async_process_requirements

from .const import (
    CONF_ALLOW_ALL_IMPORTS,
    CONF_HASS_IS_GLOBAL,
    CONFIG_ENTRY,
    DOMAIN,
    FOLDER,
    LOGGER_PATH,
    REQUIREMENTS_FILE,
    REQUIREMENTS_PATHS,
    SERVICE_JUPYTER_KERNEL_START,
    UNSUB_LISTENERS,
)
from .eval import AstEval
from .event import Event
from .function import Function
from .global_ctx import GlobalContext, GlobalContextMgr
from .jupyter_kernel import Kernel
from .state import State
from .trigger import TrigTime

if sys.version_info[:2] >= (3, 8):
    from importlib.metadata import (  # pylint: disable=no-name-in-module,import-error
        PackageNotFoundError,
        version as installed_version,
    )
else:
    from importlib_metadata import (  # pylint: disable=import-error
        PackageNotFoundError,
        version as installed_version,
    )

_LOGGER = logging.getLogger(LOGGER_PATH)

PYSCRIPT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ALLOW_ALL_IMPORTS, default=False): cv.boolean,
        vol.Optional(CONF_HASS_IS_GLOBAL, default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: PYSCRIPT_SCHEMA}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    """Component setup, run import config flow for each entry in config."""
    await restore_state(hass)
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )

    return True


async def restore_state(hass):
    """Restores the persisted pyscript state."""
    restore_data = await RestoreStateData.async_get_instance(hass)
    for entity_id, value in restore_data.last_states.items():
        if entity_id.startswith("pyscript."):
            last_state = value.state
            hass.states.async_set(entity_id, last_state.state, last_state.attributes)


async def update_yaml_config(hass, config_entry):
    """Update the yaml config."""
    try:
        conf = await async_hass_config_yaml(hass)
    except HomeAssistantError as err:
        _LOGGER.error(err)
        return

    config = PYSCRIPT_SCHEMA(conf.get(DOMAIN, {}))

    #
    # If data in config doesn't match config entry, trigger a config import
    # so that the config entry can get updated
    #
    if config != config_entry.data:
        await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data=config)


def start_global_contexts(global_ctx_only=None):
    """Start all the file and apps global contexts."""
    start_list = []
    for global_ctx_name, global_ctx in GlobalContextMgr.items():
        idx = global_ctx_name.find(".")
        if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps"}:
            continue
        if global_ctx_only is not None:
            if global_ctx_name != global_ctx_only and not global_ctx_name.startswith(global_ctx_only + "."):
                continue
        global_ctx.set_auto_start(True)
        start_list.append(global_ctx)
    for global_ctx in start_list:
        global_ctx.start()


async def async_setup_entry(hass, config_entry):
    """Initialize the pyscript config entry."""
    if Function.hass:
        #
        # reload yaml if this isn't the first time (ie, on reload)
        #
        await update_yaml_config(hass, config_entry)

    Function.init(hass)
    Event.init(hass)
    TrigTime.init(hass)
    State.init(hass)
    State.register_functions()
    GlobalContextMgr.init()

    pyscript_folder = hass.config.path(FOLDER)
    if not await hass.async_add_executor_job(os.path.isdir, pyscript_folder):
        _LOGGER.debug("Folder %s not found in configuration folder, creating it", FOLDER)
        await hass.async_add_executor_job(os.makedirs, pyscript_folder)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONFIG_ENTRY] = config_entry
    hass.data[DOMAIN][UNSUB_LISTENERS] = []

    State.set_pyscript_config(config_entry.data)

    await install_requirements(hass)
    await load_scripts(hass, config_entry.data)

    async def reload_scripts_handler(call):
        """Handle reload service calls."""
        _LOGGER.debug("reload: yaml, reloading scripts, and restarting")

        await update_yaml_config(hass, config_entry)
        State.set_pyscript_config(config_entry.data)

        await State.get_service_params()

        global_ctx_only = call.data.get("global_ctx", None)

        if global_ctx_only is not None and not GlobalContextMgr.get(global_ctx_only):
            _LOGGER.error("pyscript.reload: no global context '%s' to reload", global_ctx_only)
            return

        await unload_scripts(global_ctx_only=global_ctx_only)

        await install_requirements(hass)
        await load_scripts(hass, config_entry.data, global_ctx_only=global_ctx_only)

        start_global_contexts(global_ctx_only=global_ctx_only)

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, reload_scripts_handler)

    async def jupyter_kernel_start(call):
        """Handle Jupyter kernel start call."""
        _LOGGER.debug("service call to jupyter_kernel_start: %s", call.data)

        global_ctx_name = GlobalContextMgr.new_name("jupyter_")
        global_ctx = GlobalContext(
            global_ctx_name, global_sym_table={"__name__": global_ctx_name}, manager=GlobalContextMgr
        )
        global_ctx.set_auto_start(True)

        GlobalContextMgr.set(global_ctx_name, global_ctx)

        ast_ctx = AstEval(global_ctx_name, global_ctx)
        Function.install_ast_funcs(ast_ctx)
        kernel = Kernel(call.data, ast_ctx, global_ctx_name)
        await kernel.session_start()
        hass.states.async_set(call.data["state_var"], json.dumps(kernel.get_ports()))

        def state_var_remove():
            hass.states.async_remove(call.data["state_var"])

        kernel.set_session_cleanup_callback(state_var_remove)

    hass.services.async_register(DOMAIN, SERVICE_JUPYTER_KERNEL_START, jupyter_kernel_start)

    async def state_changed(event):
        var_name = event.data["entity_id"]
        # attr = event.data["new_state"].attributes
        if "new_state" not in event.data or event.data["new_state"] is None:
            # state variable has been deleted
            new_val = None
        else:
            new_val = event.data["new_state"].state
        old_val = event.data["old_state"].state if event.data["old_state"] else None
        new_vars = {var_name: new_val, f"{var_name}.old": old_val}
        func_args = {
            "trigger_type": "state",
            "var_name": var_name,
            "value": new_val,
            "old_value": old_val,
            "context": event.context,
        }
        await State.update(new_vars, func_args)

    async def hass_started(event):
        _LOGGER.debug("adding state changed listener and starting global contexts")
        await State.get_service_params()
        hass.data[DOMAIN][UNSUB_LISTENERS].append(hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed))
        start_global_contexts()

    async def hass_stop(event):
        _LOGGER.debug("stopping global contexts")
        await unload_scripts(unload_all=True)
        # tell reaper task to exit (after other tasks are cancelled)
        await Function.reaper_stop()

    # Store callbacks to event listeners so we can unsubscribe on unload
    hass.data[DOMAIN][UNSUB_LISTENERS].append(
        hass.bus.async_listen(EVENT_HOMEASSISTANT_STARTED, hass_started)
    )
    hass.data[DOMAIN][UNSUB_LISTENERS].append(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, hass_stop))

    return True


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    # Unload scripts
    await unload_scripts()

    # Unsubscribe from listeners
    for unsub_listener in hass.data[DOMAIN][UNSUB_LISTENERS]:
        unsub_listener()

    hass.data.pop(DOMAIN)
    return True


async def unload_scripts(global_ctx_only=None, unload_all=False):
    """Unload all scripts from GlobalContextMgr with given name prefixes."""
    ctx_delete = {}
    for global_ctx_name, global_ctx in GlobalContextMgr.items():
        if not unload_all:
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps", "modules"}:
                continue
        if global_ctx_only is not None:
            if global_ctx_name != global_ctx_only and not global_ctx_name.startswith(global_ctx_only + "."):
                continue
        global_ctx.stop()
        ctx_delete[global_ctx_name] = global_ctx
    for global_ctx_name, global_ctx in ctx_delete.items():
        await GlobalContextMgr.delete(global_ctx_name)


@bind_hass
def load_all_requirement_lines(hass, requirements_paths, requirements_file):
    """Load all lines from requirements_file located in requirements_paths."""
    all_requirements = {}
    for root in requirements_paths:
        for requirements_path in glob.glob(os.path.join(hass.config.path(FOLDER), root, requirements_file)):
            with open(requirements_path, "r") as requirements_fp:
                all_requirements[requirements_path] = requirements_fp.readlines()

    return all_requirements


@bind_hass
async def install_requirements(hass):
    """Install missing requirements from requirements.txt."""
    all_requirements = await hass.async_add_executor_job(
        load_all_requirement_lines, hass, REQUIREMENTS_PATHS, REQUIREMENTS_FILE
    )
    requirements_to_install = []
    for requirements_path, pkg_lines in all_requirements.items():
        for pkg in pkg_lines:
            # Remove inline comments which are accepted by pip but not by Home
            # Assistant's installation method.
            # https://rosettacode.org/wiki/Strip_comments_from_a_string#Python
            i = pkg.find("#")
            if i >= 0:
                pkg = pkg[:i]
            pkg = pkg.strip()

            if not pkg:
                continue

            try:
                # Attempt to get version of package. Do nothing if it's found since
                # we want to use the version that's already installed to be safe
                requirement = pkg_resources.Requirement.parse(pkg)
                requirement_installed_version = installed_version(requirement.project_name)

                if requirement_installed_version in requirement:
                    _LOGGER.debug("`%s` already found", requirement.project_name)
                else:
                    _LOGGER.warning(
                        (
                            "`%s` already found but found version `%s` does not"
                            " match requirement. Keeping found version."
                        ),
                        requirement.project_name,
                        requirement_installed_version,
                    )
            except PackageNotFoundError:
                # Since package wasn't found, add it to installation list
                _LOGGER.debug("%s not found, adding it to package installation list", pkg)
                requirements_to_install.append(pkg)
            except ValueError:
                # Not valid requirements line so it can be skipped
                _LOGGER.debug("Ignoring `%s` because it is not a valid package", pkg)
        if requirements_to_install:
            _LOGGER.info(
                "Installing the following packages from %s: %s",
                requirements_path,
                ", ".join(requirements_to_install),
            )
            await async_process_requirements(hass, DOMAIN, requirements_to_install)
        else:
            _LOGGER.debug("All packages in %s are already available", requirements_path)


@bind_hass
async def load_scripts(hass, data, global_ctx_only=None):
    """Load all python scripts in FOLDER."""

    pyscript_dir = hass.config.path(FOLDER)

    def glob_files(load_paths, data):
        source_files = []
        apps_config = data.get("apps", None)
        for path, match, check_config in load_paths:
            for this_path in sorted(glob.glob(os.path.join(pyscript_dir, path, match))):
                rel_import_path = None
                elts = this_path.split("/")
                if match.find("/") < 0:
                    # last entry without the .py
                    mod_name = elts[-1][0:-3]
                else:
                    # 2nd last entry
                    mod_name = elts[-2]
                    rel_import_path = f"{path}/mod_name"
                if path == "":
                    global_ctx_name = f"file.{mod_name}"
                    fq_mod_name = mod_name
                else:
                    global_ctx_name = f"{path}.{mod_name}"
                    fq_mod_name = global_ctx_name
                if check_config:
                    if not isinstance(apps_config, dict) or mod_name not in apps_config:
                        _LOGGER.debug("load_scripts: skipping %s because config not present", this_path)
                        continue
                source_files.append([global_ctx_name, this_path, rel_import_path, fq_mod_name])
        return source_files

    load_paths = [
        ["apps", "*.py", True],
        ["apps", "*/__init__.py", True],
        ["", "*.py", False],
    ]

    source_files = await hass.async_add_executor_job(glob_files, load_paths, data)
    for global_ctx_name, source_file, rel_import_path, fq_mod_name in source_files:
        if global_ctx_only is not None:
            if global_ctx_name != global_ctx_only and not global_ctx_name.startswith(global_ctx_only + "."):
                continue
        global_ctx = GlobalContext(
            global_ctx_name,
            global_sym_table={"__name__": fq_mod_name},
            manager=GlobalContextMgr,
            rel_import_path=rel_import_path,
        )
        await GlobalContextMgr.load_file(source_file, global_ctx)
