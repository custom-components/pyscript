"""Webhook decorator."""

from __future__ import annotations

import logging
from typing import ClassVar

from homeassistant.components import webhook

from ..decorator_abc import DispatchData
from .webhook_base import WebhookBaseDecorator

_LOGGER = logging.getLogger(__name__)


class WebhookTriggerDecorator(WebhookBaseDecorator):
    """Implementation for @webhook_trigger."""

    name = "webhook_trigger"

    webhook_id2triggers: ClassVar[dict[str, set[WebhookTriggerDecorator]]] = {}

    @staticmethod
    async def _handler(_hass, webhook_id, request):
        func_args = await WebhookTriggerDecorator.build_func_args(webhook_id, request)

        for trigger in WebhookTriggerDecorator.webhook_id2triggers.get(webhook_id, set()).copy():
            trigger_args = func_args.copy()
            if trigger.has_expression():
                if not await trigger.check_expression_vars(trigger_args):
                    continue
            await trigger.dispatch(DispatchData(trigger_args))

    @staticmethod
    def _add_trigger(trigger: WebhookTriggerDecorator) -> None:
        webhook_id = trigger.webhook_id
        if webhook_id not in WebhookTriggerDecorator.webhook_id2triggers:
            WebhookTriggerDecorator.register_webhook(trigger, WebhookTriggerDecorator._handler)
            WebhookTriggerDecorator.webhook_id2triggers[webhook_id] = set()

        WebhookTriggerDecorator.webhook_id2triggers[webhook_id].add(trigger)

    @staticmethod
    def _remove_trigger(trigger: WebhookTriggerDecorator) -> None:
        webhook_id = trigger.webhook_id
        triggers = WebhookTriggerDecorator.webhook_id2triggers.get(webhook_id)
        if not triggers:
            return

        triggers.discard(trigger)
        if len(triggers) == 0:
            webhook.async_unregister(trigger.dm.hass, webhook_id)
            del WebhookTriggerDecorator.webhook_id2triggers[webhook_id]

    async def start(self):
        """Start the webhook trigger."""
        await super().start()
        self._add_trigger(self)

        _LOGGER.debug("webhook trigger %s listening on id %s", self.dm.name, self.webhook_id)

    async def stop(self):
        """Stop the webhook trigger."""
        await super().stop()
        self._remove_trigger(self)
