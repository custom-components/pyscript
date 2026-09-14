"""Pyscript decorators."""

from .event import EventTriggerDecorator
from .mqtt import MQTTTriggerDecorator
from .sentence import SentenceTriggerDecorator
from .service import ServiceDecorator
from .state import StateActiveDecorator, StateTriggerDecorator
from .task import TaskUniqueDecorator
from .timing import TimeActiveDecorator, TimeTriggerDecorator
from .webhook import WebhookHandlerDecorator, WebhookTriggerDecorator

DECORATORS = [
    StateTriggerDecorator,
    StateActiveDecorator,
    TimeTriggerDecorator,
    TimeActiveDecorator,
    TaskUniqueDecorator,
    EventTriggerDecorator,
    MQTTTriggerDecorator,
    SentenceTriggerDecorator,
    WebhookTriggerDecorator,
    WebhookHandlerDecorator,
    ServiceDecorator,
]
