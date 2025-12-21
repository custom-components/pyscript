"""Trigger decorator implementations."""

from __future__ import annotations

import json
import logging

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.core import CALLBACK_TYPE

from .base import ExpressionDecorator, AutoKwargsDecorator
from ..decorator_abc import DispatchData, TriggerDecorator

_LOGGER = logging.getLogger(__name__)


class MQTTTriggerDecorator(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator):
    """Implementation for @mqtt_trigger."""

    name = "mqtt_trigger"
    args_schema = vol.Schema(vol.All([vol.Coerce(str)], vol.Length(min=1, max=2)))
    kwargs_schema = vol.Schema({vol.Optional("encoding", default="utf-8"): str})

    encoding: str

    remove_listener_callback: CALLBACK_TYPE | None = None

    async def validate(self) -> None:
        """Validate the MQTT trigger."""
        await super().validate()
        if len(self.args) == 2:
            self.create_expression(self.args[1])

    async def _mqtt_message_handler(self, mqttmsg: mqtt.ReceiveMessage) -> None:
        func_args = {
            "trigger_type": "mqtt",
            "topic": mqttmsg.topic,
            "payload": mqttmsg.payload,
            "qos": mqttmsg.qos,
            "retain": mqttmsg.retain,
        }
        try:
            func_args["payload_obj"] = json.loads(mqttmsg.payload)
        except ValueError:
            pass
        if self.has_expression():
            if not await self.check_expression_vars(func_args):
                return
        await self.dispatch(DispatchData(func_args))

    async def start(self) -> None:
        """Start the MQTT trigger."""
        await super().start()
        topic = self.args[0]
        self.remove_listener_callback = await mqtt.async_subscribe(
            self.dm.hass,
            topic,
            self._mqtt_message_handler,
            encoding=self.encoding,
            qos=0,
        )

    async def stop(self) -> None:
        """Stop the MQTT trigger."""
        await super().stop()
        if self.remove_listener_callback:
            self.remove_listener_callback()
