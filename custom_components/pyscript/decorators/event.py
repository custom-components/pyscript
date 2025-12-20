import logging

import voluptuous as vol
from homeassistant.core import Event, CALLBACK_TYPE

from .base import ExpressionDecorator
from ..decorator_abc import DispatchData, TriggerDecorator

_LOGGER = logging.getLogger(__name__)


class EventTriggerDecorator(TriggerDecorator, ExpressionDecorator):
    """Implementation for @event_trigger."""

    name = "event_trigger"
    args_schema = vol.Schema(
        vol.All(
            [vol.Coerce(str)],
            vol.Length(min=1, max=2, msg="needs at least one argument"),
        )
    )

    remove_listener_callback: CALLBACK_TYPE | None = None

    async def validate(self) -> None:
        """Validate the event trigger."""
        await super().validate()
        if len(self.args) == 2:
            self.create_expression(self.args[1])

    async def _event_callback(self, event: Event) -> None:
        """Callback for the event trigger."""
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
        self.remove_listener_callback = self.dm.hass.bus.async_listen(self.args[0], self._event_callback)
        _LOGGER.debug("Event trigger started for event: %s", self.args[0])
        _LOGGER.debug("Remove listener: %s", self.remove_listener_callback)

    async def stop(self) -> None:
        """Stop the event trigger."""
        await super().stop()
        if self.remove_listener_callback:
            self.remove_listener_callback()
