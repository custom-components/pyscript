"""Global context handling."""

from collections import OrderedDict
import io
import logging

import yaml

from homeassistant.const import SERVICE_RELOAD
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_set_service_schema

from .const import DOMAIN, LOGGER_PATH, SERVICE_JUPYTER_KERNEL_START
from .eval import AstEval
from .trigger import TrigInfo


class GlobalContext:
    """Define class for global variables and trigger context."""

    def __init__(
        self,
        name,
        hass,
        global_sym_table=None,
        state_func=None,
        event_func=None,
        handler_func=None,
        trig_time_func=None,
    ):
        """Initialize GlobalContext."""
        self.name = name
        self.hass = hass
        self.global_sym_table = global_sym_table if global_sym_table else {}
        self.triggers = {}
        self.triggers_new = {}
        self.services = set()
        self.state_func = state_func
        self.handler_func = handler_func
        self.event_func = event_func
        self.trig_time_func = trig_time_func
        self.logger = logging.getLogger(LOGGER_PATH + "." + name)
        self.auto_start = False

    async def trigger_init(self, func):
        """Initialize any decorator triggers for a newly defined function."""
        func_name = func.get_name()
        trig_args = {}
        got_reqd_dec = False
        trig_decorators_reqd = {
            "time_trigger",
            "state_trigger",
            "event_trigger",
        }
        trig_decorators = {
            "time_trigger",
            "state_trigger",
            "event_trigger",
            "state_active",
            "time_active",
            "task_unique",
        }
        decorator_used = set()
        for dec in func.get_decorators():
            dec_name, dec_args, dec_kwargs = dec[0], dec[1], dec[2]
            if dec_name in decorator_used:
                self.logger.error(
                    "%s defined in %s: decorator %s repeated; ignoring decorator",
                    func_name,
                    self.name,
                    dec_name,
                )
                continue
            decorator_used.add(dec_name)
            if dec_name in trig_decorators_reqd:
                got_reqd_dec = True
            if dec_name in trig_decorators:
                if dec_name not in trig_args:
                    trig_args[dec_name] = {}
                    trig_args[dec_name]["args"] = []
                if dec_args is not None:
                    trig_args[dec_name]["args"] += dec_args
                if dec_kwargs is not None:
                    trig_args[dec_name]["kwargs"] = dec_kwargs
            elif dec_name == "service":
                if dec_args is not None:
                    self.logger.error(
                        "%s defined in %s: decorator @service takes no arguments; ignoring decorator",
                        func_name,
                        self.name,
                    )
                    continue
                if func_name in (SERVICE_RELOAD, SERVICE_JUPYTER_KERNEL_START):
                    self.logger.error(
                        "function '%s' in %s with @service conflicts with builtin service; ignoring (please rename function)",
                        func_name,
                        self.name,
                    )
                    return
                desc = func.get_doc_string()
                if desc is None or desc == "":
                    desc = f"pyscript function {func_name}()"
                desc = desc.lstrip(" \n\r")
                if desc.startswith("yaml"):
                    try:
                        desc = desc[4:].lstrip(" \n\r")
                        file_desc = io.StringIO(desc)
                        service_desc = (
                            yaml.load(file_desc, Loader=yaml.BaseLoader) or OrderedDict()
                        )
                        file_desc.close()
                    except Exception as exc:
                        self.logger.error(
                            "Unable to decode yaml doc_string for %s(): %s", func_name, str(exc)
                        )
                        raise HomeAssistantError(exc)
                else:
                    fields = OrderedDict()
                    for arg in func.get_positional_args():
                        fields[arg] = OrderedDict(description=f"argument {arg}")
                    service_desc = {"description": desc, "fields": fields}

                def pyscript_service_factory(func_name, func):
                    async def pyscript_service_handler(call):
                        """Handle python script service calls."""
                        # self.logger.debug("service call to %s", func_name)
                        #
                        # use a new AstEval context so it can run fully independently
                        # of other instances (except for global_ctx which is common)
                        #
                        ast_ctx = AstEval(
                            f"{self.name}.{func_name}",
                            global_ctx=self,
                            state_func=self.state_func,
                            event_func=self.event_func,
                            handler_func=self.handler_func,
                        )
                        self.handler_func.install_ast_funcs(ast_ctx)
                        func_args = {
                            "trigger_type": "service",
                        }
                        func_args.update(call.data)

                        async def do_service_call(func, ast_ctx, data):
                            await func.call(ast_ctx, [], call.data)
                            if ast_ctx.get_exception_obj():
                                ast_ctx.get_logger().error(ast_ctx.get_exception_long())

                        self.handler_func.create_task(do_service_call(func, ast_ctx, func_args))

                    return pyscript_service_handler

                self.hass.services.async_register(
                    DOMAIN,
                    func_name,
                    pyscript_service_factory(func_name, func),
                )
                async_set_service_schema(self.hass, DOMAIN, func_name, service_desc)
                self.services.add(func_name)
            else:
                self.logger.warning(
                    "%s defined in %s: unknown decorator @%s: ignored",
                    func_name,
                    self.name,
                    dec_name,
                )

        if func_name in self.services and "service" not in decorator_used:
            # function redefined without @service, so remove it
            self.hass.services.async_remove(DOMAIN, func_name)
            self.services.discard(func_name)

        for dec_name in trig_decorators:
            if dec_name in trig_args and len(trig_args[dec_name]["args"]) == 0:
                trig_args[dec_name]["args"] = None

        #
        # check that we have the right number of arguments, and that they are
        # strings
        #
        arg_check = {
            "event_trigger": {1, 2},
            "state_active": {1},
            "state_trigger": {1},
            "task_unique": {1},
            "time_active": {"*"},
            "time_trigger": {"*"},
        }
        for dec_name, arg_cnt in arg_check.items():
            if dec_name not in trig_args or trig_args[dec_name]["args"] is None:
                continue
            if "*" not in arg_cnt and len(trig_args[dec_name]["args"]) not in arg_cnt:
                self.logger.error(
                    "%s defined in %s: decorator @%s got %d argument%s, expected %s; ignoring decorator",
                    func_name,
                    self.name,
                    dec_name,
                    len(trig_args[dec_name]["args"]),
                    "s" if len(trig_args[dec_name]["args"]) > 1 else "",
                    " or ".join([str(cnt) for cnt in sorted(arg_cnt)]),
                )
                del trig_args[dec_name]
                break
            for arg_num, arg in enumerate(trig_args[dec_name]["args"]):
                if not isinstance(arg, str):
                    self.logger.error(
                        "%s defined in %s: decorator @%s argument %d should be a string; ignoring decorator",
                        func_name,
                        self.name,
                        dec_name,
                        arg_num + 1
                    )
                    del trig_args[dec_name]
                    break
            if arg_cnt == {1}:
                trig_args[dec_name]["args"] = trig_args[dec_name]["args"][0]

        kwarg_check = {
            "task_unique": {"kill_me"},
        }
        for dec_name in trig_args:
            if dec_name not in kwarg_check and "kwargs" in trig_args[dec_name]:
                self.logger.error(
                    "%s defined in %s: decorator @%s doesn't take keyword arguments; ignored",
                    func_name,
                    self.name,
                    dec_name,
                )
            if dec_name in kwarg_check and "kwargs" in trig_args[dec_name]:
                used_kw = set(trig_args[dec_name]["kwargs"].keys())
                if not used_kw.issubset(kwarg_check[dec_name]):
                    self.logger.error(
                        "%s defined in %s: decorator @%s valid keyword arguments are: %s; others ignored",
                        func_name,
                        self.name,
                        dec_name,
                        ", ".join(sorted(kwarg_check[dec_name])),
                    )

        if not got_reqd_dec and len(trig_args) > 0:
            self.logger.error(
                "%s defined in %s: needs at least one trigger decorator (ie: %s)",
                func_name,
                self.name,
                ", ".join(sorted(trig_decorators_reqd)),
            )
            return

        if len(trig_args) == 0:
            #
            # function defined without triggers; remove old one if necessary
            #
            if func_name in self.triggers:
                await self.triggers[func_name].stop()
                del self.triggers[func_name]
            if func_name in self.triggers_new:
                del self.triggers_new[func_name]
            return

        trig_args["action"] = func
        trig_args["action_ast_ctx"] = AstEval(
            f"{self.name}.{func_name}",
            global_ctx=self,
            state_func=self.state_func,
            event_func=self.event_func,
            handler_func=self.handler_func,
        )
        self.handler_func.install_ast_funcs(trig_args["action_ast_ctx"])
        trig_args["global_sym_table"] = self.global_sym_table

        if func_name in self.triggers:
            await self.triggers[func_name].stop()

        self.triggers_new[func_name] = TrigInfo(
            f"{self.name}.{func_name}",
            trig_args,
            event_func=self.event_func,
            state_func=self.state_func,
            handler_func=self.handler_func,
            trig_time=self.trig_time_func,
            global_ctx=self,
        )

        if self.auto_start:
            await self.start()

    def set_auto_start(self, auto_start):
        """Set the auto-start flag."""
        self.auto_start = auto_start
        
    async def start(self):
        """Start new triggers."""
        for name, trig in self.triggers_new.items():
            trig.start()
            self.triggers[name] = trig
        self.triggers_new = {}

    async def stop(self, name=None):
        """Stop triggers (all by default) and unregister services."""
        if name is None:
            for trig in self.triggers.values():
                await trig.stop()
            for srv_name in self.services:
                self.hass.services.async_remove(DOMAIN, srv_name)
            self.triggers = {}
            self.services = set()
        else:
            if name in self.triggers:
                await self.triggers[name].stop()
                del self.triggers[name]
            if name in self.services:
                self.hass.services.async_remove(DOMAIN, name)
                self.services.discard(name)

    def get_name(self):
        """Return the global context name."""
        return self.name

    def get_global_sym_table(self):
        """Return the global symbol table."""
        return self.global_sym_table


class GlobalContextMgr:
    """Define class for all global contexts."""

    def __init__(self, handler_func):
        """Initialize GlobalContextMgr."""
        self.handler_func = handler_func
        self.contexts = {}
        self.name_seq = 0

        def get_global_ctx_factory(ast_ctx):
            """Generate a pyscript.get_global_ctx() function with given ast_ctx."""
            async def get_global_ctx():
                return ast_ctx.get_global_ctx_name()
            return get_global_ctx

        def list_global_ctx_factory(ast_ctx):
            """Generate a pyscript.list_global_ctx() function with given ast_ctx."""
            async def list_global_ctx():
                ctx_names = set(self.contexts.keys())
                curr_ctx_name = ast_ctx.get_global_ctx_name()
                ctx_names.discard(curr_ctx_name)
                return [curr_ctx_name] + sorted(sorted(ctx_names))
            return list_global_ctx

        def set_global_ctx_factory(ast_ctx):
            """Generate a pyscript.set_global_ctx() function with given ast_ctx."""
            async def set_global_ctx(name):
                global_ctx = self.get(name)
                if global_ctx is None:
                    raise NameError(f"global context '{name}' does not exist")
                ast_ctx.set_global_ctx(global_ctx)
                ast_ctx.set_logger_name(global_ctx.name)
            return set_global_ctx

        self.ast_funcs = {
            "pyscript.get_global_ctx": get_global_ctx_factory,
            "pyscript.list_global_ctx": list_global_ctx_factory,
            "pyscript.set_global_ctx": set_global_ctx_factory,
        }

        self.handler_func.register_ast(self.ast_funcs)

    def get(self, name):
        """Return the GlobalContext given a name."""
        return self.contexts.get(name, None)

    def set(self, name, global_ctx):
        """Save the GlobalContext by name."""
        self.contexts[name] = global_ctx

    def items(self):
        """Return all the global context items."""
        return sorted(self.contexts.items())

    async def delete(self, name):
        """Delete the given GlobalContext."""
        if name in self.contexts:
            global_ctx = self.contexts[name]
            await global_ctx.stop()
            del self.contexts[name]

    def new_name(self, root):
        """Find a unique new name by appending a sequence number to root."""
        while True:
            name = f"{root}{self.name_seq}"
            self.name_seq += 1
            if name not in self.contexts:
                return name
