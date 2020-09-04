"""Function call handling."""

import asyncio
import logging
import traceback

from homeassistant.helpers.service import async_get_all_descriptions

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".handler")


class Handler:
    """Define function handler functions."""

    def __init__():
        """Warn on Handler instantiation."""
        _LOGGER.error("Handler class is not meant to be instantiated")

    def init(hass):
        """Initialize Handler."""
        Handler.hass = hass
        Handler.unique_task2name = {}
        Handler.unique_name2task = {}
        Handler.our_tasks = set()

        #
        # initial list of available functions
        #
        Handler.functions = {
            "event.fire": Handler.event_fire,
            "task.sleep": Handler.async_sleep,
            "task.unique": Handler.task_unique,
            "service.call": Handler.service_call,
            "service.has_service": Handler.service_has_service,
        }

        #
        # Functions that take the AstEval context as a first argument,
        # which is needed by a handful of special functions that need the
        # ast context
        #

        Handler.ast_functions = {
            "log.debug": Handler.get_logger_debug,
            "log.error": Handler.get_logger_error,
            "log.info": Handler.get_logger_info,
            "log.warning": Handler.get_logger_warning,
            "print": Handler.get_logger_debug,
        }

        #
        # We create loggers for each top-level function that include
        # that function's name.  We cache them here so we only create
        # one for each function
        #
        Handler.loggers = {}

    async def entity_ids(domain=None):
        """Implement entity_ids."""
        return Handler.hass.states.async_entity_ids(domain)

    async def async_sleep(duration):
        """Implement task.sleep()."""
        await asyncio.sleep(float(duration))

    async def event_fire(event_type, **kwargs):
        """Implement event.fire()."""
        Handler.hass.bus.async_fire(event_type, kwargs)

    async def task_unique(name, kill_me=False):
        """Implement task.unique()."""
        if name in Handler.unique_name2task:
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
                task = Handler.unique_name2task[name]
                if task in Handler.our_tasks:
                    # only cancel tasks if they are ones we started
                    try:
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass
        task = asyncio.current_task()
        if task in Handler.our_tasks:
            Handler.unique_name2task[name] = task
            Handler.unique_task2name[task] = name

    def unique_name_used(name):
        """Return whether the current unique name is in use."""
        return name in Handler.unique_name2task

    def service_has_service(domain, name):
        """Implement service.has_service()."""
        return Handler.hass.services.has_service(domain, name)

    async def service_call(domain, name, **kwargs):
        """Implement service.call()."""
        await Handler.hass.services.async_call(domain, name, kwargs)

    async def service_completions(root):
        """Return possible completions of HASS services."""
        words = set()
        services = await async_get_all_descriptions(Handler.hass)
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

    async def func_completions(root):
        """Return possible completions of functions."""
        words = set()
        funcs = Handler.functions.copy()
        funcs.update(Handler.ast_functions)
        for name in funcs.keys():
            if name.lower().startswith(root):
                words.add(name)
        return words

    def get_logger(ast_ctx, log_type, *arg, **kw):
        """Return a logger function tied to the execution context of a function."""

        name = ast_ctx.get_logger_name()
        if name not in Handler.loggers:
            #
            # Maintain a cache for efficiency.
            #
            Handler.loggers[name] = ast_ctx.get_logger()
        return getattr(Handler.loggers[name], log_type)

    def get_logger_debug(ast_ctx, *arg, **kw):
        """Implement log.debug()."""
        return Handler.get_logger(ast_ctx, "debug", *arg, **kw)

    def get_logger_error(ast_ctx, *arg, **kw):
        """Implement log.error()."""
        return Handler.get_logger(ast_ctx, "error", *arg, **kw)

    def get_logger_info(ast_ctx, *arg, **kw):
        """Implement log.info()."""
        return Handler.get_logger(ast_ctx, "info", *arg, **kw)

    def get_logger_warning(ast_ctx, *arg, **kw):
        """Implement log.warning()."""
        return Handler.get_logger(ast_ctx, "warning", *arg, **kw)

    def register(funcs):
        """Register functions to be available for calling."""
        for name, func in funcs.items():
            Handler.functions[name] = func

    def register_ast(funcs):
        """Register functions that need ast context to be available for calling."""
        for name, func in funcs.items():
            Handler.ast_functions[name] = func

    def install_ast_funcs(ast_ctx):
        """Install ast functions into the local symbol table."""
        sym_table = {}
        for name, func in Handler.ast_functions.items():
            sym_table[name] = func(ast_ctx)
        ast_ctx.set_local_sym_table(sym_table)

    def get(name):
        """Lookup a function locally and then as a service."""
        func = Handler.functions.get(name, None)
        if func:
            return func
        parts = name.split(".", 1)
        if len(parts) != 2:
            return None
        domain = parts[0]
        service = parts[1]
        if not Handler.hass.services.has_service(domain, service):
            return None

        async def service_call(*args, **kwargs):
            await Handler.hass.services.async_call(domain, service, kwargs)

        return service_call

    async def run_coro(coro):
        """Run coroutine task and update unique task on start and exit."""
        #
        # Add a placeholder for the new task so we know it's one we started
        #
        task = asyncio.current_task()
        Handler.our_tasks.add(task)
        try:
            await coro
        except asyncio.CancelledError:
            if task in Handler.unique_task2name:
                Handler.unique_name2task.pop(Handler.unique_task2name[task], None)
                Handler.unique_task2name.pop(task, None)
            Handler.our_tasks.discard(task)
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.error("run_coro: %s", traceback.format_exc(-1))
        if task in Handler.unique_task2name:
            Handler.unique_name2task.pop(Handler.unique_task2name[task], None)
            Handler.unique_task2name.pop(task, None)
        Handler.our_tasks.discard(task)

    def create_task(coro):
        """Create a new task that runs a coroutine."""
        return Handler.hass.loop.create_task(Handler.run_coro(coro))
