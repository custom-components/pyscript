"""Time decorators."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import voluptuous as vol

from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .. import trigger
from ..decorator import WaitUntilDecoratorManager
from ..decorator_abc import DecoratorManagerStatus, DispatchData, TriggerDecorator, TriggerHandlerDecorator
from .base import AutoKwargsDecorator

_LOGGER = logging.getLogger(__name__)


def dt_now():
    """Return current time."""
    # Keep this wrapper so tests can patch it during the transition.
    return trigger.dt_now()


def dt_utcnow():
    """Return current UTC time."""
    return dt_util.utcnow()


class TimeActiveDecorator(TriggerHandlerDecorator, AutoKwargsDecorator):
    """Implementation for @time_active."""

    name = "time_active"
    args_schema = vol.Schema(vol.All([vol.Coerce(str)], vol.Length(min=0)))
    kwargs_schema = vol.Schema(
        {
            vol.Optional("hold_off", default=0.0): vol.Any(None, cv.positive_float),
            vol.Optional("hold_off_send_last", default=False): cv.boolean,
        }
    )

    hold_off: float | None
    hold_off_send_last: bool

    last_trig_time: float = 0.0
    _hold_off_task: asyncio.Task | None = None
    _pending_data: DispatchData | None = None

    async def _dispatch_after_hold_off(self) -> None:
        """Dispatch the latest suppressed payload after the current hold-off window."""
        while self._pending_data is not None:
            delay = self.last_trig_time + self.hold_off - time.monotonic()
            if delay > 0.0:
                # A trigger may pass and clear _pending_data while we sleep;
                # re-check the loop condition after every await.
                await asyncio.sleep(delay)
                continue

            data = self._pending_data
            _LOGGER.debug("%s hold_off_send_last dispatching %s", self, data)
            await self.dm.dispatch(data)
            if self._pending_data is data:
                self._pending_data = None

    async def handle_dispatch(self, data: DispatchData) -> bool:
        """Handle dispatch."""
        if self.last_trig_time > 0.0 and self.hold_off is not None and self.hold_off > 0.0:
            if time.monotonic() - self.last_trig_time < self.hold_off:
                if self.hold_off_send_last:
                    self._pending_data = data
                    if self._hold_off_task is None or self._hold_off_task.done():
                        self._hold_off_task = self.dm.hass.async_create_background_task(
                            self._dispatch_after_hold_off(), f"{self} hold_off_send_last"
                        )
                return False

        if data is self._pending_data:
            self.last_trig_time = time.monotonic()
            return True

        if len(self.args) > 0:
            if "trigger_time" in data.func_args and isinstance(data.func_args["trigger_time"], dt.datetime):
                now = data.func_args["trigger_time"]
            else:
                now = dt_now()

            for time_spec in self.args:
                _LOGGER.debug("time_spec %s, %s", time_spec, self)
                _LOGGER.debug("time_active now %s, %s", now, self)
                if await trigger.TrigTime.timer_active_check(time_spec, now, self.dm.startup_time):
                    self.last_trig_time = time.monotonic()
                    if data is not self._pending_data:
                        self._pending_data = None
                    return True
            return False

        self.last_trig_time = time.monotonic()
        if data is not self._pending_data:
            self._pending_data = None
        return True

    async def stop(self) -> None:
        """Stop pending hold-off dispatch."""
        await super().stop()
        self._pending_data = None
        if self._hold_off_task is not None:
            self._hold_off_task.cancel()


class TimeTriggerDecorator(TriggerDecorator):
    """Implementation for @time_trigger."""

    name = "time_trigger"
    # args_schema = vol.Schema(vol.All([vol.Coerce(str)], vol.Length(min=0)))
    args_schema = vol.Schema(
        vol.All(
            vol.Length(min=0),
            vol.All(
                [str], msg="argument 2 should be a string"
            ),  # Keep this wording for transition compatibility.
        )
    )

    run_on_startup: bool = False
    run_on_shutdown: bool = False
    timespec: list[str]
    _cycle_task: asyncio.Task = None

    async def validate(self) -> None:
        """Validate the decorator arguments."""
        await super().validate()
        self.timespec = self.args

        if len(self.timespec) == 0:
            self.run_on_startup = True
            return

        while "startup" in self.timespec:
            self.run_on_startup = True
            self.timespec.remove("startup")
        while "shutdown" in self.timespec:
            self.run_on_shutdown = True
            self.timespec.remove("shutdown")

    async def _cycle(self):
        if self.run_on_startup:
            await self.dispatch(DispatchData({"trigger_type": "time", "trigger_time": "startup"}))

        first_run = True
        try:
            while self.dm.status is DecoratorManagerStatus.RUNNING:
                if first_run:
                    now = self.dm.startup_time
                    first_run = False
                else:
                    now = dt_now()

                _LOGGER.debug("time_trigger now %s", now)
                time_next, time_next_adj = await trigger.TrigTime.timer_trigger_next(
                    self.timespec, now, self.dm.startup_time
                )
                _LOGGER.debug(
                    "trigger %s time_next = %s, time_next_adj = %s, now = %s",
                    self.dm.name,
                    time_next,
                    time_next_adj,
                    now,
                )
                if time_next is None:
                    _LOGGER.debug("trigger %s finished", self.name)
                    if isinstance(self.dm, WaitUntilDecoratorManager):
                        await self.dispatch(DispatchData({"trigger_type": "none"}))
                    break

                # replace with homeassistant.helpers.event.async_track_point_in_utc_time?
                timeout = (time_next_adj - now).total_seconds()
                # Check the wait against UTC, not naive local time: time_next_adj already
                # accounts for a DST change, and local time repeats an hour when the
                # clocks go back, which would make us wait that hour again.
                deadline = dt_utcnow() + dt.timedelta(seconds=timeout)
                _LOGGER.debug("%s sleeping for %s seconds", self, timeout)
                await asyncio.sleep(timeout)
                _LOGGER.debug("%s finish sleeping for %s seconds", self, timeout)
                while True:
                    timeout = (deadline - dt_utcnow()).total_seconds()
                    if timeout <= 1e-6:
                        break
                    _LOGGER.debug("%s additional sleep for %s seconds", self, timeout)
                    await asyncio.sleep(timeout)

                await self.dispatch(DispatchData({"trigger_type": "time", "trigger_time": time_next}))
        except asyncio.CancelledError:
            raise

    async def stop(self):
        """Stop the trigger."""
        if self._cycle_task is not None:
            self._cycle_task.cancel()
        if self.run_on_shutdown:
            await self.dispatch(DispatchData({"trigger_type": "time", "trigger_time": "shutdown"}))

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.dm.logger.error("%s failed", self, exc_info=exc)

    async def start(self) -> None:
        """Start the decorator."""
        await super().start()
        self._cycle_task = self.dm.hass.async_create_background_task(self._cycle(), repr(self))
        self._cycle_task.add_done_callback(self._on_task_done)
