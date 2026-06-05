"""Base mixins for pyscript decorators."""

from abc import ABC
import inspect
import logging
from typing import Any

import voluptuous as vol

from ..decorator import FunctionDecoratorManager
from ..decorator_abc import Decorator
from ..eval import AstEval, Function

_LOGGER = logging.getLogger(__name__)


class AutoKwargsDecorator(Decorator, ABC):
    """Mixin that copies validated kwargs into instance attributes based on annotations."""

    async def validate(self) -> None:
        """Run base validation and materialize annotated kwargs as attributes."""
        await super().validate()
        # Collect annotations declared anywhere in the class hierarchy so kwargs
        # handling keeps working when attributes are declared on a shared base
        # class (a class's ``__annotations__`` only exposes its own annotations).
        # ``Decorator`` is skipped because its ``args``/``kwargs`` annotations would
        # otherwise clobber the validated values.
        annotations = {
            name
            for klass in type(self).__mro__
            if klass is not Decorator
            for name in inspect.get_annotations(klass)
        }
        for k in type(self).kwargs_schema.schema:
            if isinstance(k, vol.Marker):
                k = k.schema
            if k in annotations:
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
