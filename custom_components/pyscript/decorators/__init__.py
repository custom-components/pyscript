from .event import EventTriggerDecorator
from .mqtt import MQTTTriggerDecorator
from .service import ServiceDecorator
from .state import StateTriggerDecorator, StateActiveDecorator
from .task import TaskUniqueDecorator
from .timing import TimeTriggerDecorator, TimeActiveDecorator
from .webhook import WebhookTriggerDecorator

DECORATORS = [
    StateTriggerDecorator,
    StateActiveDecorator,
    TimeTriggerDecorator,
    TimeActiveDecorator,
    TaskUniqueDecorator,
    EventTriggerDecorator,
    MQTTTriggerDecorator,
    WebhookTriggerDecorator,
    ServiceDecorator,
]
