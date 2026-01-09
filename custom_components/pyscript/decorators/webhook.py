"""Webhook decorator."""

import logging

from aiohttp import hdrs
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.webhook import SUPPORTED_METHODS
from homeassistant.helpers import config_validation as cv

from ..decorator_abc import DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator, ExpressionDecorator

_LOGGER = logging.getLogger(__name__)


class WebhookTriggerDecorator(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator):
    """Implementation for @webhook_trigger."""

    name = "webhook_trigger"
    args_schema = vol.Schema(
        vol.All(
            [vol.Coerce(str)],
            vol.Length(min=1, max=2, msg="needs at least one argument"),
        )
    )
    kwargs_schema = vol.Schema(
        {
            vol.Optional("local_only", default=True): cv.boolean,
            vol.Optional("methods"): vol.All(list[str], [vol.In(SUPPORTED_METHODS)]),
        }
    )

    webhook_id: str
    local_only: bool
    methods: set[str]

    async def validate(self):
        """Validate the webhook trigger configuration."""
        await super().validate()
        self.webhook_id = self.args[0]

        if len(self.args) == 2:
            self.create_expression(self.args[1])

    async def _handler(self, hass, webhook_id, request):
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

        if self.has_expression():
            if not await self.check_expression_vars(func_args):
                return

        await self.dispatch(DispatchData(func_args))

    async def start(self):
        """Start the webhook trigger."""
        await super().start()
        webhook.async_register(
            self.dm.hass,
            "pyscript",  # DOMAIN
            "pyscript",  # NAME
            self.webhook_id,
            self._handler,
            local_only=self.local_only,
            allowed_methods=self.methods,
        )

        _LOGGER.debug("webhook trigger %s listening on id %s", self.dm.name, self.webhook_id)

    async def stop(self):
        """Stop the webhook trigger."""
        await super().stop()
        webhook.async_unregister(self.dm.hass, self.webhook_id)
