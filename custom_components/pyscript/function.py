"""Function call handling."""

import asyncio
import functools
import logging
import traceback

from homeassistant.helpers.service import async_get_all_descriptions

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".handler")


class Function:
    """Define function handler functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # Mappings of tasks ids <-> task names
    #
    unique_task2name = {}
    unique_name2task = {}

    #
    # Set of tasks that are running
    #
    our_tasks = set()

    #
    # initial list of available functions
    #
    functions = {}

    #
    # Functions that take the AstEval context as a first argument,
    # which is needed by a handful of special functions that need the
    # ast context
    #
    ast_functions = {}

    def __init__(self):
        """Warn on Function instantiation."""
        _LOGGER.error("Function class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Function."""
        cls.hass = hass
        cls.functions.update({
            "task.executor": cls.task_executor,
            "event.fire": cls.event_fire,
            "task.sleep": cls.async_sleep,
            "task.unique": cls.task_unique,
            "service.call": cls.service_call,
            "service.has_service": cls.service_has_service,
        })
        cls.ast_functions.update({
            "log.debug": lambda ast_ctx: ast_ctx.get_logger().debug,
            "log.error": lambda ast_ctx: ast_ctx.get_logger().error,
            "log.info": lambda ast_ctx: ast_ctx.get_logger().info,
            "log.warning": lambda ast_ctx: ast_ctx.get_logger().warning,
            "print": lambda ast_ctx: ast_ctx.get_logger().debug,
        })

    @classmethod
    async def entity_ids(cls, domain=None):
        """Implement entity_ids."""
        return cls.hass.states.async_entity_ids(domain)

    @classmethod
    async def async_sleep(cls, duration):
        """Implement task.sleep()."""
        await asyncio.sleep(float(duration))

    @classmethod
    async def event_fire(cls, event_type, **kwargs):
        """Implement event.fire()."""
        cls.hass.bus.async_fire(event_type, kwargs)

    @classmethod
    async def task_unique(cls, name, kill_me=False):
        """Implement task.unique()."""
        if name in cls.unique_name2task:
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
                task = cls.unique_name2task[name]
                if task in cls.our_tasks:
                    # only cancel tasks if they are ones we started
                    try:
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass
        task = asyncio.current_task()
        if task in cls.our_tasks:
            cls.unique_name2task[name] = task
            cls.unique_task2name[task] = name

    @classmethod
    async def task_executor(cls, func, *args, **kwargs):
        """Implement task.executor()."""
        if asyncio.iscoroutinefunction(func) or not callable(func):
            raise TypeError("function is not callable by task.executor()")
        return await cls.hass.async_add_executor_job(
            functools.partial(func, **kwargs), *args
        )

    @classmethod
    def unique_name_used(cls, name):
        """Return whether the current unique name is in use."""
        return name in cls.unique_name2task

    @classmethod
    def service_has_service(cls, domain, name):
        """Implement service.has_service()."""
        return cls.hass.services.has_service(domain, name)

    @classmethod
    async def service_call(cls, domain, name, **kwargs):
        """Implement service.call()."""
        await cls.hass.services.async_call(domain, name, kwargs)

    @classmethod
    async def service_completions(cls, root):
        """Return possible completions of HASS services."""
        words = set()
        services = await async_get_all_descriptions(cls.hass)
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

    @classmethod
    async def func_completions(cls, root):
        """Return possible completions of functions."""
        words = set()
        funcs = cls.functions.copy()
        funcs.update(cls.ast_functions)
        for name in funcs.keys():
            if name.lower().startswith(root):
                words.add(name)
        return words

    @classmethod
    def register(cls, funcs):
        """Register functions to be available for calling."""
        for name, func in funcs.items():
            cls.functions[name] = func

    @classmethod
    def register_ast(cls, funcs):
        """Register functions that need ast context to be available for calling."""
        for name, func in funcs.items():
            cls.ast_functions[name] = func

    @classmethod
    def install_ast_funcs(cls, ast_ctx):
        """Install ast functions into the local symbol table."""
        sym_table = {}
        for name, func in cls.ast_functions.items():
            sym_table[name] = func(ast_ctx)
        ast_ctx.set_local_sym_table(sym_table)

    @classmethod
    def get(cls, name):
        """Lookup a function locally and then as a service."""
        func = cls.functions.get(name, None)
        if func:
            return func
        parts = name.split(".", 1)
        if len(parts) != 2:
            return None
        domain = parts[0]
        service = parts[1]
        if not cls.hass.services.has_service(domain, service):
            return None

        async def service_call(*args, **kwargs):
            await cls.hass.services.async_call(domain, service, kwargs)

        return service_call

    @classmethod
    async def run_coro(cls, coro):
        """Run coroutine task and update unique task on start and exit."""
        #
        # Add a placeholder for the new task so we know it's one we started
        #
        task = asyncio.current_task()
        cls.our_tasks.add(task)
        try:
            await coro
        except asyncio.CancelledError:
            if task in cls.unique_task2name:
                cls.unique_name2task.pop(cls.unique_task2name[task], None)
                cls.unique_task2name.pop(task, None)
            cls.our_tasks.discard(task)
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.error("run_coro: %s", traceback.format_exc(-1))
        if task in cls.unique_task2name:
            cls.unique_name2task.pop(cls.unique_task2name[task], None)
            cls.unique_task2name.pop(task, None)
        cls.our_tasks.discard(task)

    @classmethod
    def create_task(cls, coro):
        """Create a new task that runs a coroutine."""
        return cls.hass.loop.create_task(cls.run_coro(coro))
