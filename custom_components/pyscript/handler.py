"""Function call handling."""

import asyncio
import logging
import traceback

from homeassistant.helpers.service import async_get_all_descriptions

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".handler")


class Handler:
    """Define function handler functions."""

    def __init__(self, hass):
        """Initialize Handler."""
        self.hass = hass
        self.unique_task2name = {}
        self.unique_name2task = {}
        self.our_tasks = set()

        #
        # initial list of available functions
        #
        self.functions = {
            "event.fire": self.event_fire,
            "task.sleep": self.async_sleep,
            "task.unique": self.task_unique,
            "service.call": self.service_call,
            "service.has_service": self.service_has_service,
        }

        #
        # Functions that take the AstEval context as a first argument,
        # which is needed by a handful of special functions that need the
        # ast context
        #

        self.ast_functions = {
            "log.debug": self.get_logger_debug,
            "log.error": self.get_logger_error,
            "log.info": self.get_logger_info,
            "log.warning": self.get_logger_warning,
        }

        #
        # We create loggers for each top-level function that include
        # that function's name.  We cache them here so we only create
        # one for each function
        #
        self.loggers = {}

    async def async_sleep(self, duration):
        """Implement task.sleep()."""
        await asyncio.sleep(float(duration))

    async def event_fire(self, event_type, **kwargs):
        """Implement event.fire()."""
        self.hass.bus.async_fire(event_type, kwargs)

    async def task_unique(self, name, kill_me=False):
        """Implement task.unique()."""
        if name in self.unique_name2task:
            if kill_me:
                task = asyncio.current_task()

                # it seems we need to use another task to cancel ourselves
                # I'm sure there is a better way to cancel ourselves...
                async def cancel_self():
                    try:
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass

                asyncio.create_task(cancel_self())
                # ugh - wait to be canceled
                await asyncio.sleep(10000)
            else:
                task = self.unique_name2task[name]
                if task in self.our_tasks:
                    # only cancel tasks if they are ones we started
                    try:
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass
        task = asyncio.current_task()
        if task in self.our_tasks:
            self.unique_name2task[name] = task
            self.unique_task2name[task] = name

    def service_has_service(self, domain, name):
        """Implement service.has_service()."""
        return self.hass.services.has_service(domain, name)

    async def service_call(self, domain, name, **kwargs):
        """Implement service.call()."""
        await self.hass.services.async_call(domain, name, kwargs)

    async def service_completions(self, root):
        """Return possible completions of HASS services."""
        words = set()
        services = await async_get_all_descriptions(self.hass)
        num_period = root.count(".")
        if num_period == 1:
            domain, srv_root = root.split(".")
            if domain in services:
                for srv in services[domain].keys():
                    if srv.lower().startswith(srv_root):
                        words.add(f"{domain}.{srv}")
        elif num_period == 0:
            for domain in services.keys():
                if domain.lower().startswith(root):
                    words.add(domain)
        return words

    async def func_completions(self, root):
        """Return possible completions of functions."""
        words = set()
        funcs = self.functions.copy()
        funcs.update(self.ast_functions)
        for name in funcs.keys():
            if name.lower().startswith(root):
                words.add(name)
        return words

    def get_logger(self, ast_ctx, log_type, *arg, **kw):
        """Return a logger function tied to the execution context of a function."""

        name = ast_ctx.get_logger_name()
        if name not in self.loggers:
            #
            # Maintain a cache for efficiency.
            #
            self.loggers[name] = ast_ctx.get_logger()
        return getattr(self.loggers[name], log_type)

    def get_logger_debug(self, ast_ctx, *arg, **kw):
        """Implement log.debug()."""
        return self.get_logger(ast_ctx, "debug", *arg, **kw)

    def get_logger_error(self, ast_ctx, *arg, **kw):
        """Implement log.error()."""
        return self.get_logger(ast_ctx, "error", *arg, **kw)

    def get_logger_info(self, ast_ctx, *arg, **kw):
        """Implement log.info()."""
        return self.get_logger(ast_ctx, "info", *arg, **kw)

    def get_logger_warning(self, ast_ctx, *arg, **kw):
        """Implement log.warning()."""
        return self.get_logger(ast_ctx, "warning", *arg, **kw)

    def register(self, funcs):
        """Register functions to be available for calling."""
        for name, func in funcs.items():
            self.functions[name] = func

    def register_ast(self, funcs):
        """Register functions that need ast context to be available for calling."""
        for name, func in funcs.items():
            self.ast_functions[name] = func

    def install_ast_funcs(self, ast_ctx):
        """Install ast functions into the local symbol table."""
        sym_table = {}
        for name, func in self.ast_functions.items():
            sym_table[name] = func(ast_ctx)
        ast_ctx.set_local_sym_table(sym_table)

    def get(self, name):
        """Lookup a function locally and then as a service."""
        func = self.functions.get(name, None)
        if func:
            return func
        parts = name.split(".", 1)
        if len(parts) != 2:
            return None
        domain = parts[0]
        service = parts[1]
        if not self.hass.services.has_service(domain, service):
            return None

        async def service_call(*args, **kwargs):
            await self.hass.services.async_call(domain, service, kwargs)

        return service_call

    async def run_coro(self, coro):
        """Run coroutine task and update unique task on start and exit."""
        #
        # Add a placeholder for the new task so we know it's one we started
        #
        task = asyncio.current_task()
        self.our_tasks.add(task)
        try:
            await coro
        except asyncio.CancelledError:
            if task in self.unique_task2name:
                self.unique_name2task.pop(self.unique_task2name[task], None)
                self.unique_task2name.pop(task, None)
            self.our_tasks.discard(task)
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.error("run_coro: %s", traceback.format_exc(-1))
        if task in self.unique_task2name:
            self.unique_name2task.pop(self.unique_task2name[task], None)
            self.unique_task2name.pop(task, None)
        self.our_tasks.discard(task)

    def create_task(self, coro):
        """Create a new task that runs a coroutine."""
        return self.hass.loop.create_task(self.run_coro(coro))
