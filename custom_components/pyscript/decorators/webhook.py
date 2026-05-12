"""Webhook decorator."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from aiohttp import hdrs, web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.webhook import SUPPORTED_METHODS
from homeassistant.helpers import config_validation as cv

from ..decorator_abc import CallResultHandlerDecorator, DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator, ExpressionDecorator

_LOGGER = logging.getLogger(__name__)


class WebhookTriggerDecorator(
    TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator, CallResultHandlerDecorator
):
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
            vol.Optional("sets_http_response_code", default=False): cv.boolean,
        }
    )

    webhook_id: str
    local_only: bool
    methods: set[str]
    sets_http_response_code: bool
    future: asyncio.Future[Any] | None = None

    webhook_id2triggers: ClassVar[dict[str, set[WebhookTriggerDecorator]]] = {}

    async def validate(self):
        """Validate the webhook trigger configuration."""
        await super().validate()
        self.webhook_id = self.args[0]

        if len(self.args) == 2:
            self.create_expression(self.args[1])

    @staticmethod
    async def _handler(hass, webhook_id, request):
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

        response_future: asyncio.Future[Any] | None = None
        futures: list[asyncio.Future[Any]] = []
        for trigger in WebhookTriggerDecorator.webhook_id2triggers.get(webhook_id, set()).copy():
            trigger_args = func_args.copy()
            if trigger.has_expression():
                if not await trigger.check_expression_vars(trigger_args):
                    continue
            future: asyncio.Future[Any] = hass.loop.create_future()
            trigger.future = future
            if trigger.sets_http_response_code:
                response_future = future
            futures.append(future)
            await trigger.dispatch(DispatchData(trigger_args))

        if not futures:
            return None

        await asyncio.gather(*futures, return_exceptions=True)

        if response_future is None:
            return None
        return WebhookTriggerDecorator.coerce_response(response_future.result())

    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Resolve the response future with the decorated function's return value."""
        if data.trigger is not self:
            return
        response_future = self.future
        if response_future is not None and not response_future.done():
            response_future.set_result(result)

    @staticmethod
    def coerce_response(value: Any) -> web.Response | None:
        """Convert a webhook function return value to an aiohttp Response."""
        if value is None:
            return None
        if isinstance(value, web.Response):
            return value
        # bool is a subclass of int; reject it so True/False don't become 1/0 status codes.
        if isinstance(value, int) and not isinstance(value, bool):
            return web.Response(status=value)
        _LOGGER.warning(
            "webhook function returned unsupported type %s; expected int status code or aiohttp.web.Response",
            type(value).__name__,
        )
        return None

    @staticmethod
    def _add_trigger(trigger: WebhookTriggerDecorator) -> None:
        webhook_id = trigger.webhook_id
        existing = WebhookTriggerDecorator.webhook_id2triggers.get(webhook_id)
        if (
            trigger.sets_http_response_code
            and existing is not None
            and any(t.sets_http_response_code for t in existing)
        ):
            raise ValueError(
                f"webhook_id '{webhook_id}' already has a @webhook_trigger with "
                f"sets_http_response_code=True; only one is allowed"
            )

        if existing is None:
            webhook.async_register(
                trigger.dm.hass,
                "pyscript",  # DOMAIN
                "pyscript",  # NAME
                webhook_id,
                WebhookTriggerDecorator._handler,
                local_only=trigger.local_only,
                allowed_methods=trigger.methods,
            )
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
