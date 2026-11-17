"""Base mixins for pyscript decorators."""

from abc import ABC
import logging
from typing import Any, ClassVar

import voluptuous as vol

from ..decorator import FunctionDecoratorManager
from ..decorator_abc import Decorator
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
        self._ast_expression.parse(expression, mode="eval")

    def has_expression(self) -> bool:
        """Return True if expression was created."""
        return self._ast_expression is not None

    async def check_expression_vars(self, state_vars: dict[str, Any]) -> bool:
        """Evaluate expression and dispatch an exception event via manager on failure."""
        if not self.has_expression():
            raise AttributeError(f"{self} has no expression defined")
        # per-instance method trigger: make self visible to the condition
        inst_weak = getattr(self.dm, "instance_weak", None)
        if inst_weak is not None:
            inst = inst_weak()
            if inst is None:
                return False
            state_vars = {**state_vars, "self": inst}
        try:
            return await self._ast_expression.eval(state_vars)
        except Exception as exc:
            await self.dm.handle_exception(exc)
            return False
        finally:
            # eval() persists state_vars; drop self so it can't keep the instance alive
            getattr(self._ast_expression, "local_sym_table", {}).pop("self", None)
