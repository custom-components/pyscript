"""Function call handling."""

import asyncio
import logging
import traceback

from homeassistant.core import Context

from .const import LOGGER_PATH, SERVICE_RESPONSE_NONE, SERVICE_RESPONSE_ONLY

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
    # Mappings of task id to hass contexts
    task2context = {}

    #
    # Set of tasks that are running
    #
    our_tasks = set()

    #
    # Done callbacks for each task
    #
    task2cb = {}

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
    # task id of the task that cancels and waits for other tasks,
    #
    task_reaper = None
    task_reaper_q = None

    #
    # task id of the task that awaits for coros (used by shutdown triggers)
    #
    task_waiter = None
    task_waiter_q = None

    #
    # reference counting for service registrations; the new @service trigger
    # registers the service call before the old one is removed, so we only
    # remove the service registration when the reference count goes to zero
    #
    service_cnt = {}

    #
    # save the global_ctx name where a service is registered so we can raise
    # an exception if it gets registered by a different global_ctx.
    #
    service2global_ctx = {}

    def __init__(self):
        """Warn on Function instantiation."""
        _LOGGER.error("Function class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Function."""
        cls.hass = hass
        cls.functions.update(
            {
                "event.fire": cls.event_fire,
                "service.call": cls.service_call,
                "service.has_service": cls.service_has_service,
                "task.cancel": cls.user_task_cancel,
                "task.current_task": cls.user_task_current_task,
                "task.remove_done_callback": cls.user_task_remove_done_callback,
                "task.sleep": cls.async_sleep,
                "task.wait": cls.user_task_wait,
            }
        )
        cls.ast_functions.update(
            {
                "log.debug": lambda ast_ctx: ast_ctx.get_logger().debug,
                "log.error": lambda ast_ctx: ast_ctx.get_logger().error,
                "log.info": lambda ast_ctx: ast_ctx.get_logger().info,
                "log.warning": lambda ast_ctx: ast_ctx.get_logger().warning,
                "print": lambda ast_ctx: ast_ctx.get_logger().debug,
                "task.name2id": cls.task_name2id_factory,
                "task.unique": cls.task_unique_factory,
            }
        )

        #
        # start a task which is a reaper for canceled tasks, since some # functions
        # like TrigInfo.stop() can't be async (it's called from a __del__ method)
        #
        async def task_reaper(reaper_q):
            while True:
                try:
                    cmd = await reaper_q.get()
                    if cmd[0] == "exit":
                        return
                    if cmd[0] == "cancel":
                        try:
                            cmd[1].cancel()
                            await cmd[1]
                        except asyncio.CancelledError:
                            pass
                    else:
                        _LOGGER.error("task_reaper: unknown command %s", cmd[0])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.error("task_reaper: got exception %s", traceback.format_exc(-1))

        if not cls.task_reaper:
            cls.task_reaper_q = asyncio.Queue(0)
            cls.task_reaper = cls.create_task(task_reaper(cls.task_reaper_q))

        #
        # start a task which creates tasks to run coros, and then syncs on their completion;
        # this is used by the shutdown trigger
        #
        async def task_waiter(waiter_q):
            aws = []
            while True:
                try:
                    cmd = await waiter_q.get()
                    if cmd[0] == "exit":
                        return
                    if cmd[0] == "await":
                        aws.append(cls.create_task(cmd[1]))
                    elif cmd[0] == "sync":
                        if len(aws) > 0:
                            await asyncio.gather(*aws)
                            aws = []
                        await cmd[1].put(0)
                    else:
                        _LOGGER.error("task_waiter: unknown command %s", cmd[0])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.error("task_waiter: got exception %s", traceback.format_exc(-1))

        if not cls.task_waiter:
            cls.task_waiter_q = asyncio.Queue(0)
            cls.task_waiter = cls.create_task(task_waiter(cls.task_waiter_q))

    @classmethod
    def reaper_cancel(cls, task):
        """Send a task to be canceled by the reaper."""
        cls.task_reaper_q.put_nowait(["cancel", task])

    @classmethod
    async def reaper_stop(cls):
        """Tell the reaper task to exit."""
        if cls.task_reaper:
            cls.task_reaper_q.put_nowait(["exit"])
            await cls.task_reaper
            cls.task_reaper = None
            cls.task_reaper_q = None

    @classmethod
    def waiter_await(cls, coro):
        """Send a coro to be awaited by the waiter task."""
        cls.task_waiter_q.put_nowait(["await", coro])

    @classmethod
    async def waiter_sync(cls):
        """Wait until the waiter queue is empty."""
        if cls.task_waiter:
            sync_q = asyncio.Queue(0)
            cls.task_waiter_q.put_nowait(["sync", sync_q])
            await sync_q.get()

    @classmethod
    async def waiter_stop(cls):
        """Tell the waiter task to exit."""
        if cls.task_waiter:
            cls.task_waiter_q.put_nowait(["exit"])
            await cls.task_waiter
            cls.task_waiter = None
            cls.task_waiter_q = None

    @classmethod
    async def async_sleep(cls, duration):
        """Implement task.sleep()."""
        await asyncio.sleep(float(duration))

    @classmethod
    async def event_fire(cls, event_type, **kwargs):
        """Implement event.fire()."""
        curr_task = asyncio.current_task()
        if "context" in kwargs and isinstance(kwargs["context"], Context):
            context = kwargs["context"]
            del kwargs["context"]
        else:
            context = cls.task2context.get(curr_task, None)

        cls.hass.bus.async_fire(event_type, kwargs, context=context)

    @classmethod
    def store_hass_context(cls, hass_context):
        """Store a context against the running task."""
        curr_task = asyncio.current_task()
        cls.task2context[curr_task] = hass_context

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
                        # tell the reaper task to cancel us
                        #
                        cls.reaper_cancel(curr_task)
                        # wait to be canceled
                        await asyncio.sleep(100000)
                elif task != curr_task and task in cls.our_tasks:
                    # only cancel tasks if they are ones we started
                    cls.reaper_cancel(task)
            if curr_task in cls.our_tasks:
                if name in cls.unique_name2task:
                    task = cls.unique_name2task[name]
                    if task in cls.unique_task2name:
                        cls.unique_task2name[task].discard(name)
                cls.unique_name2task[name] = curr_task
                if curr_task not in cls.unique_task2name:
                    cls.unique_task2name[curr_task] = set()
                cls.unique_task2name[curr_task].add(name)

        return task_unique

    @classmethod
    async def user_task_cancel(cls, task=None):
        """Implement task.cancel()."""
        do_sleep = False
        if not task:
            task = asyncio.current_task()
            do_sleep = True
        if task not in cls.our_tasks:
            raise TypeError(f"{task} is not a user-started task")
        cls.reaper_cancel(task)
        if do_sleep:
            # wait to be canceled
            await asyncio.sleep(100000)

    @classmethod
    async def user_task_current_task(cls):
        """Implement task.current_task()."""
        return asyncio.current_task()

    @classmethod
    def task_name2id_factory(cls, ctx):
        """Define and return task.name2id() for this context."""

        def user_task_name2id(name=None):
            """Implement task.name2id()."""
            prefix = f"{ctx.get_global_ctx_name()}."
            if name is None:
                ret = {}
                for task_name, task_id in cls.unique_name2task.items():
                    if task_name.startswith(prefix):
                        ret[task_name[len(prefix) :]] = task_id
                return ret
            if prefix + name in cls.unique_name2task:
                return cls.unique_name2task[prefix + name]
            raise NameError(f"task name '{name}' is unknown")

        return user_task_name2id

    @classmethod
    async def user_task_wait(cls, aws, **kwargs):
        """Implement task.wait()."""
        return await asyncio.wait(aws, **kwargs)

    @classmethod
    def user_task_remove_done_callback(cls, task, callback):
        """Implement task.remove_done_callback()."""
        cls.task2cb[task]["cb"].pop(callback, None)

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
        curr_task = asyncio.current_task()
        hass_args = {}
        for keyword, typ, default in [
            ("context", [Context], cls.task2context.get(curr_task, None)),
            ("blocking", [bool], None),
            ("return_response", [bool], None),
        ]:
            if keyword in kwargs and type(kwargs[keyword]) in typ:
                hass_args[keyword] = kwargs.pop(keyword)
            elif default:
                hass_args[keyword] = default

        return await cls.hass_services_async_call(domain, name, kwargs, **hass_args)

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

        def service_call_factory(domain, service):
            async def service_call(*args, **kwargs):
                curr_task = asyncio.current_task()
                hass_args = {}
                for keyword, typ, default in [
                    ("context", [Context], cls.task2context.get(curr_task, None)),
                    ("blocking", [bool], None),
                    ("return_response", [bool], None),
                ]:
                    if keyword in kwargs and type(kwargs[keyword]) in typ:
                        hass_args[keyword] = kwargs.pop(keyword)
                    elif default:
                        hass_args[keyword] = default

                if len(args) != 0:
                    raise TypeError(f"service {domain}.{service} takes only keyword arguments")

                return await cls.hass_services_async_call(domain, service, kwargs, **hass_args)

            return service_call

        return service_call_factory(domain, service)

    @classmethod
    async def hass_services_async_call(cls, domain, service, kwargs, **hass_args):
        """Call a hass async service."""
        if SERVICE_RESPONSE_ONLY is None:
            # backwards compatibility < 2023.7
            await cls.hass.services.async_call(domain, service, kwargs, **hass_args)
        else:
            # allow service responses >= 2023.7
            if (
                "return_response" in hass_args
                and hass_args["return_response"]
                and "blocking" not in hass_args
            ):
                hass_args["blocking"] = True
            elif (
                "return_response" not in hass_args
                and cls.hass.services.supports_response(domain, service) == SERVICE_RESPONSE_ONLY
            ):
                hass_args["return_response"] = True
                if "blocking" not in hass_args:
                    hass_args["blocking"] = True
            return await cls.hass.services.async_call(domain, service, kwargs, **hass_args)

    @classmethod
    async def run_coro(cls, coro, ast_ctx=None):
        """Run coroutine task and update unique task on start and exit."""
        #
        # Add a placeholder for the new task so we know it's one we started
        #
        task: asyncio.Task = None
        try:
            task = asyncio.current_task()
            cls.our_tasks.add(task)
            if ast_ctx is not None:
                cls.task_done_callback_ctx(task, ast_ctx)
            result = await coro
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.error("run_coro: got exception %s", traceback.format_exc(-1))
        finally:
            if task in cls.task2cb:
                for callback, info in cls.task2cb[task]["cb"].items():
                    ast_ctx, args, kwargs = info
                    await ast_ctx.call_func(callback, None, *args, **kwargs)
                    if ast_ctx.get_exception_obj():
                        ast_ctx.get_logger().error(ast_ctx.get_exception_long())
                        break
            if task in cls.unique_task2name:
                for name in cls.unique_task2name[task]:
                    del cls.unique_name2task[name]
                del cls.unique_task2name[task]
            cls.task2context.pop(task, None)
            cls.task2cb.pop(task, None)
            cls.our_tasks.discard(task)

    @classmethod
    def create_task(cls, coro, ast_ctx=None):
        """Create a new task that runs a coroutine."""
        return cls.hass.loop.create_task(cls.run_coro(coro, ast_ctx=ast_ctx))

    @classmethod
    def service_register(
        cls, global_ctx_name, domain, service, callback, supports_response=SERVICE_RESPONSE_NONE
    ):
        """Register a new service callback."""
        key = f"{domain}.{service}"
        if key not in cls.service_cnt:
            cls.service_cnt[key] = 0
        if key not in cls.service2global_ctx:
            cls.service2global_ctx[key] = global_ctx_name
        if cls.service2global_ctx[key] != global_ctx_name:
            raise ValueError(
                f"{global_ctx_name}: can't register service {key}; already defined in {cls.service2global_ctx[key]}"
            )
        cls.service_cnt[key] += 1
        if SERVICE_RESPONSE_ONLY is None:
            # backwards compatibility < 2023.7
            cls.hass.services.async_register(domain, service, callback)
        else:
            # allow service responses >= 2023.7
            cls.hass.services.async_register(domain, service, callback, supports_response=supports_response)

    @classmethod
    def service_remove(cls, global_ctx_name, domain, service):
        """Remove a service callback."""
        key = f"{domain}.{service}"
        if cls.service_cnt.get(key, 0) > 1:
            cls.service_cnt[key] -= 1
            return
        cls.service_cnt[key] = 0
        cls.hass.services.async_remove(domain, service)
        cls.service2global_ctx.pop(key, None)

    @classmethod
    def task_done_callback_ctx(cls, task, ast_ctx):
        """Set the ast_ctx for a task, which is needed for done callbacks."""
        if task not in cls.task2cb or "ctx" not in cls.task2cb[task]:
            cls.task2cb[task] = {"ctx": ast_ctx, "cb": {}}

    @classmethod
    def task_add_done_callback(cls, task, ast_ctx, callback, *args, **kwargs):
        """Add a done callback to the given task."""
        if ast_ctx is None:
            ast_ctx = cls.task2cb[task]["ctx"]
        cls.task2cb[task]["cb"][callback] = [ast_ctx, args, kwargs]
