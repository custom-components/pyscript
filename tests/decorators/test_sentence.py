"""Test pyscript @sentence_trigger decorator."""

from dataclasses import dataclass, field
import logging
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.pyscript.decorators import sentence as sentence_decorator
from homeassistant.components.conversation import trigger as conversation_trigger


@dataclass
class MockEntity:
    """Minimal hassil entity match."""

    text: str
    value: str | Any


@dataclass
class MockRecognizeResult:
    """Minimal hassil RecognizeResult stand-in."""

    entities: dict[str, MockEntity] = field(default_factory=dict)


@dataclass
class MockConversationInput:
    """Minimal ConversationInput stand-in."""

    text: str = ""
    device_id: str | None = None
    satellite_id: str | None = None


class MockAgentManager:
    """Stand-in for the conversation AgentManager that captures trigger registrations."""

    def __init__(self) -> None:
        """Initialize an empty trigger registry."""
        self.triggers: list[dict] = []
        self._counter = 0

    def register_trigger(self, sentences: list[str], trigger_callback: Any) -> Any:
        """Register a trigger and return its removal callback."""
        entry = {"sentences": sentences, "callback": trigger_callback, "id": self._counter}
        self._counter += 1
        self.triggers.append(entry)

        def unregister():
            self.triggers.remove(entry)

        return unregister


@pytest.fixture
def agent_manager():
    """Provide a mock agent manager that intercepts register_trigger calls."""
    mgr = MockAgentManager()
    with patch(
        "homeassistant.components.conversation.agent_manager.get_agent_manager",
        return_value=mgr,
    ):
        yield mgr


@pytest.mark.asyncio
async def test_sentence_trigger_basic(pyscript, agent_manager):
    """A matched sentence fires the function with slots."""
    await pyscript.start("""
@sentence_trigger("turn on {name}")
def voice_on(trigger_type, sentence, slots):
    pyscript.done = [trigger_type, sentence, slots]
""")

    assert len(agent_manager.triggers) == 1
    assert agent_manager.triggers[0]["sentences"] == ["turn on {name}"]

    user_input = MockConversationInput(text="turn on lights", device_id="dev1", satellite_id="sat1")
    result = MockRecognizeResult(entities={"name": MockEntity(text="lights", value="lights")})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response is None  # no return -> None spoken response

    await pyscript.wait_done(["sentence", "turn on lights", {"name": "lights"}])


@pytest.mark.asyncio
async def test_sentence_trigger_return_response(pyscript, agent_manager):
    """The function's return value becomes the spoken response."""
    await pyscript.start("""
@sentence_trigger("what is {thing}")
def voice_what(slots):
    return f"The {slots['thing']} is great"
""")

    user_input = MockConversationInput(text="what is weather")
    result = MockRecognizeResult(entities={"thing": MockEntity(text="weather", value="weather")})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response == "The weather is great"


@pytest.mark.asyncio
async def test_sentence_trigger_multiple_sentences(pyscript, agent_manager):
    """A list of sentences registers all of them."""
    await pyscript.start("""
@sentence_trigger(["what time is it", "tell me the time"])
def voice_time():
    return "noon"
""")

    assert len(agent_manager.triggers) == 1
    assert agent_manager.triggers[0]["sentences"] == ["what time is it", "tell me the time"]

    user_input = MockConversationInput(text="what time is it")
    result = MockRecognizeResult(entities={})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response == "noon"


@pytest.mark.asyncio
async def test_sentence_trigger_positional_and_list_sentences(pyscript, agent_manager):
    """Separate positional arguments and lists are flattened."""
    await pyscript.start("""
@sentence_trigger("hello", ["hi", "hey"])
def voice_greetings():
    pass
""")

    assert agent_manager.triggers[0]["sentences"] == ["hello", "hi", "hey"]


@pytest.mark.asyncio
async def test_sentence_trigger_apostrophe(pyscript, agent_manager):
    """An apostrophe is valid sentence punctuation."""
    await pyscript.start("""
@sentence_trigger("It's party time")
def voice_party():
    pass
""")

    assert agent_manager.triggers[0]["sentences"] == ["It's party time"]


@pytest.mark.parametrize(
    ("sentence", "error"),
    [
        ("hello?", "sentence should not contain punctuation"),
        ("hello!", "sentence should not contain punctuation"),
        ("4 a.m.", "sentence should not contain punctuation"),
        ([], "at least one sentence is required"),
        ("", "sentence too short"),
    ],
)
@pytest.mark.asyncio
async def test_sentence_trigger_invalid_logs(pyscript, agent_manager, caplog, sentence, error):
    """Invalid sentences are rejected and logged."""
    with caplog.at_level(logging.ERROR):
        await pyscript.start(f"""
@sentence_trigger({sentence!r})
def voice_invalid():
    pass
""")
        await pyscript.wait_exception(TypeError, match=error)

    assert not agent_manager.triggers
    assert error in caplog.text


@pytest.mark.asyncio
async def test_sentence_trigger_requires_sentence(pyscript, agent_manager):
    """At least one positional sentence is required."""
    await pyscript.start("""
@sentence_trigger()
def voice_empty():
    pass
""")
    await pyscript.wait_exception(TypeError, match="at least one sentence is required")

    assert not agent_manager.triggers


@pytest.mark.asyncio
async def test_sentence_trigger_old_ha_validation(pyscript, agent_manager, monkeypatch):
    """Older HA versions use the sentence validators they provide."""
    monkeypatch.delattr(conversation_trigger, "is_valid_sentence", raising=False)

    await pyscript.start("""
@sentence_trigger("play something")
def voice_old_ha():
    pass
""")

    assert agent_manager.triggers[0]["sentences"] == ["play something"]


@pytest.mark.asyncio
async def test_sentence_trigger_without_conversation_validation(pyscript, agent_manager, monkeypatch):
    """Missing conversation dependencies do not prevent pyscript from loading."""
    monkeypatch.setattr(sentence_decorator, "_import_validators", lambda: None)

    await pyscript.start("""
@sentence_trigger("hello?")
def voice_without_conversation():
    pass
""")

    assert agent_manager.triggers[0]["sentences"] == ["hello?"]


@pytest.mark.asyncio
async def test_sentence_trigger_device_and_satellite(pyscript, agent_manager):
    """device_id and satellite_id are passed through."""
    await pyscript.start("""
@sentence_trigger("hello")
def voice_hello(device_id, satellite_id):
    pyscript.done = [device_id, satellite_id]
""")

    user_input = MockConversationInput(
        text="hello", device_id="dev_abc", satellite_id="assist_satellite.kitchen"
    )
    result = MockRecognizeResult(entities={})

    await agent_manager.triggers[0]["callback"](user_input, result)
    await pyscript.wait_done(["dev_abc", "assist_satellite.kitchen"])


@pytest.mark.asyncio
async def test_sentence_trigger_details(pyscript, agent_manager):
    """The details dict contains full hassil match info."""
    await pyscript.start("""
@sentence_trigger("set {name} to {level}")
def voice_set(details):
    pyscript.done = details
""")

    user_input = MockConversationInput(text="set brightness to 50")
    result = MockRecognizeResult(
        entities={
            "name": MockEntity(text=" brightness ", value="brightness"),
            "level": MockEntity(text=" 50 ", value=50),
        }
    )

    await agent_manager.triggers[0]["callback"](user_input, result)
    await pyscript.wait_done(
        {
            "name": {"name": "name", "text": "brightness", "value": "brightness"},
            "level": {"name": "level", "text": "50", "value": 50},
        }
    )


@pytest.mark.asyncio
async def test_sentence_trigger_none_return(pyscript, agent_manager):
    """Explicit None return gives no spoken response."""
    await pyscript.start("""
@sentence_trigger("do something")
def voice_do():
    pyscript.done = "ran"
    return None
""")

    user_input = MockConversationInput(text="do something")
    result = MockRecognizeResult(entities={})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response is None
    await pyscript.wait_done("ran")


@pytest.mark.parametrize("expected_lingering_tasks", [True])
@pytest.mark.asyncio
async def test_sentence_trigger_timeout(pyscript, agent_manager):
    """A function that doesn't finish in time returns None (no spoken response)."""
    await pyscript.start("""
@sentence_trigger("slow thing", timeout=0.05)
def voice_slow():
    task.sleep(0.2)
    return "too late"
""")

    user_input = MockConversationInput(text="slow thing")
    result = MockRecognizeResult(entities={})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response is None


@pytest.mark.asyncio
async def test_sentence_trigger_exception_returns_none(pyscript, agent_manager):
    """An exception in the function yields None spoken response."""
    await pyscript.start("""
@sentence_trigger("crash")
def voice_crash():
    raise ValueError("boom")
""")

    user_input = MockConversationInput(text="crash")
    result = MockRecognizeResult(entities={})

    response = await agent_manager.triggers[0]["callback"](user_input, result)
    assert response is None
    await pyscript.wait_exception(ValueError, match="boom")


@pytest.mark.asyncio
async def test_sentence_trigger_unregisters_on_stop(pyscript, agent_manager):
    """Reloading unregisters the sentence from the agent manager."""
    await pyscript.start("""
@sentence_trigger("temp trigger")
def voice_temp():
    pass
""")

    assert len(agent_manager.triggers) == 1

    # Unload the integration to trigger stop()
    await pyscript.hass.config_entries.async_unload(
        pyscript.hass.config_entries.async_entries("pyscript")[0].entry_id
    )
    await pyscript.hass.async_block_till_done()

    assert len(agent_manager.triggers) == 0


@pytest.mark.asyncio
async def test_sentence_trigger_kwargs(pyscript, agent_manager):
    """Extra kwargs are merged into the function call."""
    await pyscript.start("""
@sentence_trigger("with extra", kwargs={"extra": 42})
def voice_extra(extra, trigger_type):
    pyscript.done = [extra, trigger_type]
""")

    user_input = MockConversationInput(text="with extra")
    result = MockRecognizeResult(entities={})

    await agent_manager.triggers[0]["callback"](user_input, result)
    await pyscript.wait_done([42, "sentence"])
