"""Tests for pyscript config flow."""
import logging

from custom_components.pyscript import PYSCRIPT_SCHEMA
from custom_components.pyscript.const import CONF_ALLOW_ALL_IMPORTS, DOMAIN
import pytest
from pytest_homeassistant.async_mock import patch

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(name="pyscript_bypass_setup", autouse=True)
def pyscript_bypass_setup_fixture():
    """Mock component setup."""
    with patch("custom_components.pyscript.async_setup_entry", return_value=True):
        yield


async def test_user_flow_minimum_fields(hass):
    """Test user config flow with minimum fields."""
    # test form shows
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert CONF_ALLOW_ALL_IMPORTS in result["data"]
    assert not result["data"][CONF_ALLOW_ALL_IMPORTS]


async def test_user_flow_all_fields(hass):
    """Test user config flow with all fields."""
    # test form shows
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ALLOW_ALL_IMPORTS: True}
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert CONF_ALLOW_ALL_IMPORTS in result["data"]
    assert result["data"][CONF_ALLOW_ALL_IMPORTS]


async def test_user_already_configured(hass):
    """Test service is already configured during user setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_ALLOW_ALL_IMPORTS: True}
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_ALLOW_ALL_IMPORTS: True}
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_import_flow(hass, pyscript_bypass_setup):
    """Test import config flow works."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY


async def test_import_flow_update_entry(hass):
    """Test import config flow updates existing entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_ALLOW_ALL_IMPORTS: True}
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_ABORT
    assert result["reason"] == "updated_entry"


async def test_import_flow_no_update(hass):
    """Test import config flow doesn't update existing entry when data is same."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_ABORT
    assert result["reason"] == "already_configured_service"
