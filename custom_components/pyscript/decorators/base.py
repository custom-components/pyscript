"""Base mixins for pyscript decorators."""

from abc import ABC, abstractmethod
import asyncio
from contextlib import suppress
import functools
import logging
from typing import Any, ClassVar

import voluptuous as vol

from ..decorator import FunctionDecoratorManager
from ..decorator_abc import CallResultHandlerDecorator, Decorator, DispatchData
from ..eval import AstEval, Function

_LOGGER = logging.getLogger(__name__)


class AutoKwargsDecorator(Decorator, ABC):
    """Mixin that copies validated kwargs into instance attributes based on annotations."""

    _auto_kw_attrs: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs):
        """Collect names of typed attributes declared on subclasses up to (but not including) Decorator."""
        super().__init_subclass__(**kwargs)
        attrs: set[str] = set()
        for klass in cls.mro():
            if klass is Decorator:
                break
            attrs.update(getattr(klass, "__annotations__", {}))
        cls._auto_kw_attrs = frozenset(attrs)

    async def validate(self) -> None:
        """Run base validation and materialize annotated kwargs as attributes."""
        await super().validate()
        for k in self.__class__.kwargs_schema.schema:
            if isinstance(k, vol.Marker):
                k = k.schema
            if k in self._auto_kw_attrs:
                setattr(self, k, self.kwargs.get(k, None))


class ExpressionDecorator(Decorator, ABC):
    """Base for AstEval-based decorators."""

    _ast_expression: AstEval = None

    def create_expression(self, expression: str) -> None:
        """Create AstEval expression."""
        _LOGGER.debug("Create expression: %s, %s", expression, self)
        dec_name = self.name
        if isinstance(self.dm, FunctionDecoratorManager):
            dec_name = "@" + dec_name + "()"

        self._ast_expression = AstEval(
            self.dm.name + " " + dec_name, self.dm.ast_ctx.global_ctx, self.dm.name
        )
        Function.install_ast_funcs(self._ast_expression)
        self._ast_expression.parse_trigger_expr(expression)

    def has_expression(self) -> bool:
        """Return True if expression was created."""
        return self._ast_expression is not None

    async def check_expression_vars(self, state_vars: dict[str, Any]) -> bool:
        """Evaluate expression and dispatch an exception event via manager on failure."""
        if not self.has_expression():
            raise AttributeError(f"{self} has no expression defined")
        try:
            return await self._ast_expression.eval(state_vars)
        except Exception as exc:
            await self.dm.handle_exception(exc)
            return False


_RESPONSE_FUTURE = "response_future"


class RequestResponseDecorator(CallResultHandlerDecorator, ABC):
    """
    Base for a decorator that answers a request with the result of the call it starts.

    Exceptions and cancelled calls give None by default; override handle_call_exception and
    handle_call_canceled to map them to the response.
    """

    @abstractmethod
    async def dispatch_request(self, data: DispatchData) -> None:
        """Start the call for a request: through the trigger handlers, or directly."""

    async def handle_request(self, func_args: dict[str, Any], timeout: float | None = None) -> Any:
        """
        Start a call for a request and wait for the response.

        If the wait is cancelled (the caller went away), the running function is cancelled too,
        and the cancellation is passed on once it has finished.
        On timeout, TimeoutError is raised and the function keeps running.
        """
        future = self.dm.hass.loop.create_future()
        data = DispatchData(func_args, trigger=self, trigger_context={_RESPONSE_FUTURE: future})
        await self.dispatch_request(data)
        if data.call_task is not None:
            data.call_task.add_done_callback(functools.partial(self._call_done, data))
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.CancelledError:
            if data.call_task is not None:
                data.call_task.cancel()
                # like awaiting a task, or a script stopping the script it called: wait for the
                # function's except/finally, so the caller (eg: a restarted automation) doesn't go
                # on while the function is still cleaning up
                with suppress(asyncio.CancelledError):
                    await data.call_task
            # the caller's own cancellation, not the function's
            raise

    def _call_done(self, data: DispatchData, task: asyncio.Task) -> None:
        """Respond if the call ended without the result handlers doing it, so the caller can't hang."""
        future = data.trigger_context[_RESPONSE_FUTURE]
        if future.done():
            return
        if task.cancelled():
            # cancelled before it started
            answer = self.handle_call_canceled(data)
        else:
            # the call failed outside the function (eg: a call handler raised); the error is already logged
            answer = self.handle_call_exception(
                data, RuntimeError("call failed before the function ran, see the log for details")
            )
        self.dm.hass.async_create_task(answer)

    def respond(self, data: DispatchData, result: Any = None, exc: BaseException | None = None) -> None:
        """Respond to the waiting caller with a result or an exception; only to this decorator's own requests."""
        future = data.trigger_context.get(_RESPONSE_FUTURE)
        if data.trigger is not self or future is None or future.done():
            return
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result)

    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Respond with the function result."""
        self.respond(data, result)
