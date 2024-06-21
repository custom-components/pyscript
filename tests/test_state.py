"""Test pyscripts test module."""

from unittest.mock import patch

import pytest

from custom_components.pyscript.function import Function
from custom_components.pyscript.state import State
from homeassistant.core import Context, ServiceRegistry, StateMachine
from homeassistant.helpers.state import State as HassState


@pytest.mark.asyncio
async def test_service_call(hass):
    """Test calling a service using the entity_id as a property."""
    with patch(
        "custom_components.pyscript.state.async_get_all_descriptions",
        return_value={
            "test": {
                "test": {"description": None, "fields": {"entity_id": "blah", "other_service_data": "blah"}}
            }
        },
    ), patch.object(StateMachine, "get", return_value=HassState("test.entity", "True")), patch.object(
        ServiceRegistry, "async_call"
    ) as call:
        State.init(hass)
        Function.init(hass)
        await State.get_service_params()

        func = State.get("test.entity.test")
        await func(context=Context(id="test"), blocking=True, limit=1, other_service_data="test")
        assert call.called
        assert call.call_args[0] == (
            "test",
            "test",
            {"other_service_data": "test", "entity_id": "test.entity"},
        )
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": True, "limit": 1}
        call.reset_mock()

        func = State.get("test.entity.test")
        await func(context=Context(id="test"), blocking=False, other_service_data="test")
        assert call.called
        assert call.call_args[0] == (
            "test",
            "test",
            {"other_service_data": "test", "entity_id": "test.entity"},
        )
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": False}

        # Stop all tasks to avoid conflicts with other tests
        await Function.waiter_stop()
        await Function.reaper_stop()
