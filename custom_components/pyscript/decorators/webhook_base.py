"""Shared base for webhook decorators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import hdrs, web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.webhook import SUPPORTED_METHODS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..decorator_abc import TriggerDecorator
from .base import AutoKwargsDecorator, ExpressionDecorator


class WebhookBaseDecorator(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator):
    """Shared argument handling and request parsing for webhook decorators."""

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
    methods: list[str] | None

    async def validate(self):
        """Validate the webhook configuration."""
        await super().validate()
        self.webhook_id = self.args[0]

        if len(self.args) == 2:
            self.create_expression(self.args[1])

    @staticmethod
    async def build_func_args(webhook_id: str, request: web.Request) -> dict[str, Any]:
        """Build the function arguments from an incoming webhook request."""
        func_args = {
            "trigger_type": "webhook",
            "webhook_id": webhook_id,
            "request": request,
        }

        if "json" in request.headers.get(hdrs.CONTENT_TYPE, ""):
            func_args["payload"] = await request.json()
        else:
            # Could potentially return multiples of a key - only take the first
            payload_multidict = await request.post()
            func_args["payload"] = {k: payload_multidict.getone(k) for k in payload_multidict.keys()}

        return func_args

    @staticmethod
    def register_webhook(
        decorator: WebhookBaseDecorator,
        handler: Callable[[HomeAssistant, str, web.Request], Awaitable[web.Response | None]],
    ) -> None:
        """Register a webhook decorator's id with Home Assistant."""
        webhook.async_register(
            decorator.dm.hass,
            "pyscript",  # DOMAIN
            "pyscript",  # NAME
            decorator.webhook_id,
            handler,
            local_only=decorator.local_only,
            allowed_methods=decorator.methods,
        )
