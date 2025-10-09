"""Test pyscripts test module."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from custom_components.pyscript.function import Function
from custom_components.pyscript.state import State, StateVal
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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


def test_state_val_conversions():
    """Test helper conversion methods exposed on StateVal."""
    float_state = StateVal(HassState("test.float", "123.45"))
    assert float_state.as_float() == pytest.approx(123.45)

    int_state = StateVal(HassState("test.int", "42"))
    assert int_state.as_int() == 42

    hex_state = StateVal(HassState("test.hex", "FF"))
    assert hex_state.as_int(base=16) == 255

    bool_state = StateVal(HassState("test.bool", "on"))
    assert bool_state.as_bool() is True

    round_state = StateVal(HassState("test.round", "3.1415"))
    assert round_state.as_round(precision=2) == pytest.approx(3.14)

    datetime_state = StateVal(HassState("test.datetime", "2024-03-05T06:07:08+00:00"))
    assert datetime_state.as_datetime() == datetime(2024, 3, 5, 6, 7, 8, tzinfo=timezone.utc)

    invalid_state = StateVal(HassState("test.invalid", "invalid"))
    with pytest.raises(ValueError):
        invalid_state.as_float()
    with pytest.raises(ValueError):
        invalid_state.as_int()
    with pytest.raises(ValueError):
        invalid_state.as_bool()
    with pytest.raises(ValueError):
        invalid_state.as_round()
    with pytest.raises(ValueError):
        invalid_state.as_datetime()

    assert invalid_state.as_bool(default=False) is False

    assert invalid_state.as_float(default=1.23) == pytest.approx(1.23)

    assert invalid_state.as_round(default=0) == 0

    fallback_datetime = datetime(1999, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert invalid_state.as_datetime(default=fallback_datetime) == fallback_datetime

    unknown_state = StateVal(HassState("test.unknown", STATE_UNKNOWN))
    assert unknown_state.is_unknown() is True
    assert unknown_state.is_unavailable() is False
    assert unknown_state.has_value() is False

    unavailable_state = StateVal(HassState("test.unavailable", STATE_UNAVAILABLE))
    assert unavailable_state.is_unavailable() is True
    assert unavailable_state.is_unknown() is False
    assert unavailable_state.has_value() is False

    standard_state = StateVal(HassState("test.standard", "ready"))
    assert standard_state.has_value() is True
