"""Tests for pyscript config flow."""

import logging
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.pyscript import PYSCRIPT_SCHEMA, async_migrate_entry
from custom_components.pyscript.const import (
    CONF_ALLOW_ALL_IMPORTS,
    CONF_HASS_IS_GLOBAL,
    DOMAIN,
)
from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(name="pyscript_bypass_setup")
def pyscript_bypass_setup_fixture():
    """Mock component setup."""
    logging.getLogger("pytest_homeassistant.common").setLevel(logging.WARNING)
    with patch("custom_components.pyscript.async_setup_entry", return_value=True):
        yield


@pytest.mark.asyncio
async def test_user_flow_minimum_fields(hass, pyscript_bypass_setup):
    """Test user config flow with minimum fields."""
    # test form shows
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_ALLOW_ALL_IMPORTS in result["data"]
    assert CONF_HASS_IS_GLOBAL in result["data"]
    assert not result["data"][CONF_ALLOW_ALL_IMPORTS]
    assert not result["data"][CONF_HASS_IS_GLOBAL]


@pytest.mark.asyncio
async def test_user_flow_all_fields(hass, pyscript_bypass_setup):
    """Test user config flow with all fields."""
    # test form shows
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOW_ALL_IMPORTS: True,
            CONF_HASS_IS_GLOBAL: True,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_ALLOW_ALL_IMPORTS in result["data"]
    assert result["data"][CONF_ALLOW_ALL_IMPORTS]
    assert result["data"][CONF_HASS_IS_GLOBAL]


@pytest.mark.asyncio
async def test_user_already_configured(hass, pyscript_bypass_setup):
    """Test service is already configured during user setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_ALLOW_ALL_IMPORTS: True,
            CONF_HASS_IS_GLOBAL: True,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_ALLOW_ALL_IMPORTS: True,
            CONF_HASS_IS_GLOBAL: True,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


@pytest.mark.asyncio
async def test_import_flow(hass, pyscript_bypass_setup):
    """Test import config flow works."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_import_flow_update_allow_all_imports(hass, pyscript_bypass_setup):
    """Test import config flow updates existing entry when `allow_all_imports` has changed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_ALLOW_ALL_IMPORTS: True,
            CONF_HASS_IS_GLOBAL: True,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "updated_entry"


@pytest.mark.asyncio
async def test_import_flow_update_apps_from_none(hass, pyscript_bypass_setup):
    """Test import config flow updates existing entry when `apps` has changed from None to something."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={"apps": {"test_app": {"param": 1}}}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "updated_entry"


@pytest.mark.asyncio
async def test_import_flow_update_apps_to_none(hass, pyscript_bypass_setup):
    """Test import config flow updates existing entry when `apps` has changed from something to None."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({"apps": {"test_app": {"param": 1}}})
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data={})

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "updated_entry"


@pytest.mark.asyncio
async def test_import_flow_no_update(hass, pyscript_bypass_setup):
    """Test import config flow doesn't update existing entry when data is same."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=PYSCRIPT_SCHEMA({})
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_import_flow_update_user(hass, pyscript_bypass_setup):
    """Test import config flow update excludes `allow_all_imports` from being updated when updated entry was a user entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data=PYSCRIPT_SCHEMA(
            {
                CONF_ALLOW_ALL_IMPORTS: True,
                CONF_HASS_IS_GLOBAL: True,
            }
        ),
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={"apps": {"test_app": {"param": 1}}}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "updated_entry"

    assert hass.config_entries.async_entries(DOMAIN)[0].data == {
        CONF_ALLOW_ALL_IMPORTS: True,
        CONF_HASS_IS_GLOBAL: True,
        "apps": {"test_app": {"param": 1}},
    }


@pytest.mark.asyncio
async def test_import_flow_update_import(hass, pyscript_bypass_setup):
    """Test import config flow update includes `allow_all_imports` in update when updated entry was imported entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data=PYSCRIPT_SCHEMA(
            {
                CONF_ALLOW_ALL_IMPORTS: True,
                CONF_HASS_IS_GLOBAL: True,
            }
        ),
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={"apps": {"test_app": {"param": 1}}}
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "updated_entry"

    assert hass.config_entries.async_entries(DOMAIN)[0].data == {"apps": {"test_app": {"param": 1}}}


@pytest.mark.asyncio
async def test_options_flow_import(hass, pyscript_bypass_setup):
    """Test options flow aborts because configuration needs to be managed via configuration.yaml."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data=PYSCRIPT_SCHEMA(
            {
                CONF_ALLOW_ALL_IMPORTS: True,
                CONF_HASS_IS_GLOBAL: True,
            }
        ),
    )
    await hass.async_block_till_done()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = result["result"]

    result = await hass.config_entries.options.async_init(entry.entry_id, data=None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "no_ui_configuration_allowed"

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=None)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == ""


@pytest.mark.asyncio
async def test_options_flow_user_change(hass, pyscript_bypass_setup):
    """Test options flow updates config entry when options change."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data=PYSCRIPT_SCHEMA(
            {
                CONF_ALLOW_ALL_IMPORTS: True,
                CONF_HASS_IS_GLOBAL: True,
            }
        ),
    )
    await hass.async_block_till_done()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = result["result"]

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOW_ALL_IMPORTS: False,
            CONF_HASS_IS_GLOBAL: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == ""

    assert entry.data[CONF_ALLOW_ALL_IMPORTS] is False
    assert entry.data[CONF_HASS_IS_GLOBAL] is False


@pytest.mark.asyncio
async def test_options_flow_user_no_change(hass, pyscript_bypass_setup):
    """Test options flow aborts when options don't change."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data=PYSCRIPT_SCHEMA(
            {
                CONF_ALLOW_ALL_IMPORTS: True,
                CONF_HASS_IS_GLOBAL: True,
            }
        ),
    )
    await hass.async_block_till_done()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = result["result"]

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOW_ALL_IMPORTS: True,
            CONF_HASS_IS_GLOBAL: True,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "no_update"

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=None)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == ""


@pytest.mark.asyncio
async def test_config_entry_reload(hass):
    """Test that config entry reload does not duplicate listeners."""
    with (
        patch("homeassistant.config.load_yaml_config_file", return_value={}),
        patch("custom_components.pyscript.watchdog_start", return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=PYSCRIPT_SCHEMA(
                {
                    CONF_ALLOW_ALL_IMPORTS: True,
                    CONF_HASS_IS_GLOBAL: True,
                }
            ),
        )
        await hass.async_block_till_done()
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        entry = result["result"]
        listeners = hass.bus.async_listeners()
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert listeners == hass.bus.async_listeners()


def test_schema_legacy_decorators_removed(caplog):
    """The removed legacy_decorators option is dropped from the config and reported."""
    config = PYSCRIPT_SCHEMA({CONF_ALLOW_ALL_IMPORTS: True, "legacy_decorators": True})

    assert "legacy_decorators" not in config
    assert config[CONF_ALLOW_ALL_IMPORTS]
    assert "The 'legacy_decorators' option has been removed" in caplog.text


@pytest.mark.asyncio
async def test_migrate_entry_drops_legacy_decorators(hass):
    """Migrating a 1.1 entry drops the removed legacy_decorators option and keeps the rest."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        data={CONF_ALLOW_ALL_IMPORTS: True, "legacy_decorators": True},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.data == {CONF_ALLOW_ALL_IMPORTS: True}
    assert entry.minor_version == 2

    entry = MockConfigEntry(domain=DOMAIN, version=2, minor_version=1, data={})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)
