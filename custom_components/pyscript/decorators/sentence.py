"""Sentence trigger decorator."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import CALLBACK_TYPE

from ..decorator_abc import CallResultHandlerDecorator, DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator

if TYPE_CHECKING:
    from homeassistant.components.conversation import ConversationInput, RecognizeResult

_LOGGER = logging.getLogger(__name__)

_SENTENCE_RESULT_FUTURE = "sentence_result_future"


class SentenceTriggerDecorator(TriggerDecorator, AutoKwargsDecorator, CallResultHandlerDecorator):
    """
    Implementation for @sentence_trigger.

    Registers sentences with HA's conversation agent manager.  The decorated
    function's return value (if not None) becomes the spoken response.
    """

    name = "sentence_trigger"
    args_schema = vol.Schema(
        vol.All(
            [vol.Any(str, [str])],
            vol.Length(min=1, max=1, msg="needs one argument (a sentence or list of sentences)"),
        )
    )
    kwargs_schema = vol.Schema(
        {
            vol.Optional("timeout", default=10.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        }
    )

    sentences: list[str]
    timeout: float
    _unregister: CALLBACK_TYPE | None

    async def validate(self):
        """Validate the sentence trigger configuration."""
        await super().validate()
        raw = self.args[0]
        self.sentences = raw if isinstance(raw, list) else [raw]
        self._unregister = None

    async def _trigger_callback(self, user_input: ConversationInput, result: RecognizeResult) -> str | None:
        """Handle a matched sentence from the conversation agent."""
        details = {
            entity_name: {
                "name": entity_name,
                "text": entity.text.strip() if isinstance(entity.text, str) else entity.text,
                "value": (entity.value.strip() if isinstance(entity.value, str) else entity.value),
            }
            for entity_name, entity in result.entities.items()
        }

        func_args: dict[str, Any] = {
            "trigger_type": "sentence",
            "sentence": user_input.text,
            "slots": {n: d["value"] for n, d in details.items()},
            "details": details,
            "device_id": user_input.device_id,
            "satellite_id": user_input.satellite_id,
        }

        future = self.dm.hass.loop.create_future()
        data = DispatchData(func_args, trigger_context={_SENTENCE_RESULT_FUTURE: future})
        await self.dispatch(data)

        try:
            response = await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError:
            _LOGGER.warning(
                "sentence_trigger %s timed out after %ss",
                self.dm.name,
                self.timeout,
            )
            return None

        return str(response) if response is not None else None

    async def handle_call_result(self, data: DispatchData, result: Any) -> None:
        """Forward the function return value as the spoken response."""
        if data.trigger is not self:
            return
        self._resolve_future(data, result)

    @staticmethod
    def _resolve_future(data: DispatchData, result: Any) -> None:
        future = data.trigger_context.get(_SENTENCE_RESULT_FUTURE)
        if future is not None and not future.done():
            future.set_result(result)

    async def start(self):
        """Register sentences with the conversation agent manager."""
        await super().start()
        try:
            from homeassistant.components.conversation.agent_manager import get_agent_manager

            mgr = get_agent_manager(self.dm.hass)
            self._unregister = mgr.register_trigger(
                sentences=self.sentences,
                trigger_callback=self._trigger_callback,
            )
        except Exception as err:
            _LOGGER.warning(
                "sentence_trigger %s failed to register; conversations unavailable: %s", self.dm.name, err
            )
            return
        _LOGGER.debug("sentence_trigger %s registered sentences: %s", self.dm.name, self.sentences)

    async def stop(self):
        """Unregister sentences from the conversation agent manager."""
        await super().stop()
        if self._unregister:
            self._unregister()
            self._unregister = None
