from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .base import AutoKwargsDecorator
from .. import trigger
from ..decorator import WaitUntilDecoratorManager
from ..decorator_abc import DispatchData, TriggerHandlerDecorator, TriggerDecorator, DecoratorManagerStatus

_LOGGER = logging.getLogger(__name__)


def dt_now():
    """Return current time."""
    # FIXME For test compatibility. The tests patch this function
    return trigger.dt_now()


class TimeActiveDecorator(TriggerHandlerDecorator, AutoKwargsDecorator):
    """Implementation for @time_active."""

    name = "time_active"
    args_schema = vol.Schema(vol.All([vol.Coerce(str)], vol.Length(min=0)))
    kwargs_schema = vol.Schema({vol.Optional("hold_off", default=0.0): cv.positive_float})

    hold_off: float

    last_trig_time: float = 0.0

    async def handle_dispatch(self, data: DispatchData) -> bool:
        if self.last_trig_time > 0.0 and self.hold_off > 0.0:
            if time.monotonic() - self.last_trig_time < self.hold_off:
                return False

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
                    return True
            return False

        self.last_trig_time = time.monotonic()
        return True


class TimeTriggerDecorator(TriggerDecorator):
    """Implementation for @time_trigger."""

    name = "time_trigger"
    # args_schema = vol.Schema(vol.All([vol.Coerce(str)], vol.Length(min=0)))
    args_schema = vol.Schema(
        vol.All(
            vol.Length(min=0),
            vol.All(
                [str], msg="argument 2 should be a string"
            ),  # FIXME For test compatibility. Update the message in the future.
        )
    )

    run_on_startup: bool = False
    run_on_shutdown: bool = False
    timespec: list[str]
    _cycle_task: asyncio.Task

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
                _LOGGER.debug("%s sleeping for %s seconds", self, timeout)
                await asyncio.sleep(timeout)
                _LOGGER.debug("%s finish sleeping for %s seconds", self, timeout)
                while True:
                    now = dt_now()
                    timeout = (time_next_adj - now).total_seconds()
                    if timeout <= 1e-6:
                        break
                    _LOGGER.debug("%s additional sleep for %s seconds", self, timeout)
                    await asyncio.sleep(timeout)

                await self.dispatch(DispatchData({"trigger_type": "time", "trigger_time": time_next}))
        except asyncio.CancelledError:
            raise

    async def stop(self):
        """Stop the trigger."""
        if hasattr(self, "_cycle_task"):
            self._cycle_task.cancel()
        if self.run_on_shutdown:
            await self.dispatch(DispatchData({"trigger_type": "time", "trigger_time": "shutdown"}))

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.dm.logger.exception(f"{self} failed", exc_info=exc)

    async def start(self) -> None:
        """Start the decorator."""
        await super().start()
        self._cycle_task = self.dm.hass.async_create_task(self._cycle())
        self._cycle_task.add_done_callback(self._on_task_done)
