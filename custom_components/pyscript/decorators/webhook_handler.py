"""Webhook handler decorator."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
from typing import Any, ClassVar

from aiohttp import web

from homeassistant.components import webhook

from ..decorator_abc import CallResultHandlerDecorator, DispatchData
from .webhook_base import WebhookBaseDecorator

_LOGGER = logging.getLogger(__name__)

# Key under which the per-request response future is stored on DispatchData.trigger_context.
# Per-request state must live on DispatchData (one per request), not on the shared decorator
# instance, so concurrent requests to the same webhook_id don't race over a single future.
_RESPONSE_FUTURE_KEY = "webhook_response_future"


class WebhookHandlerDecorator(WebhookBaseDecorator, CallResultHandlerDecorator):
    """Implementation for @webhook_handler (one handler per id; return value drives the response)."""

    name = "webhook_handler"

    # Exactly one handler per webhook_id (unlike webhook_trigger which allows many).
    webhook_id2handler: ClassVar[dict[str, WebhookHandlerDecorator]] = {}

    @staticmethod
    async def _handler(hass, webhook_id, request):
        handler = WebhookHandlerDecorator.webhook_id2handler.get(webhook_id)
        if handler is None:
            return None

        try:
            func_args = await WebhookHandlerDecorator.build_func_args(webhook_id, request)
        except ValueError:
            # The body could not be parsed (e.g. malformed JSON). Unlike @webhook_trigger,
            # which silently drops the event, a handler should tell the caller their request
            # was bad rather than returning a 200.
            _LOGGER.debug("webhook %s received an unparseable request body", webhook_id)
            return web.Response(status=HTTPStatus.BAD_REQUEST)

        if handler.has_expression():
            if not await handler.check_expression_vars(func_args):
                return None

        response_future: asyncio.Future[Any] = hass.loop.create_future()
        data = DispatchData(func_args, trigger_context={_RESPONSE_FUTURE_KEY: response_future})
        await handler.dispatch(data)

        result = await response_future
        return WebhookHandlerDecorator.coerce_response(result)

    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Resolve the per-request response future with the function's return value."""
        if data.trigger is not self:
            return
        response_future = data.trigger_context.get(_RESPONSE_FUTURE_KEY)
        if response_future is not None and not response_future.done():
            response_future.set_result(result)

    async def handle_call_exception(self, data: DispatchData, exc: Exception) -> None:
        """
        Resolve the per-request response future with a 500 on an unhandled exception.

        The exception is also logged via the manager's handle_exception; here we only
        ensure the awaiting request gets a 500 instead of falling back to a 200. We
        resolve with a Response (not set_exception) so the error does not propagate out
        of the aiohttp handler, where Home Assistant would turn it back into a 200.
        """
        if data.trigger is not self:
            return
        response_future = data.trigger_context.get(_RESPONSE_FUTURE_KEY)
        if response_future is not None and not response_future.done():
            response_future.set_result(web.Response(status=HTTPStatus.INTERNAL_SERVER_ERROR))

    @staticmethod
    def coerce_response(value: Any) -> web.Response | None:
        """Convert a webhook handler return value to an aiohttp Response."""
        if value is None:
            return None
        if isinstance(value, web.Response):
            return value
        # bool is a subclass of int; reject it so True/False don't become 1/0 status codes.
        if isinstance(value, int) and not isinstance(value, bool):
            if 100 <= value <= 599:
                return web.Response(status=value)
            _LOGGER.warning(
                "@webhook_handler function returned %s, which is not a valid HTTP status code (100-599)",
                value,
            )
            return None
        _LOGGER.warning(
            "@webhook_handler function returned unsupported type %s; "
            "expected int status code or aiohttp.web.Response",
            type(value).__name__,
        )
        return None

    @staticmethod
    def _add_handler(handler: WebhookHandlerDecorator) -> None:
        # Home Assistant's webhook.async_register raises if the webhook_id is already
        # registered (by another @webhook_handler, a @webhook_trigger, or any other
        # integration), so duplicates are rejected here without an extra pyscript check.
        webhook_id = handler.webhook_id
        WebhookHandlerDecorator.register_webhook(handler, WebhookHandlerDecorator._handler)
        WebhookHandlerDecorator.webhook_id2handler[webhook_id] = handler

    @staticmethod
    def _remove_handler(handler: WebhookHandlerDecorator) -> None:
        webhook_id = handler.webhook_id
        if WebhookHandlerDecorator.webhook_id2handler.get(webhook_id) is not handler:
            return
        webhook.async_unregister(handler.dm.hass, webhook_id)
        del WebhookHandlerDecorator.webhook_id2handler[webhook_id]

    async def start(self):
        """Start the webhook handler."""
        await super().start()
        self._add_handler(self)

        _LOGGER.debug("webhook handler %s listening on id %s", self.dm.name, self.webhook_id)

    async def stop(self):
        """Stop the webhook handler."""
        await super().stop()
        self._remove_handler(self)
