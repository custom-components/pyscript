"""Test: Global configuration for pytest."""

from ast import literal_eval
import asyncio
from collections.abc import Generator
from datetime import datetime
import glob
import re
from typing import Any
from unittest.mock import patch

from mock_open import MockOpen
import pytest

from custom_components.pyscript import trigger
from custom_components.pyscript.const import DOMAIN, FOLDER
from custom_components.pyscript.eval import AstEval
from custom_components.pyscript.function import Function
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

_UNSET = object()


async def _wait(queue: asyncio.Queue, expected: Any, timeout: float) -> Any:
    raw = await asyncio.wait_for(queue.get(), timeout=timeout)
    if expected is _UNSET:
        return raw
    actual = raw if isinstance(expected, str) else literal_eval(raw)
    assert actual == expected
    return actual


class PyscriptFixture:
    """
    Configurable pyscript bootstrap for tests.

    Attributes:
        files: Full path -> source content. Multiple entries enable
            pattern-based glob mocking for apps/modules/scripts layouts.
            Use `add_file` / `add_files` to populate.
        config: Config passed to `async_setup_component` (defaults to
            `{DOMAIN: {}}`).
        yaml_config: Value returned by mocked `load_yaml_config_file`
            (defaults to `{}`).
        now: A single datetime, or a list of datetimes returned successively
            as pyscript reads `dt_now`.
        done, done2: Queues fed by `pyscript.done` / `pyscript.done2` state
            writes from scripts. Consume via `wait_done` / `wait_done2`.
        exceptions: Queue of exceptions captured from `AstEval.log_exception`
            (i.e. any exception pyscript would otherwise only surface via the
            log). Consume via `wait_exception`.

    """

    DEFAULT_NOW = datetime(2020, 7, 1, 11, 59, 59, 999999)

    def __init__(self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch | None = None) -> None:
        """Initialize with defaults; mutate attributes before calling `start()`."""
        self.hass = hass
        self.monkeypatch = monkeypatch
        self.files: dict[str, str] = {}
        self.config: dict[str, Any] | None = None
        self.yaml_config: dict[str, Any] | None = None
        self.now: datetime | list[datetime] = self.DEFAULT_NOW
        self.done: asyncio.Queue = asyncio.Queue()
        self.done2: asyncio.Queue = asyncio.Queue()
        self.exceptions: asyncio.Queue = asyncio.Queue()

    def add_file(self, name: str, content: str) -> None:
        """Register a single script file under the pyscript folder (name is relative to FOLDER)."""
        self.files[f"{self.hass.config.path(FOLDER)}/{name}"] = content

    def add_files(self, files: dict[str, str]) -> None:
        """Register multiple script files under the pyscript folder (keys are relative to FOLDER)."""
        for name, content in files.items():
            self.add_file(name, content)

    async def start(self, source: str | None = None) -> None:
        """
        Load pyscript using the current attributes.

        If `source` is given, it's added as `hello.py` before loading — a
        shortcut for the common single-file test pattern.
        """
        if source is not None:
            self.add_file("hello.py", source)
        Function.hass = None

        if self.monkeypatch is not None:
            original_log_exception = AstEval.log_exception
            exceptions_queue = self.exceptions

            def capturing_log_exception(ast_self, exc):
                exceptions_queue.put_nowait(exc)
                original_log_exception(ast_self, exc)

            self.monkeypatch.setattr(AstEval, "log_exception", capturing_log_exception)

        config = self.config if self.config is not None else {DOMAIN: {}}
        yaml_config = self.yaml_config if self.yaml_config is not None else {}
        files_map = self.files
        now = self.now
        first_value = now[0] if isinstance(now, list) else now

        mock_open = MockOpen()
        for path, content in files_map.items():
            mock_open[path].read_data = content

        def isfile_side_effect(arg):
            return arg in files_map

        def glob_side_effect(path, recursive=False, root_dir=None, dir_fd=None, include_hidden=False):
            path_re = re.compile(glob.translate(path, recursive=recursive, include_hidden=include_hidden))
            return [this_path for this_path in files_map if path_re.match(this_path)]

        with (
            patch("custom_components.pyscript.os_path_isdir", return_value=True),
            patch("custom_components.pyscript.glob.iglob") as mock_glob,
            patch("custom_components.pyscript.global_ctx.open", mock_open),
            patch("custom_components.pyscript.open", mock_open),
            patch("custom_components.pyscript.trigger.dt_now", return_value=first_value),
            patch("homeassistant.config.load_yaml_config_file", return_value=yaml_config),
            patch("custom_components.pyscript.watchdog_start", return_value=None),
            patch("custom_components.pyscript.os.path.getmtime", return_value=1000),
            patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000),
            patch("custom_components.pyscript.install_requirements", return_value=None),
            patch("custom_components.pyscript.global_ctx.os_path_isfile") as mock_isfile,
        ):
            mock_isfile.side_effect = isfile_side_effect
            mock_glob.side_effect = glob_side_effect
            assert await async_setup_component(self.hass, "pyscript", config)

        def return_next_time():
            if isinstance(now, list):
                return now.pop(0) if len(now) > 1 else now[0]
            return now

        trigger.__dict__["dt_now"] = return_next_time

        async def state_changed(event):
            entity_id = event.data["entity_id"]
            if entity_id == "pyscript.done":
                await self.done.put(event.data["new_state"].state)
            elif entity_id == "pyscript.done2":
                await self.done2.put(event.data["new_state"].state)

        self.hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)

        self.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await self.hass.async_block_till_done()

    async def wait_done(self, expected: Any = _UNSET, *, timeout: float = 4) -> Any:
        """
        Await the next pyscript.done value.

        If `expected` is given, assert the received value matches and return it.
        Non-string expected are compared after `ast.literal_eval` (pyscript
        stores list/dict/number values as string representations in HA state).
        """
        return await _wait(self.done, expected, timeout)

    async def wait_done2(self, expected: Any = _UNSET, *, timeout: float = 4) -> Any:
        """Await the next pyscript.done2 value (see wait_done)."""
        return await _wait(self.done2, expected, timeout)

    async def wait_exception(
        self,
        expected_type: type[BaseException] | None = None,
        *,
        match: str | None = None,
        timeout: float = 4,
    ) -> BaseException:
        """
        Await the next exception captured from `AstEval.log_exception`.

        If `expected_type` is given, assert the captured exception is an
        instance of it. If `match` is given, assert it appears in `str(exc)`.
        """
        exc = await asyncio.wait_for(self.exceptions.get(), timeout=timeout)
        if expected_type is not None:
            assert isinstance(exc, expected_type), (
                f"expected {expected_type.__name__}, got {type(exc).__name__}: {exc}"
            )
        if match is not None:
            assert match in str(exc), f"expected {match!r} in {str(exc)!r}"
        return exc


def _drain(queue: asyncio.Queue) -> list[Any]:
    leftover = []
    while not queue.empty():
        leftover.append(queue.get_nowait())
    return leftover


@pytest.fixture
def pyscript(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> Generator[PyscriptFixture]:
    """
    Per-test pyscript fixture: configure attributes, then `await pyscript.start()`.

    On teardown, asserts that no ``pyscript.done`` / ``pyscript.done2`` value and
    no exception captured from ``AstEval.log_exception`` was left unconsumed.
    Tests that intentionally produce values or exceptions must drain them via
    ``wait_done`` / ``wait_done2`` / ``wait_exception``.
    """
    fixture = PyscriptFixture(hass, monkeypatch)
    yield fixture
    leftover_done = _drain(fixture.done)
    leftover_done2 = _drain(fixture.done2)
    leftover_exc = _drain(fixture.exceptions)
    assert not leftover_done, f"unconsumed pyscript.done values: {leftover_done}"
    assert not leftover_done2, f"unconsumed pyscript.done2 values: {leftover_done2}"
    assert not leftover_exc, f"unconsumed pyscript exceptions: {leftover_exc}"
