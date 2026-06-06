"""Event decorator."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import CALLBACK_TYPE, Event, callback

from ..decorator_abc import DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator, ExpressionDecorator

_LOGGER = logging.getLogger(__name__)

# No builtins: an event_filter expression may only reference event data keys.
_FILTER_GLOBALS = {"__builtins__": {}}


class EventTriggerDecorator(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator):
    """Implementation for @event_trigger."""

    name = "event_trigger"
    args_schema = vol.Schema(
        vol.All(
            [vol.Coerce(str)],
            vol.Length(min=1, max=2, msg="needs at least one argument"),
        )
    )
    kwargs_schema = vol.Schema({vol.Optional("event_filter"): str})

    event_filter: str | None

    remove_listener_callback: CALLBACK_TYPE | None = None
    _filter_code: Any = None

    async def validate(self) -> None:
        """Validate the event trigger."""
        await super().validate()
        if len(self.args) == 2:
            self.create_expression(self.args[1])
        if self.event_filter:
            try:
                self._filter_code = compile(self.event_filter, "<event_filter>", "eval")
            except SyntaxError as err:
                raise TypeError(
                    f"function '{self.dm.func_name}' defined in "
                    f"{self.dm.ast_ctx.get_global_ctx_name()}: decorator @{self.name} "
                    f"event_filter {err.msg!r} is not a valid expression"
                ) from err

    @callback
    def _bus_event_filter(self, event_data: Any) -> bool:
        """Evaluate the event_filter expression natively at the event bus."""
        if self._filter_code is None:
            return True
        try:
            return bool(eval(self._filter_code, _FILTER_GLOBALS, event_data))  # noqa: S307
        except Exception as exc:
            _LOGGER.error("event_trigger %s event_filter %r raised %s", self.args[0], self.event_filter, exc)
            return False

    async def _event_callback(self, event: Event) -> None:
        _LOGGER.debug("Event trigger received: %s %s", type(event), event)
        func_args = {
            "trigger_type": "event",
            "event_type": event.event_type,
            "context": event.context,
        }
        func_args.update(event.data)
        if self.has_expression():
            if not await self.check_expression_vars(func_args):
                return

        await self.dispatch(DispatchData(func_args))

    async def start(self) -> None:
        """Start the event trigger."""
        await super().start()
        # Only register the bus filter when an event_filter is given; HA then
        # rejects filter-mandatory events (e.g. EVENT_STATE_REPORTED) instead of
        # firehosing every event of that type.
        event_filter = self._bus_event_filter if self._filter_code is not None else None
        try:
            self.remove_listener_callback = self.dm.hass.bus.async_listen(
                self.args[0], self._event_callback, event_filter=event_filter
            )
        except Exception as exc:
            # Keep sibling triggers alive; surface the error and leave this one inert.
            self.remove_listener_callback = None
            _LOGGER.error("Event trigger failed to start for event %s: %s", self.args[0], exc)
            await self.dm.handle_exception(exc)
            return
        _LOGGER.debug("Event trigger started for event: %s", self.args[0])
        _LOGGER.debug("Remove listener: %s", self.remove_listener_callback)

    async def stop(self) -> None:
        """Stop the event trigger."""
        await super().stop()
        if self.remove_listener_callback:
            self.remove_listener_callback()
