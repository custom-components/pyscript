"""Handles event firing and notification."""

import logging

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".event")


class Event:
    """Define event functions."""

    def __init__():
        """Warn on Event instantiation."""
        _LOGGER.error("Event class is not meant to be instantiated")

    def init(hass):
        """Initialize Event."""
        Event.hass = hass

        Event.hass = hass
        #
        # notify message queues by event type
        #
        Event.notify = {}
        Event.notify_remove = {}

    async def event_listener(event):
        """Listen callback for given event which updates any notifications."""

        _LOGGER.debug("event_listener(%s)", event)
        func_args = {
            "trigger_type": "event",
            "event_type": event.event_type,
        }
        func_args.update(event.data)
        await Event.update(event.event_type, func_args)

    def notify_add(event_type, queue):
        """Register to notify for events of given type to be sent to queue."""

        if event_type not in Event.notify:
            Event.notify[event_type] = set()
            _LOGGER.debug("event.notify_add(%s) -> adding event listener", event_type)
            Event.notify_remove[event_type] = Event.hass.bus.async_listen(
                event_type, Event.event_listener
            )
        Event.notify[event_type].add(queue)

    def notify_del(event_type, queue):
        """Unregister to notify for events of given type for given queue."""

        if event_type not in Event.notify or queue not in Event.notify[event_type]:
            return
        Event.notify[event_type].discard(queue)
        if len(Event.notify[event_type]) == 0:
            Event.notify_remove[event_type]()
            _LOGGER.debug("event.notify_del(%s) -> removing event listener", event_type)
            del Event.notify[event_type]
            del Event.notify_remove[event_type]

    async def update(event_type, func_args):
        """Deliver all notifications for an event of the given type."""

        _LOGGER.debug("event.update(%s, %s, %s)", event_type, vars, func_args)
        if event_type in Event.notify:
            for queue in Event.notify[event_type]:
                await queue.put(["event", func_args])
