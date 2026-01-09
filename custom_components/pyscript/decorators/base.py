"""Base mixins for pyscript decorators."""

from abc import ABC
import logging
from typing import Any

import voluptuous as vol

from ..decorator import FunctionDecoratorManager
from ..decorator_abc import Decorator, DispatchData
from ..eval import AstEval, Function

_LOGGER = logging.getLogger(__name__)


class AutoKwargsDecorator(Decorator, ABC):
    """Mixin that copies validated kwargs into instance attributes based on annotations."""

    async def validate(self) -> None:
        """Run base validation and materialize annotated kwargs as attributes."""
        await super().validate()
        for k in self.__class__.kwargs_schema.schema:
            if isinstance(k, vol.Marker):
                k = k.schema
            if k in self.__class__.__annotations__:
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
        self._ast_expression.parse(expression, mode="eval")
        exc = self._ast_expression.get_exception_obj()
        if exc is not None:
            raise exc

    def has_expression(self) -> bool:
        """Return True if expression was created."""
        return self._ast_expression is not None

    async def check_expression_vars(self, state_vars: dict[str, Any]) -> bool:
        """Evaluate expression and dispatch an exception event via manager on failure."""
        if not self.has_expression():
            raise AttributeError(f"{self} has no expression defined")
        ret = await self._ast_expression.eval(state_vars)
        if exception := self._ast_expression.get_exception_obj():
            exception_text = self._ast_expression.get_exception_long()
            await self.dm.dispatch(DispatchData({}, exception=exception, exception_text=exception_text))
            return False
        return ret
