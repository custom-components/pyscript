"""Handles mqtt messages and notification."""

import json
import logging

from homeassistant.components import mqtt

from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".mqtt")


class Mqtt:
    """Define mqtt functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # notify message queues by mqtt message topic
    #
    notify = {}
    notify_remove = {}

    def __init__(self):
        """Warn on Mqtt instantiation."""
        _LOGGER.error("Mqtt class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize Mqtt."""

        cls.hass = hass

    @classmethod
    def mqtt_message_handler_maker(cls, subscribed_topic):
        """Closure for mqtt_message_handler."""

        async def mqtt_message_handler(mqttmsg):
            """Listen for MQTT messages."""
            func_args = {
                "trigger_type": "mqtt",
                "topic": mqttmsg.topic,
                "payload": mqttmsg.payload,
                "qos": mqttmsg.qos,
            }

            try:
                func_args["payload_obj"] = json.loads(mqttmsg.payload)
            except ValueError:
                pass

            await cls.update(subscribed_topic, func_args)

        return mqtt_message_handler

    @classmethod
    async def notify_add(cls, topic, queue):
        """Register to notify for mqtt messages of given topic to be sent to queue."""

        if topic not in cls.notify:
            cls.notify[topic] = set()
            _LOGGER.debug("mqtt.notify_add(%s) -> adding mqtt subscription", topic)
            cls.notify_remove[topic] = await mqtt.async_subscribe(
                cls.hass, topic, cls.mqtt_message_handler_maker(topic), encoding="utf-8", qos=0
            )
        cls.notify[topic].add(queue)

    @classmethod
    def notify_del(cls, topic, queue):
        """Unregister to notify for mqtt messages of given topic for given queue."""

        if topic not in cls.notify or queue not in cls.notify[topic]:
            return
        cls.notify[topic].discard(queue)
        if len(cls.notify[topic]) == 0:
            cls.notify_remove[topic]()
            _LOGGER.debug("mqtt.notify_del(%s) -> removing mqtt subscription", topic)
            del cls.notify[topic]
            del cls.notify_remove[topic]

    @classmethod
    async def update(cls, topic, func_args):
        """Deliver all notifications for an mqtt message on the given topic."""

        _LOGGER.debug("mqtt.update(%s, %s, %s)", topic, vars, func_args)
        if topic in cls.notify:
            for queue in cls.notify[topic]:
                await queue.put(["mqtt", func_args.copy()])
