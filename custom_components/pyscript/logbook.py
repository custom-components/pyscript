"""Describe logbook events."""
import logging

from homeassistant.core import callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@callback
def async_describe_events(hass, async_describe_event):  # type: ignore
    """Describe logbook events."""

    @callback
    def async_describe_logbook_event(event):  # type: ignore
        """Describe a logbook event."""
        data = event.data
        func_args = data.get("func_args", {})
        ev_name = data.get("name", "unknown")
        ev_entity_id = data.get("entity_id", "pyscript.unknown")

        ev_trigger_type = func_args.get("trigger_type", "unknown")
        if ev_trigger_type == "event":
            ev_source = f"event {func_args.get('event_type', 'unknown event')}"
        elif ev_trigger_type == "state":
            ev_source = f"state change {func_args.get('var_name', 'unknown entity')} == {func_args.get('value', 'unknown value')}"
        elif ev_trigger_type == "time":
            ev_trigger_time = func_args.get("trigger_time", "unknown")
            if ev_trigger_time is None:
                ev_trigger_time = "startup"
            ev_source = f"time {ev_trigger_time}"
        else:
            ev_source = ev_trigger_type

        message = f"has been triggered by {ev_source}"

        return {
            "name": ev_name,
            "message": message,
            "source": ev_source,
            "entity_id": ev_entity_id,
        }

    async_describe_event(DOMAIN, "pyscript_running", async_describe_logbook_event)
