"""Component to allow running Python scripts."""

import glob
import json
import logging
import os

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
from homeassistant.loader import bind_hass

from .const import CONF_ALLOW_ALL_IMPORTS, DOMAIN, FOLDER, LOGGER_PATH, SERVICE_JUPYTER_KERNEL_START
from .eval import AstEval
from .event import Event
from .function import Function
from .global_ctx import GlobalContext, GlobalContextMgr
from .jupyter_kernel import Kernel
from .state import State
from .trigger import TrigTime

_LOGGER = logging.getLogger(LOGGER_PATH)

PYSCRIPT_SCHEMA = vol.Schema(
    {vol.Optional(CONF_ALLOW_ALL_IMPORTS, default=False): cv.boolean}, extra=vol.ALLOW_EXTRA,
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: PYSCRIPT_SCHEMA}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    """Component setup, run import config flow for each entry in config."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )

    return True


async def async_setup_entry(hass, config_entry):
    """Initialize the pyscript config entry."""
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
    hass.data[DOMAIN][CONF_ALLOW_ALL_IMPORTS] = config_entry.data.get(CONF_ALLOW_ALL_IMPORTS)

    State.set_pyscript_config(config_entry.data)

    await load_scripts(hass, config_entry.data)

    async def reload_scripts_handler(call):
        """Handle reload service calls."""
        _LOGGER.debug("reload: yaml, reloading scripts, and restarting")

        try:
            conf = await async_hass_config_yaml(hass)
        except HomeAssistantError as err:
            _LOGGER.error(err)
            return

        config = PYSCRIPT_SCHEMA(conf.get(DOMAIN, {}))

        # If data in config doesn't match config entry, trigger a config import
        # so that the config entry can get updated
        if config != config_entry.data:
            await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data=config)

        State.set_pyscript_config(config_entry.data)

        ctx_delete = {}
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps", "modules"}:
                continue
            global_ctx.stop()
            ctx_delete[global_ctx_name] = global_ctx
        for global_ctx_name, global_ctx in ctx_delete.items():
            await GlobalContextMgr.delete(global_ctx_name)

        await load_scripts(hass, config_entry.data)

        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps"}:
                continue
            global_ctx.set_auto_start(True)

        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps"}:
                continue
            global_ctx.start()

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, reload_scripts_handler)

    async def jupyter_kernel_start(call):
        """Handle Jupyter kernel start call."""
        _LOGGER.debug("service call to jupyter_kernel_start: %s", call.data)

        global_ctx_name = GlobalContextMgr.new_name("jupyter_")
        global_ctx = GlobalContext(global_ctx_name, global_sym_table={}, manager=GlobalContextMgr)
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
        }
        await State.update(new_vars, func_args)

    async def start_triggers(event):
        _LOGGER.debug("adding state changed listener and starting triggers")
        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps"}:
                continue
            global_ctx.set_auto_start(True)
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps"}:
                continue
            global_ctx.start()

    async def stop_triggers(event):
        _LOGGER.debug("stopping triggers")
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            if not global_ctx_name.startswith("file."):
                continue
            global_ctx.stop()

    hass.bus.async_listen(EVENT_HOMEASSISTANT_STARTED, start_triggers)
    hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, stop_triggers)

    return True


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    hass.data.pop(DOMAIN)
    return True


@bind_hass
async def load_scripts(hass, data):
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
        global_ctx = GlobalContext(
            global_ctx_name,
            global_sym_table={"__name__": fq_mod_name},
            manager=GlobalContextMgr,
            rel_import_path=rel_import_path,
        )
        await GlobalContextMgr.load_file(source_file, global_ctx)
