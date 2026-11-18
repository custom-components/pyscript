"""Webhook decorator."""

from __future__ import annotations

from abc import ABC
import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
import logging
from typing import Any, ClassVar

from aiohttp import hdrs
from aiohttp.web import Request, Response
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.webhook import SUPPORTED_METHODS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.json import json_dumps

from ..decorator_abc import CallResultHandlerDecorator, DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator, ExpressionDecorator

_LOGGER = logging.getLogger(__name__)

_WEBHOOK_RESULT_FUTURE = "webhook_result_future"


class _WebhookDecoratorBase(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator, ABC):
    """Base class for @webhook_trigger and @webhook_handler."""

    args_schema = vol.Schema(
        vol.All(
            [vol.Coerce(str)],
            vol.Length(min=1, max=2, msg="needs at least one argument"),
        )
    )
    kwargs_schema = vol.Schema(
        {
            vol.Optional("local_only", default=True): cv.boolean,
            vol.Optional("methods", default={hdrs.METH_POST, hdrs.METH_PUT}): vol.All(
                vol.Coerce(list),
                [vol.In(SUPPORTED_METHODS)],
                vol.Length(min=1, msg="needs at least one HTTP method"),
                vol.Coerce(set),
            ),
        }
    )

    webhook_id: str
    local_only: bool
    methods: set[str]

    async def validate(self):
        """Validate the webhook configuration."""
        await super().validate()
        self.webhook_id = self.args[0]

        if len(self.args) == 2:
            self.create_expression(self.args[1])

    @staticmethod
    async def _build_func_args(webhook_id: str, request: Request) -> dict[str, Any]:
        """Build the standard webhook function kwargs from an incoming request."""
        if "json" in request.headers.get(hdrs.CONTENT_TYPE, ""):
            payload = await request.json()
        else:
            payload_multidict = await request.post()
            payload = {k: payload_multidict.getone(k) for k in payload_multidict.keys()}
        return {
            "trigger_type": "webhook",
            "webhook_id": webhook_id,
            "request": request,
            "payload": payload,
        }

    def _register_webhook(
        self,
        handler: Callable[[HomeAssistant, str, Request], Awaitable[Response | None]],
    ) -> None:
        """Register self.webhook_id with Home Assistant, dispatching to ``handler``."""
        webhook.async_register(
            self.dm.hass,
            "pyscript",  # DOMAIN
            "pyscript",  # NAME
            self.webhook_id,
            handler,
            local_only=self.local_only,
            allowed_methods=self.methods,
        )


class WebhookTriggerDecorator(_WebhookDecoratorBase):
    """Implementation for @webhook_trigger."""

    name = "webhook_trigger"

    webhook_id2triggers: ClassVar[dict[str, set[WebhookTriggerDecorator]]] = {}

    @staticmethod
    async def _handler(_hass: HomeAssistant, webhook_id: str, request: Request) -> None:
        func_args = await WebhookTriggerDecorator._build_func_args(webhook_id, request)

        for trigger in WebhookTriggerDecorator.webhook_id2triggers.get(webhook_id, set()).copy():
            trigger_args = func_args.copy()
            if trigger.has_expression():
                if not await trigger.check_expression_vars(trigger_args):
                    continue
            await trigger.dispatch(DispatchData(trigger_args))

    def _add_trigger(self) -> None:
        triggers = WebhookTriggerDecorator.webhook_id2triggers.get(self.webhook_id)
        if triggers is None:
            self._register_webhook(WebhookTriggerDecorator._handler)
            WebhookTriggerDecorator.webhook_id2triggers[self.webhook_id] = {self}
            return

        existing = next(iter(triggers))
        if existing.local_only != self.local_only or existing.methods != self.methods:
            raise ValueError(
                f"'{self.dm.func_name}' @webhook_trigger for '{self.webhook_id}' conflicts with existing "
                f"'{existing.dm.ast_ctx.get_global_ctx_name()}.{existing.dm.func_name}' "
                f"(local_only={existing.local_only}, methods={existing.methods})"
            )
        triggers.add(self)

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
        self._add_trigger()

        _LOGGER.debug("webhook trigger %s listening on id %s", self.dm.name, self.webhook_id)

    async def stop(self):
        """Stop the webhook trigger."""
        await super().stop()
        self._remove_trigger(self)


class WebhookHandlerDecorator(_WebhookDecoratorBase, CallResultHandlerDecorator):
    """
    Implementation for @webhook_handler.

    Like @webhook_trigger, but the function's return value becomes the HTTP
    response. Only one handler can be registered per webhook_id.
    """

    name = "webhook_handler"
    kwargs_schema = _WebhookDecoratorBase.kwargs_schema.extend(
        {vol.Optional("timeout", default=10.0): vol.All(vol.Coerce(float), vol.Range(min=0))}
    )

    timeout: float

    async def _to_response(self, result: Any) -> Response:
        """Convert a user-returned value into an aiohttp Response."""
        if result is None:
            return Response(status=HTTPStatus.OK)
        if isinstance(result, Response):
            return result
        if isinstance(result, str):
            return Response(text=result)
        if isinstance(result, bytes):
            return Response(body=result)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
            status, body = result
            response = await self._to_response(body)
            response.set_status(status)
            return response
        if isinstance(result, (dict, list)):
            try:
                body = json_dumps(result)
            except (TypeError, ValueError) as exc:
                await self.dm.handle_exception(exc)
                return Response(status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return Response(text=body, content_type="application/json")
        _LOGGER.warning("@webhook_handler returned unsupported type %s", type(result).__name__)
        return Response(status=HTTPStatus.INTERNAL_SERVER_ERROR)

    async def _handler(self, _hass: HomeAssistant, webhook_id: str, request: Request) -> Response:
        try:
            func_args = await self._build_func_args(webhook_id, request)
        except ValueError:
            # Body could not be parsed (e.g. malformed JSON). Tell the caller their
            # request was bad rather than silently returning 200 like webhook_trigger.
            # the webhook id can be secret, so name the function instead
            _LOGGER.warning("webhook_handler %s received an unparsable request body", self.dm.name)
            return Response(status=HTTPStatus.BAD_REQUEST)

        if self.has_expression() and not await self.check_expression_vars(func_args):
            return Response(status=HTTPStatus.FORBIDDEN)

        future = self.dm.hass.loop.create_future()
        data = DispatchData(func_args, trigger_context={_WEBHOOK_RESULT_FUTURE: future})
        await self.dispatch(data)

        try:
            result = await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError:
            _LOGGER.warning(
                "webhook_handler %s timed out after %ss",
                self.dm.name,
                self.timeout,
            )
            return Response(status=HTTPStatus.GATEWAY_TIMEOUT)

        try:
            return await self._to_response(result)
        except Exception as exc:
            await self.dm.handle_exception(exc)
            return Response(status=HTTPStatus.INTERNAL_SERVER_ERROR)

    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Forward the function result to the awaiting webhook request."""
        if data.trigger is not self:
            return
        self._resolve_future(data, result)

    async def handle_call_exception(self, data: DispatchData, exc: Exception) -> None:
        """Return a 500 response when the user function raised."""
        if data.trigger is not self:
            return
        self._resolve_future(data, Response(status=HTTPStatus.INTERNAL_SERVER_ERROR))

    async def handle_call_canceled(self, data: DispatchData) -> None:
        """Return a 503 response when the call was canceled by a guard (e.g. @task_unique, @state_active)."""
        if data.trigger is not self:
            return
        self._resolve_future(data, Response(status=HTTPStatus.SERVICE_UNAVAILABLE))

    @staticmethod
    def _resolve_future(data: DispatchData, result: Any) -> None:
        future = data.trigger_context.get(_WEBHOOK_RESULT_FUTURE)
        if future is not None and not future.done():
            future.set_result(result)

    async def start(self):
        """Start the webhook handler."""
        await super().start()
        self._register_webhook(self._handler)

        _LOGGER.debug("webhook handler %s listening on id %s", self.dm.name, self.webhook_id)

    async def stop(self):
        """Stop the webhook handler."""
        await super().stop()
        webhook.async_unregister(self.dm.hass, self.webhook_id)
