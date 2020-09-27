"""Component to allow running Python scripts."""

import glob
import json
import logging
import os

import voluptuous as vol

from homeassistant.config import async_hass_config_yaml, async_process_component_config
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
    SERVICE_RELOAD,
)
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.loader import async_get_integration, bind_hass

from .const import DOMAIN, FOLDER, LOGGER_PATH, SERVICE_JUPYTER_KERNEL_START
from .eval import AstEval
from .event import Event
from .function import Function
from .global_ctx import GlobalContext, GlobalContextMgr
from .jupyter_kernel import Kernel
from .state import State
from .trigger import TrigTime

_LOGGER = logging.getLogger(LOGGER_PATH)

CONF_ALLOW_ALL_IMPORTS = "allow_all_imports"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {vol.Optional(CONF_ALLOW_ALL_IMPORTS, default=False): cv.boolean}, extra=vol.ALLOW_EXTRA,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Initialize the pyscript component."""
    Function.init(hass)
    Event.init(hass)
    TrigTime.init(hass)
    State.init(hass)
    State.register_functions()
    GlobalContextMgr.init()

    pyscript_folder = hass.config.path(FOLDER)

    def check_isdir(path):
        return os.path.isdir(path)

    if not await hass.async_add_executor_job(check_isdir, pyscript_folder):
        _LOGGER.error("Folder %s not found in configuration folder", FOLDER)
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["allow_all_imports"] = config[DOMAIN].get(CONF_ALLOW_ALL_IMPORTS)

    State.set_pyscript_config(config.get(DOMAIN, {}))

    await load_scripts(hass)

    async def reload_scripts_handler(call):
        """Handle reload service calls."""
        _LOGGER.debug("reload: yaml, reloading scripts, and restarting")

        try:
            conf = await async_hass_config_yaml(hass)
        except HomeAssistantError as err:
            _LOGGER.error(err)
            return

        integration = await async_get_integration(hass, DOMAIN)

        config = await async_process_component_config(hass, conf, integration)

        # GlobalContext.global_sym_table_add("pyscript.config", config.get(DOMAIN, {}))
        State.set_pyscript_config(config.get(DOMAIN, {}))

        ctx_delete = {}
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            idx = global_ctx_name.find(".")
            if idx < 0 or global_ctx_name[0:idx] not in {"file", "apps", "modules"}:
                continue
            global_ctx.stop()
            global_ctx.set_auto_start(False)
            ctx_delete[global_ctx_name] = global_ctx
        for global_ctx_name, global_ctx in ctx_delete.items():
            await GlobalContextMgr.delete(global_ctx_name)

        await load_scripts(hass)

        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            if not global_ctx_name.startswith("file."):
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
            if not global_ctx_name.startswith("file."):
                continue
            global_ctx.start()
            global_ctx.set_auto_start(True)

    async def stop_triggers(event):
        _LOGGER.debug("stopping triggers")
        for global_ctx_name, global_ctx in GlobalContextMgr.items():
            if not global_ctx_name.startswith("file."):
                continue
            global_ctx.stop()

    hass.bus.async_listen(EVENT_HOMEASSISTANT_STARTED, start_triggers)
    hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, stop_triggers)

    return True


@bind_hass
async def load_scripts(hass):
    """Load all python scripts in FOLDER."""

    load_paths = [hass.config.path(FOLDER) + "/apps", hass.config.path(FOLDER)]

    _LOGGER.debug("load_scripts: load_paths = %s", load_paths)

    def glob_files(load_paths, match):
        source_files = []
        for path in load_paths:
            source_files += sorted(glob.glob(os.path.join(path, match)))
        return source_files

    source_files = await hass.async_add_executor_job(glob_files, load_paths, "*.py")

    for source_file in sorted(source_files):
        name = os.path.splitext(os.path.basename(source_file))[0]

        global_ctx = GlobalContext(f"file.{name}", global_sym_table={}, manager=GlobalContextMgr)
        global_ctx.set_auto_start(False)

        await GlobalContextMgr.load_file(source_file, global_ctx)
