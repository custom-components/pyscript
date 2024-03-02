"""Handles webhooks and notification."""

import logging

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".webhook")


class Webhook:
    """Define webhook functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # notify message queues by webhook type
    #
    notify = {}
    notify_remove = {}

    def __init__(self):
        """Warn on Webhook instantiation."""
        _LOGGER.error("Webhook class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Webhook."""

        cls.hass = hass

    @classmethod
    async def webhook_listener(cls, event):
        """Listen callback for given webhook which updates any notifications."""

        func_args = {
            "trigger_type": "event",
            "event_type": event.event_type,
            "context": event.context,
        }
        func_args.update(event.data)
        await cls.update(event.event_type, func_args)

    @classmethod
    def notify_add(cls, webhook_type, queue):
        """Register to notify for webhooks of given type to be sent to queue."""

        if webhook_type not in cls.notify:
            cls.notify[webhook_type] = set()
            _LOGGER.debug("webhook.notify_add(%s) -> adding webhook listener", webhook_type)
            cls.notify_remove[webhook_type] = cls.hass.bus.async_listen(webhook_type, cls.webhook_listener)
        cls.notify[webhook_type].add(queue)

    @classmethod
    def notify_del(cls, webhook_type, queue):
        """Unregister to notify for webhooks of given type for given queue."""

        if webhook_type not in cls.notify or queue not in cls.notify[webhook_type]:
            return
        cls.notify[webhook_type].discard(queue)
        if len(cls.notify[webhook_type]) == 0:
            cls.notify_remove[webhook_type]()
            _LOGGER.debug("webhook.notify_del(%s) -> removing webhook listener", webhook_type)
            del cls.notify[webhook_type]
            del cls.notify_remove[webhook_type]

    @classmethod
    async def update(cls, webhook_type, func_args):
        """Deliver all notifications for an webhook of the given type."""

        _LOGGER.debug("webhook.update(%s, %s)", webhook_type, func_args)
        if webhook_type in cls.notify:
            for queue in cls.notify[webhook_type]:
                await queue.put(["webhook", func_args.copy()])
