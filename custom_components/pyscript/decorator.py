from __future__ import annotations

import ast
import asyncio
import logging
import os
import weakref
from typing import Type, ClassVar, Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Context

from .decorator_abc import (
    Decorator,
    DecoratorManager,
    DispatchData,
    DecoratorManagerStatus,
    TriggerHandlerDecorator,
    CallHandlerDecorator,
    TriggerDecorator,
    CallResultHandlerDecorator,
)
from .eval import AstEval, EvalFunc, EvalFuncVar
from .function import Function
from .state import State

_LOGGER = logging.getLogger(__name__)


class DecoratorRegistry:
    """Decorator registry."""

    _decorators: dict[str, Type[Decorator]]  # decorator name to class
    hass: ClassVar[HomeAssistant]
    prefix: ClassVar[str] = "e"

    @classmethod
    def init(cls, hass: HomeAssistant, config_entry: ConfigEntry = None) -> None:
        """Initialize the decorator registry."""
        cls.hass = hass
        cls._decorators = {}
        enabled = False
        if "PYTEST_CURRENT_TEST" in os.environ:
            enabled = "NODM" not in os.environ
        elif config_entry is not None and config_entry.data.get("dm", False):
            enabled = True

        if enabled:
            cls.prefix = ""
            space = "\n" + " " * 35
            border = space + "=" * 35
            _LOGGER.warning(border + space + "DecoratorManager enabled by default" + border)
        else:
            cls.prefix = "e"

        DecoratorManager.hass = hass

        Function.register_ast({cls.prefix + "task.wait_until": DecoratorRegistry.wait_until_factory})

        from .decorators import DECORATORS

        for dec_type in DECORATORS:
            cls.register(dec_type)

    @classmethod
    def register(cls, dec_type: Type[Decorator]):
        """Register a decorator."""
        if not dec_type.name:
            raise TypeError(f"Decorator name is required {dec_type}")

        name = cls.prefix + dec_type.name
        _LOGGER.debug("Registering decorator @%s %s", name, dec_type)
        if name in cls._decorators:
            _LOGGER.warning("Overriding decorator: %s %s with %s", name, cls._decorators[name], dec_type)
        cls._decorators[name] = dec_type

    @classmethod
    async def get_decorator_by_expr(cls, ast_ctx: AstEval, dec_expr: ast.expr) -> Decorator | None:
        """Return decorator instance from an AST decorator expression."""
        dec_name = None
        has_args = False

        if isinstance(dec_expr, ast.Name):  # decorator without ()
            dec_name = dec_expr.id
        elif isinstance(dec_expr, ast.Call) and isinstance(dec_expr.func, ast.Name):
            dec_name = dec_expr.func.id
            has_args = True

        if know_decorator := cls._decorators.get(dec_name):
            if has_args:
                args = await ast_ctx.eval_elt_list(dec_expr.args)
                kwargs = {keyw.arg: await ast_ctx.aeval(keyw.value) for keyw in dec_expr.keywords}
            else:
                args = []
                kwargs = {}

            decorator = know_decorator(args, kwargs, dec_expr.lineno, dec_expr.col_offset)
            return decorator

        return None

    @classmethod
    async def wait_until(cls, ast_ctx: AstEval, *arg, **kwargs):
        """Build a temporary decorator manager that waits until one of trigger decorators fires."""
        func_args = set(kwargs.keys())
        if len(func_args) == 0:
            return {"trigger_type": "none"}

        found_args = set()
        dm = WaitUntilDecoratorManager(ast_ctx, **kwargs)

        found_args.add("timeout")
        found_args.add("__test_handshake__")

        prefix_len = len(DecoratorRegistry.prefix)
        for dec_name, dec_class in cls._decorators.items():
            if not issubclass(dec_class, TriggerDecorator):
                continue
            if prefix_len > 0:
                dec_name = dec_name[prefix_len:]
            if dec_name not in func_args:
                continue

            dec_args = kwargs[dec_name]
            if not isinstance(dec_args, list):
                dec_args = [dec_args]
            found_args.add(dec_name)

            dec_kwargs = {}
            func_args.remove(dec_name)
            kwargs_schema_keys = dec_class.kwargs_schema.schema.keys()
            for key in kwargs_schema_keys:
                if key in kwargs:
                    dec_kwargs[key] = kwargs[key]
                    found_args.add(key)
            dec = dec_class(dec_args, dec_kwargs, ast_ctx.lineno, ast_ctx.col_offset)
            dm.add(dec)

        unknown_args = set(kwargs.keys()).difference(found_args)
        if unknown_args:
            raise ValueError(f"Unknown arguments: {unknown_args}")
        await dm.validate()

        # state_trigger sets __test_handshake__ after the initial checks.
        # In some cases, it returns a value before __test_handshake__ is set.
        if "state_trigger" not in kwargs:
            if test_handshake := kwargs.get("__test_handshake__"):
                #
                # used for testing to avoid race conditions
                # we use this as a handshake that we are about to
                # listen to the queue
                #
                State.set(test_handshake[0], test_handshake[1])
        await dm.start()

        ret = await dm.wait_until()

        return ret

    @classmethod
    def wait_until_factory(cls, ast_ctx):
        """Return wrapper to call to astFunction with the ast context."""

        async def wait_until_call(*arg, **kw):
            return await cls.wait_until(ast_ctx, *arg, **kw)

        return wait_until_call


class WaitUntilDecoratorManager(DecoratorManager):
    """Decorator manager for task.wait_until."""

    def __init__(self, ast_ctx: AstEval, **kwargs: dict[str, Any]) -> None:
        super().__init__(ast_ctx, ast_ctx.name)
        self.kwargs = kwargs
        self._future: asyncio.Future[DispatchData] = self.hass.loop.create_future()
        self.timeout_decorator = None
        if timeout := kwargs.get("timeout"):
            to_dec = DecoratorRegistry._decorators.get(DecoratorRegistry.prefix + "time_trigger")
            self.timeout_decorator = to_dec(
                [f"once(now + {timeout}s)"], {}, ast_ctx.lineno, ast_ctx.col_offset
            )
            self.add(self.timeout_decorator)

    async def dispatch(self, data: DispatchData) -> None:
        """Resolve the waiting future on the first incoming dispatch."""
        _LOGGER.debug("task.wait_until dispatch: %s", data)
        if self._future.done():
            _LOGGER.debug("task.wait_until future already completed: %s", self._future.exception())
            # ignore another calls
            return
        await self.stop()
        self._future.set_result(data)

    async def wait_until(self) -> dict[str, Any]:
        """Wait for dispatch and normalize the return payload."""
        data = await self._future
        if data.exception is not None:
            raise data.exception
        if data.trigger == self.timeout_decorator:
            ret = {"trigger_type": "timeout"}
        else:
            ret = data.func_args
        _LOGGER.debug("task.wait_until finish: %s", ret)
        return ret


DT = TypeVar("DT", bound=Decorator)


class FunctionDecoratorManager(DecoratorManager):
    """Maintain and validate a set of decorators applied to a function."""

    def __init__(self, ast_ctx: AstEval, eval_func_var: EvalFuncVar) -> None:
        super().__init__(ast_ctx, f"{ast_ctx.get_global_ctx_name()}.{eval_func_var.get_name()}")
        self.eval_func: EvalFunc = eval_func_var.func

        self.logger = self.eval_func.logger

        def on_func_var_deleted():
            if self.status is DecoratorManagerStatus.RUNNING:
                self.hass.async_create_task(self.stop())

        weakref.finalize(eval_func_var, on_func_var_deleted)

    async def _call(self, data: DispatchData) -> None:
        handlers = self.get_decorators(CallHandlerDecorator)
        result_handlers = self.get_decorators(CallResultHandlerDecorator)

        for handler_dec in handlers:
            if await handler_dec.handle_call(data) is False:
                self.logger.debug("Calling canceled by %s", handler_dec)
                # notify handlers with "None"
                for result_handler_dec in result_handlers:
                    await result_handler_dec.handle_call_result(data, None)
                return
        # Fire an event indicating that pyscript is running
        # Note: the event must have an entity_id for logbook to work correctly.
        ev_name = self.name.replace(".", "_")
        ev_entity_id = f"pyscript.{ev_name}"

        event_data = {"name": ev_name, "entity_id": ev_entity_id, "func_args": data.func_args}
        self.hass.bus.async_fire("pyscript_running", event_data, context=data.hass_context)
        # Store HASS Context for this Task
        Function.store_hass_context(data.hass_context)

        result = await data.call_ast_ctx.call_func(self.eval_func, None, **data.func_args)
        for result_handler_dec in result_handlers:
            await result_handler_dec.handle_call_result(data, result)

        if data.call_ast_ctx.get_exception_obj():
            data.call_ast_ctx.get_logger().error(data.call_ast_ctx.get_exception_long())

    async def dispatch(self, data: DispatchData) -> None:
        """Handle a trigger dispatch: run guards, create a context, and invoke the function."""
        _LOGGER.debug("Dispatching for %s: %s", self.name, data)

        if data.exception:
            self.logger.error(data.exception_text)
            return

        decorators = self.get_decorators(TriggerHandlerDecorator)
        for dec in decorators:
            if await dec.handle_dispatch(data) is False:
                self.logger.debug("Trigger not active due to %s", dec)
                return

        action_ast_ctx = AstEval(
            f"{self.eval_func.global_ctx_name}.{self.eval_func.name}", self.eval_func.global_ctx
        )
        Function.install_ast_funcs(action_ast_ctx)
        data.call_ast_ctx = action_ast_ctx

        # Create new HASS Context with incoming as parent
        if "context" in data.func_args and isinstance(data.func_args["context"], Context):
            data.hass_context = Context(parent_id=data.func_args["context"].id)
        else:
            data.hass_context = Context()

        self.logger.debug(
            "trigger %s got %s trigger, running action (kwargs = %s)",
            self.name,
            data.trigger,
            data.func_args,
        )

        task = Function.create_task(self._call(data), ast_ctx=action_ast_ctx)
        Function.task_done_callback_ctx(task, action_ast_ctx)
