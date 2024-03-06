"""Handles webhooks and notification."""

import logging

from aiohttp import hdrs

from homeassistant.components import webhook

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
    async def webhook_handler(cls, hass, webhook_id, request):
        """Listen callback for given webhook which updates any notifications."""

        func_args = {
            "trigger_type": "webhook",
            "webhook_id": webhook_id,
        }

        if "json" in request.headers.get(hdrs.CONTENT_TYPE, ""):
            func_args["payload"] = await request.json()
        else:
            # Could potentially return multiples of a key - only take the first
            payload_multidict = await request.post()
            func_args["payload"] = {k: payload_multidict.getone(k) for k in payload_multidict.keys()}

        await cls.update(webhook_id, func_args)

    @classmethod
    def notify_add(cls, webhook_id, local_only, methods, queue):
        """Register to notify for webhooks of given type to be sent to queue."""
        if webhook_id not in cls.notify:
            cls.notify[webhook_id] = set()
            _LOGGER.debug("webhook.notify_add(%s) -> adding webhook listener", webhook_id)
            webhook.async_register(
                cls.hass,
                "pyscript",  # DOMAIN
                "pyscript",  # NAME
                webhook_id,
                cls.webhook_handler,
                local_only=local_only,
                allowed_methods=methods,
            )
            cls.notify_remove[webhook_id] = lambda: webhook.async_unregister(cls.hass, webhook_id)

        cls.notify[webhook_id].add(queue)

    @classmethod
    def notify_del(cls, webhook_id, queue):
        """Unregister to notify for webhooks of given type for given queue."""

        if webhook_id not in cls.notify or queue not in cls.notify[webhook_id]:
            return
        cls.notify[webhook_id].discard(queue)
        if len(cls.notify[webhook_id]) == 0:
            cls.notify_remove[webhook_id]()
            _LOGGER.debug("webhook.notify_del(%s) -> removing webhook listener", webhook_id)
            del cls.notify[webhook_id]
            del cls.notify_remove[webhook_id]

    @classmethod
    async def update(cls, webhook_id, func_args):
        """Deliver all notifications for an webhook of the given type."""

        _LOGGER.debug("webhook.update(%s, %s)", webhook_id, func_args)
        if webhook_id in cls.notify:
            for queue in cls.notify[webhook_id]:
                await queue.put(["webhook", func_args.copy()])
