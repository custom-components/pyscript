"""Sentence trigger decorator."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers import config_validation as cv

from ..decorator_abc import DispatchData, TriggerDecorator
from .base import AutoKwargsDecorator, RequestResponseDecorator

if TYPE_CHECKING:
    from homeassistant.components.conversation import ConversationInput, RecognizeResult

_LOGGER = logging.getLogger(__name__)

Validator = Callable[[list[str]], list[str]]


def _import_validators() -> list[Validator] | None:
    """
    Import the sentence validators supported by HA.

    Delay imports to avoid breaking installs that haven't added the conversation component yet.
    """
    try:
        from homeassistant.components.conversation import trigger as conversation_trigger
    except ImportError:
        return None

    validators = [
        conversation_trigger.has_one_non_empty_item,
        conversation_trigger.has_no_punctuation,
    ]

    # is_valid_sentence is available in HA 2026.7 and later.
    if is_valid_sentence := getattr(conversation_trigger, "is_valid_sentence", None):
        validators.append(is_valid_sentence)

    return validators


def _validate_sentences(sentences: list[str]) -> list[str]:
    """Run HA's sentence validators when the conversation component is available."""
    if validators := _import_validators():
        return vol.All(*validators)(sentences)
    return sentences


def _flatten_sentence_args(args: list[Any]) -> list[Any]:
    """Flatten string and list positional arguments into one sentence list."""
    return [sentence for arg in args for sentence in cv.ensure_list(arg)]


class SentenceTriggerDecorator(TriggerDecorator, AutoKwargsDecorator, RequestResponseDecorator):
    """
    Implementation for @sentence_trigger.

    Registers sentences with HA's conversation agent manager.  The decorated
    function's string return value becomes the spoken response.
    """

    name = "sentence_trigger"
    args_schema = vol.Schema(
        vol.All(
            _flatten_sentence_args,
            [cv.string],
            vol.Length(min=1, msg="at least one sentence is required"),
            _validate_sentences,
        )
    )
    kwargs_schema = vol.Schema(
        {
            vol.Optional("timeout", default=3.0): vol.All(
                vol.Coerce(float), vol.Range(min=0, min_included=False)
            ),
        }
    )

    sentences: list[str]
    timeout: float
    _unregister: CALLBACK_TYPE | None = None

    async def validate(self):
        """Validate the sentence trigger configuration."""
        await super().validate()
        self.sentences = self.args

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
            "details": details,
            "device_id": user_input.device_id,
            "satellite_id": user_input.satellite_id,
        }
        func_args.update({n: d["value"] for n, d in details.items()})

        try:
            response = await self.handle_request(func_args, timeout=self.timeout)
        except TimeoutError:
            _LOGGER.warning(
                "sentence_trigger %s timed out after %ss",
                self.dm.name,
                self.timeout,
            )
            return None

        if response is None or isinstance(response, str):
            return response
        _LOGGER.warning(
            "sentence_trigger %s returned unsupported type %s", self.dm.name, type(response).__name__
        )
        return None

    async def dispatch_request(self, data: DispatchData) -> None:
        """Start the call through the trigger handlers."""
        await self.dispatch(data)

    async def start(self):
        """Register sentences with the conversation agent manager."""
        await super().start()
        from homeassistant.components.conversation.agent_manager import get_agent_manager

        mgr = get_agent_manager(self.dm.hass)
        self._unregister = mgr.register_trigger(
            sentences=self.sentences,
            trigger_callback=self._trigger_callback,
        )
        _LOGGER.debug("sentence_trigger %s registered sentences: %s", self.dm.name, self.sentences)

    async def stop(self):
        """Unregister sentences from the conversation agent manager."""
        await super().stop()
        if self._unregister:
            self._unregister()
            self._unregister = None
