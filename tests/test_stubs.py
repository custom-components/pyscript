"""Tests for pyscript stub generation and stub imports."""

from __future__ import annotations

from datetime import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.pyscript.const import DOMAIN, FOLDER, SERVICE_GENERATE_STUBS

from tests.test_init import setup_script


@pytest.mark.asyncio
async def test_generate_stubs_service_writes_files(hass, caplog, monkeypatch):
    """Ensure the generate_stubs service writes expected files into modules/stubs."""

    # Set up pyscript so the service is registered.
    await setup_script(
        hass,
        notify_q=None,
        now=dt(2024, 1, 1, 0, 0, 0),
        source="""
@service
def ready():
    pass
""",
        script_name="/stub_service.py",
    )

    hass.states.async_set(
        "light.lamp",
        "on",
        {
            "valid_attr": 42,
            "invalid attr": "ignored",
        },
    )

    dummy_registry = SimpleNamespace(
        entities={
            "light.lamp": SimpleNamespace(entity_id="light.lamp", disabled=False),
        }
    )
    monkeypatch.setattr("custom_components.pyscript.stubs.generator.er.async_get", lambda _: dummy_registry)

    async def fake_service_descriptions(_hass) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "light": {
                "blink": {
                    "description": "Blink the light once.",
                    "target": {"entity": {"domain": "light"}},
                    "fields": {
                        "brightness": {
                            "required": True,
                            "selector": {"number": {}},
                            "description": "Brightness.",
                        },
                        "speed": {
                            "required": False,
                            "selector": {"select": {"options": ["slow", "fast"]}},
                            "description": "Blink speed.",
                        },
                        "invalid-field": {"required": True, "selector": {"boolean": None}},
                    },
                    "response": {"optional": True},
                }
            }
        }

    monkeypatch.setattr(
        "custom_components.pyscript.stubs.generator.async_get_all_descriptions", fake_service_descriptions
    )

    stubs_dir = Path(hass.config.path(FOLDER)) / "modules" / "stubs"
    builtins_target = stubs_dir / "pyscript_builtins.py"
    generated_target = stubs_dir / "pyscript_generated.py"

    if stubs_dir.exists():
        # Clean up artifacts from previous runs to avoid false positives.
        for child in stubs_dir.iterdir():
            child.unlink()
    else:
        stubs_dir.mkdir(parents=True, exist_ok=True)

    response: dict[str, Any] = await hass.services.async_call(
        DOMAIN,
        SERVICE_GENERATE_STUBS,
        {},
        blocking=True,
        return_response=True,
    )

    expected_ignored: list[str] = [
        "blink(invalid-field)",
        "light.lamp.invalid attr",
    ]
    assert response["ignored_identifiers"] == sorted(expected_ignored)
    assert response["result"] == "OK"
    assert builtins_target.exists()
    assert generated_target.exists()

    generated_content = generated_target.read_text(encoding="utf-8")
    assert "class light" in generated_content
    assert "class _light_state(StateVal)" in generated_content
    assert "lamp: _light_state" in generated_content
    assert "def blink(self, *, brightness: int, speed" in generated_content
    assert "def blink(*, entity_id: str, brightness: int, speed:" in generated_content
    assert "Blink the light once." in generated_content
    assert "Literal" in generated_content
    assert "'slow'" in generated_content
    assert "'fast'" in generated_content
    assert "-> dict[str, Any]" in generated_content

    original_builtins = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "pyscript"
        / "stubs"
        / "pyscript_builtins.py"
    )
    assert builtins_target.read_text(encoding="utf-8") == original_builtins.read_text(encoding="utf-8")

    # Clean up generated files so other tests start with a blank slate.
    generated_target.unlink()
    builtins_target.unlink()
    try:
        stubs_dir.rmdir()
    except OSError:
        # Directory contains other content; leave it in place.
        pass


@pytest.mark.asyncio
async def test_stub_imports_are_ignored(hass, caplog):
    """Verify importing from stubs.* does not raise even when the module is missing."""

    await setup_script(
        hass,
        notify_q=None,
        now=dt(2024, 2, 2, 0, 0, 0),
        source="""
from stubs import helper1
from stubs.fake_module import helper2
from stubs.fake_module.deep import helper3

@service
def stub_import_ready():
    log.info("stub import ready")
""",
        script_name="/stub_import.py",
    )

    assert hass.services.has_service(DOMAIN, "stub_import_ready")
    assert "ModuleNotFoundError" not in caplog.text
