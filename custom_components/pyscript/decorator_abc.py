from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import field, dataclass
from enum import StrEnum
from typing import ClassVar, Any, TypeVar, Type, final

import voluptuous as vol
from homeassistant.core import Context, HomeAssistant

from . import trigger
from .eval import AstEval

_LOGGER = logging.getLogger(__name__)


def dt_now():
    """Return current time."""
    # For test compatibility. The tests patch this function
    return trigger.dt_now()


class DecoratorManagerStatus(StrEnum):
    """Status of a decorator manager."""

    INIT = "init"  # initial status when created
    NO_DECORATORS = "no_decorators"  # no decorators found
    VALIDATED = "validated"
    INVALID = "invalid"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass()
class DispatchData:
    """Data for a dispatch event."""

    func_args: dict[str, Any]
    trigger: TriggerDecorator | None = field(default=None, kw_only=True)
    trigger_context: dict[str, Any] = field(default_factory=dict, kw_only=True)

    call_ast_ctx: AstEval | None = field(default=None, kw_only=True)
    hass_context: Context | None = field(default=None, kw_only=True)

    # Normally shouldn’t be used.
    exception: Exception | None = field(default=None, kw_only=True)
    exception_text: str | None = field(default=None, kw_only=True)


class Decorator(ABC):
    """Generic decorator abstraction."""

    # Subclasses should override.
    name: ClassVar[str] = ""
    # without args by default
    args_schema: ClassVar[vol.Schema] = vol.Schema([], extra=vol.PREVENT_EXTRA)
    # without kwargs by default
    kwargs_schema: ClassVar[vol.Schema] = vol.Schema({}, extra=vol.PREVENT_EXTRA)

    # instance attributes
    dm: DecoratorManager
    raw_args: list[Any]
    raw_kwargs: dict[str, Any]

    args: list[Any]
    kwargs: dict[str, Any]

    @final
    def __init__(
        self, raw_args: list[Any], raw_kwargs: dict[str, Any], lineno: int, col_offset: int
    ) -> None:
        """Initialize the decorator definition."""

        self.raw_args = raw_args
        self.raw_kwargs = raw_kwargs
        self.lineno = lineno
        self.col_offset = col_offset

    async def validate(self) -> None:
        """Validate the arguments."""

        _LOGGER.debug("Validating %s", self.name)

        try:
            self.args = self.args_schema(self.raw_args)
            self.kwargs = self.kwargs_schema(self.raw_kwargs)

        except vol.Invalid as err:
            # FIXME For test compatibility. Update the message in the future.
            if len(err.path) == 1:
                if "extra keys not allowed" in err.msg:
                    message = f"invalid keyword argument '{err.path[0]}'"
                else:
                    message = f"keyword '{err.path[0]}' {err}"
            else:
                message = str(err)

            type_error = TypeError(
                f"function '{self.dm.func_name}' defined in {self.dm.ast_ctx.get_global_ctx_name()}: "
                f"decorator @{self.name} {message}"
            )
            raise type_error from err

    async def start(self):
        """Start the decorator."""

    async def stop(self):
        """Stop the decorator."""

    def __repr__(self):
        parts = []
        if self.raw_args is not None:
            parts.append(",".join(map(str, self.raw_args)))
        if self.raw_kwargs is not None:
            parts += [f"{k}={v!r}" for k, v in self.raw_kwargs.items()]
        return f"@{self.name}({', '.join(parts)})"


DecoratorType = TypeVar("DecoratorType", bound=Decorator)


class DecoratorManager(ABC):
    """Maintain and validate a set of decorators"""

    hass: ClassVar[HomeAssistant]

    def __init__(self, ast_ctx: AstEval, name: str) -> None:
        self.ast_ctx = ast_ctx
        self.name = name
        self.func_name = name.split(".")[-1]
        self.logger = ast_ctx.get_logger()

        self.lineno = ast_ctx.lineno
        self.col_offset = ast_ctx.col_offset

        self.status: DecoratorManagerStatus = DecoratorManagerStatus.INIT
        self.startup_time = None
        self._decorators: list[Decorator] = []

    def update_status(self, new_status: DecoratorManagerStatus) -> None:
        """Update the manager status."""
        if self.status is new_status:
            return
        _LOGGER.debug("DM %s status: %s -> %s", self.name, self.status.value, new_status.value)
        self.status = new_status

        if new_status in (DecoratorManagerStatus.STOPPED, DecoratorManagerStatus.INVALID):
            del self._decorators[:]

    def add(self, decorator: Decorator) -> None:
        """Add a decorator to the manager."""
        _LOGGER.debug("Add %s to %s", decorator, self)
        self._decorators.append(decorator)
        decorator.dm = self

    def get_decorators(self, decorator_type: Type[DecoratorType] | None = None) -> list[DecoratorType]:
        """Get decorators of a specific type."""
        if decorator_type is None:
            return self._decorators.copy()
        return [dec for dec in self._decorators if isinstance(dec, decorator_type)]

    async def validate(self) -> None:
        """Validate all decorators."""
        lineno, col_offset = self.ast_ctx.lineno, self.ast_ctx.col_offset
        try:
            for decorator in self._decorators:
                self.ast_ctx.lineno, self.ast_ctx.col_offset = decorator.lineno, decorator.col_offset
                _LOGGER.debug("Validating decorator: %s", decorator)
                self.lineno, self.col_offset = decorator.lineno, decorator.col_offset
                await decorator.validate()
        except Exception:
            self.update_status(DecoratorManagerStatus.INVALID)
            raise

        self.ast_ctx.lineno, self.ast_ctx.col_offset = lineno, col_offset

        if len(self._decorators) == 0:
            self.update_status(DecoratorManagerStatus.NO_DECORATORS)
        else:
            self.update_status(DecoratorManagerStatus.VALIDATED)

    async def start(self):
        """Start all decorators."""
        if self.status is not DecoratorManagerStatus.VALIDATED:
            raise RuntimeError(f"Starting not valid {self}")

        self.startup_time = dt_now()
        self.update_status(DecoratorManagerStatus.RUNNING)
        started = []
        for decorator in self._decorators:
            _LOGGER.debug("Starting decorator: %s", decorator)
            try:
                await decorator.start()
                started.append(decorator)
            except Exception as err:
                self.logger.exception("%s start failed: %s", self, err)
                for started_dec in started:
                    await self._stop_decorator(started_dec)
                self.startup_time = None
                self.update_status(DecoratorManagerStatus.INVALID)
                raise

    async def _stop_decorator(self, decorator: Decorator) -> None:
        try:
            await decorator.stop()
        except Exception as err:
            _LOGGER.exception("%s stop failed: %s", self, err)

    async def stop(self):
        """Stop all decorators."""
        if self.status is not DecoratorManagerStatus.RUNNING:
            _LOGGER.warning("Stopping before starting for %s (status=%s)", self.name, self.status.value)
            return

        _LOGGER.debug("Stopping all decorators %s", self)
        for decorator in self._decorators:
            await self._stop_decorator(decorator)

        self.update_status(DecoratorManagerStatus.STOPPED)

    @abstractmethod
    async def dispatch(self, data: DispatchData) -> None:
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}({self.status}) {self._decorators} for {self.name}()>"


class TriggerDecorator(Decorator, ABC):
    """Base class for trigger-based decorators."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # kwargs for all triggers
        if "kwargs" not in cls.kwargs_schema.schema.keys():
            cls.kwargs_schema = cls.kwargs_schema.extend(
                {vol.Optional("kwargs"): vol.Coerce(dict[str, Any], msg="should be type dict")}
            )

    async def dispatch(self, data: DispatchData):
        """Dispatch a trigger call to the function."""
        if not data.trigger:
            data.trigger = self

        data.func_args.update(self.kwargs.get("kwargs", {}))

        await self.dm.dispatch(data)


class TriggerHandlerDecorator(Decorator, ABC):
    """Base class for trigger handler decorators."""

    async def validate(self) -> None:
        """Validate the decorated function."""
        await super().validate()
        decorators = self.dm.get_decorators(TriggerDecorator)
        if len(decorators) == 0:
            # FIXME For test compatibility. Update the message in the future.
            trig_decorators_reqd = {
                "event_trigger",
                "mqtt_trigger",
                "state_trigger",
                "time_trigger",
                "webhook_trigger",
            }
            raise ValueError(
                f"{self.dm.func_name} defined in {self.dm.ast_ctx.get_global_ctx_name()}: "
                f"needs at least one trigger decorator (ie: {', '.join(sorted(trig_decorators_reqd))})"
            )

    @abstractmethod
    async def handle_dispatch(self, data: DispatchData) -> bool | None:
        """Handle a trigger dispatch call. Return False for stop dispatching."""


class CallHandlerDecorator(Decorator, ABC):
    """Base class for call-based handlers."""

    @abstractmethod
    async def handle_call(self, data: DispatchData) -> bool | None:
        """Handle an action call. Return False for stop calling."""
        pass


class CallResultHandlerDecorator(Decorator, ABC):
    """Base class for call-based result handlers."""

    @abstractmethod
    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Handle an action call result."""
        pass
