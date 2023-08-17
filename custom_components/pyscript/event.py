"""Handles event firing and notification."""

import logging

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".event")


class Event:
    """Define event functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # notify message queues by event type
    #
    notify = {}
    notify_remove = {}

    def __init__(self):
        """Warn on Event instantiation."""
        _LOGGER.error("Event class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Event."""

        cls.hass = hass

    @classmethod
    async def event_listener(cls, event):
        """Listen callback for given event which updates any notifications."""

        func_args = {
            "trigger_type": "event",
            "event_type": event.event_type,
            "context": event.context,
        }
        func_args.update(event.data)
        await cls.update(event.event_type, func_args)

    @classmethod
    def notify_add(cls, event_type, queue):
        """Register to notify for events of given type to be sent to queue."""

        if event_type not in cls.notify:
            cls.notify[event_type] = set()
            _LOGGER.debug("event.notify_add(%s) -> adding event listener", event_type)
            cls.notify_remove[event_type] = cls.hass.bus.async_listen(event_type, cls.event_listener)
        cls.notify[event_type].add(queue)

    @classmethod
    def notify_del(cls, event_type, queue):
        """Unregister to notify for events of given type for given queue."""

        if event_type not in cls.notify or queue not in cls.notify[event_type]:
            return
        cls.notify[event_type].discard(queue)
        if len(cls.notify[event_type]) == 0:
            cls.notify_remove[event_type]()
            _LOGGER.debug("event.notify_del(%s) -> removing event listener", event_type)
            del cls.notify[event_type]
            del cls.notify_remove[event_type]

    @classmethod
    async def update(cls, event_type, func_args):
        """Deliver all notifications for an event of the given type."""

        _LOGGER.debug("event.update(%s, %s)", event_type, func_args)
        if event_type in cls.notify:
            for queue in cls.notify[event_type]:
                await queue.put(["event", func_args.copy()])
