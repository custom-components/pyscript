"""Function call handling."""

import asyncio
import functools
import logging
import traceback

from homeassistant.core import Context

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".function")


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

    #
    # task id of the task that cancel and waits for other tasks
    #
    task_cancel_repeaer = None

    def __init__(self):
        """Warn on Function instantiation."""
        _LOGGER.error("Function class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Function."""
        cls.hass = hass
        cls.functions.update(
            {
                "task.executor": cls.task_executor,
                "event.fire": cls.event_fire,
                "task.sleep": cls.async_sleep,
                "service.call": cls.service_call,
                "service.has_service": cls.service_has_service,
            }
        )
        cls.ast_functions.update(
            {
                "log.debug": lambda ast_ctx: ast_ctx.get_logger().debug,
                "log.error": lambda ast_ctx: ast_ctx.get_logger().error,
                "log.info": lambda ast_ctx: ast_ctx.get_logger().info,
                "log.warning": lambda ast_ctx: ast_ctx.get_logger().warning,
                "print": lambda ast_ctx: ast_ctx.get_logger().debug,
                "task.unique": cls.task_unique_factory,
            }
        )

        #
        # start a task which is a reaper for canceled tasks, since some # functions
        # like TrigInfo.stop() can't be async (it's called from a __del__ method)
        #
        async def task_cancel_reaper(reaper_q):
            while True:
                try:
                    try:
                        task = await reaper_q.get()
                        if task is None:
                            return
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.error("task_cancel_reaper: got exception %s", traceback.format_exc(-1))

        if not cls.task_cancel_repeaer:
            cls.task_reaper_q = asyncio.Queue(0)
            cls.task_cancel_repeaer = Function.create_task(task_cancel_reaper(cls.task_reaper_q))

    @classmethod
    async def reaper_stop(cls):
        """Tell the reaper task to exit by sending a special task None."""
        if cls.task_cancel_repeaer:
            cls.task_cancel(None)
            await cls.task_cancel_repeaer
            cls.task_cancel_repeaer = None
            cls.task_reaper_q = None

    @classmethod
    async def async_sleep(cls, duration):
        """Implement task.sleep()."""
        await asyncio.sleep(float(duration))

    @classmethod
    async def event_fire(cls, event_type, **kwargs):
        """Implement event.fire()."""
        if "context" in kwargs and isinstance(kwargs["context"], Context):
            context = kwargs["context"]
            del kwargs["context"]
            cls.hass.bus.async_fire(event_type, kwargs, context=context)
        else:
            cls.hass.bus.async_fire(event_type, kwargs)

    @classmethod
    def task_unique_factory(cls, ctx):
        """Define and return task.unique() for this context."""

        async def task_unique(name, kill_me=False):
            """Implement task.unique()."""
            name = f"{ctx.get_global_ctx_name()}.{name}"
            curr_task = asyncio.current_task()
            if name in cls.unique_name2task:
                task = cls.unique_name2task[name]
                if kill_me:
                    if task != curr_task:
                        #
                        # it seems we can't cancel ourselves, so we
                        # tell the repeaer task to cancel us
                        #
                        Function.task_cancel(curr_task)
                        # wait to be canceled
                        await asyncio.sleep(100000)
                elif task != curr_task and task in cls.our_tasks:
                    # only cancel tasks if they are ones we started
                    try:
                        task.cancel()
                        await task
                    except asyncio.CancelledError:
                        pass
            if curr_task in cls.our_tasks:
                cls.unique_name2task[name] = curr_task
                cls.unique_task2name[curr_task] = name

        return task_unique

    @classmethod
    async def task_executor(cls, func, *args, **kwargs):
        """Implement task.executor()."""
        if asyncio.iscoroutinefunction(func) or not callable(func):
            raise TypeError("function is not callable by task.executor()")
        return await cls.hass.async_add_executor_job(functools.partial(func, **kwargs), *args)

    @classmethod
    def unique_name_used(cls, ctx, name):
        """Return whether the current unique name is in use."""
        name = f"{ctx.get_global_ctx_name()}.{name}"
        return name in cls.unique_name2task

    @classmethod
    def service_has_service(cls, domain, name):
        """Implement service.has_service()."""
        return cls.hass.services.has_service(domain, name)

    @classmethod
    async def service_call(cls, domain, name, **kwargs):
        """Implement service.call()."""
        if "context" in kwargs and isinstance(kwargs["context"], Context):
            context = kwargs["context"]
            del kwargs["context"]
            await cls.hass.services.async_call(domain, name, kwargs, context=context)
        else:
            await cls.hass.services.async_call(domain, name, kwargs)

    @classmethod
    async def service_completions(cls, root):
        """Return possible completions of HASS services."""
        words = set()
        services = cls.hass.services.async_services()
        num_period = root.count(".")
        if num_period == 1:
            domain, svc_root = root.split(".")
            if domain in services:
                words |= {f"{domain}.{svc}" for svc in services[domain] if svc.lower().startswith(svc_root)}
        elif num_period == 0:
            words |= {domain for domain in services if domain.lower().startswith(root)}

        return words

    @classmethod
    async def func_completions(cls, root):
        """Return possible completions of functions."""
        funcs = {**cls.functions, **cls.ast_functions}
        words = {name for name in funcs if name.lower().startswith(root)}

        return words

    @classmethod
    def register(cls, funcs):
        """Register functions to be available for calling."""
        cls.functions.update(funcs)

    @classmethod
    def register_ast(cls, funcs):
        """Register functions that need ast context to be available for calling."""
        cls.ast_functions.update(funcs)

    @classmethod
    def install_ast_funcs(cls, ast_ctx):
        """Install ast functions into the local symbol table."""
        sym_table = {name: func(ast_ctx) for name, func in cls.ast_functions.items()}
        ast_ctx.set_local_sym_table(sym_table)

    @classmethod
    def get(cls, name):
        """Lookup a function locally and then as a service."""
        func = cls.functions.get(name, None)
        if func:
            return func

        name_parts = name.split(".")
        if len(name_parts) != 2:
            return None

        domain, service = name_parts
        if not cls.service_has_service(domain, service):
            return None

        async def service_call(*args, **kwargs):
            if "context" in kwargs and isinstance(kwargs["context"], Context):
                context = kwargs["context"]
                del kwargs["context"]
                await cls.hass.services.async_call(domain, service, kwargs, context=context)
            else:
                await cls.hass.services.async_call(domain, service, kwargs)

        return service_call

    @classmethod
    async def run_coro(cls, coro):
        """Run coroutine task and update unique task on start and exit."""
        #
        # Add a placeholder for the new task so we know it's one we started
        #
        try:
            task = asyncio.current_task()
            cls.our_tasks.add(task)
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.error("run_coro: got exception %s", traceback.format_exc(-1))
        finally:
            if task in cls.unique_task2name:
                del cls.unique_name2task[cls.unique_task2name[task]]
                del cls.unique_task2name[task]
            cls.our_tasks.discard(task)

    @classmethod
    def create_task(cls, coro):
        """Create a new task that runs a coroutine."""
        return cls.hass.loop.create_task(cls.run_coro(coro))

    @classmethod
    def task_cancel(cls, task):
        """Send a task to be canceled by the reaper."""
        cls.task_reaper_q.put_nowait(task)
