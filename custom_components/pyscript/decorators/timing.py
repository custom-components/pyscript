"""Time decorators."""

from __future__ import annotations

import asyncio
import datetime as dt
import locale
import logging
import math
import re
import time
from typing import ClassVar

from croniter import croniter
import voluptuous as vol

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.helpers import config_validation as cv, sun
from homeassistant.util import dt as dt_util

from .. import decorator_abc
from ..decorator import WaitUntilDecoratorManager
from ..decorator_abc import DecoratorManagerStatus, DispatchData, TriggerDecorator, TriggerHandlerDecorator
from .base import AutoKwargsDecorator

_LOGGER = logging.getLogger(__name__)


def dt_now():
    """Return current time."""
    # decorator_abc.dt_now is looked up on each call, so tests can patch it there.
    return decorator_abc.dt_now()


def parse_time_offset(offset_str):
    """Parse a time offset."""
    match = re.split(r"([-+]?\s*\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(\w*)", offset_str)
    scale = 1
    value = 0
    if len(match) == 4:
        value = float(match[1].replace(" ", ""))
        if match[2] in {"m", "min", "mins", "minute", "minutes"}:
            scale = 60
        elif match[2] in {"h", "hr", "hour", "hours"}:
            scale = 60 * 60
        elif match[2] in {"d", "day", "days"}:
            scale = 60 * 60 * 24
        elif match[2] in {"w", "week", "weeks"}:
            scale = 60 * 60 * 24 * 7
        elif match[2] not in {"", "s", "sec", "second", "seconds"}:
            _LOGGER.error("can't parse time offset %s", offset_str)
    else:
        _LOGGER.error("can't parse time offset %s", offset_str)
    return value * scale


class TrigTime:
    """Class for trigger time functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # Mappings of day of week name to number, using US convention of sunday is 0.
    # Initialized based on locale at startup.
    #
    dow2int: ClassVar[dict[str, int]] = {}

    def __init__(self):
        """Warn on TrigTime instantiation."""
        _LOGGER.error("TrigTime class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize TrigTime, unless already done for this hass instance."""
        if cls.hass is hass:
            return
        cls.hass = hass

        try:
            for i in range(0, 7):
                cls.dow2int[locale.nl_langinfo(getattr(locale, f"ABDAY_{i + 1}")).lower()] = i
                cls.dow2int[locale.nl_langinfo(getattr(locale, f"DAY_{i + 1}")).lower()] = i
        except AttributeError:
            # Win10 Python doesn't have locale.nl_langinfo, so default to English days of week
            dow = [
                "sunday",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
            ]
            for idx, name in enumerate(dow):
                cls.dow2int[name] = idx
                cls.dow2int[name[0:3]] = idx

    @classmethod
    async def parse_date_time(cls, date_time_str, day_offset, now, startup_time):
        """Parse a date time string, returning datetime."""
        year = now.year
        month = now.month
        day = now.day
        fixed_date = False

        dt_str_orig = dt_str = date_time_str.strip().lower()
        #
        # parse the date
        #
        match0 = re.match(r"0*(\d+)[-/]0*(\d+)(?:[-/]0*(\d+))?", dt_str)
        match1 = re.match(r"(\w+)", dt_str)
        if match0:
            if match0[3]:
                year, month, day = int(match0[1]), int(match0[2]), int(match0[3])
            else:
                month, day = int(match0[1]), int(match0[2])
            day_offset = 0  # explicit date means no offset
            fixed_date = True
            dt_str = dt_str[len(match0.group(0)) :]
        elif match1:
            skip = True
            if match1[1] in cls.dow2int:
                dow = cls.dow2int[match1[1]]
                if dow >= (now.isoweekday() % 7):
                    day_offset = dow - (now.isoweekday() % 7)
                else:
                    day_offset = 7 + dow - (now.isoweekday() % 7)
                fixed_date = True
            elif match1[1] == "today":
                day_offset = 0
                fixed_date = True
            elif match1[1] == "tomorrow":
                day_offset = 1
                fixed_date = True
            else:
                if match1[1] == "now":
                    day_offset = 0
                    fixed_date = True
                skip = False
            if skip:
                dt_str = dt_str[len(match1.group(0)) :]
        if day_offset != 0:
            now = dt.datetime(year, month, day) + dt.timedelta(days=day_offset)
            year = now.year
            month = now.month
            day = now.day
        else:
            now = dt.datetime(year, month, day)
        dt_str = dt_str.strip()
        if len(dt_str) == 0:
            return now, fixed_date

        #
        # parse the time
        #
        match0 = re.match(r"0*(\d+):0*(\d+)(?::0*(\d*\.?\d+(?:[eE][-+]?\d+)?))?", dt_str)
        if match0:
            if match0[3]:
                hour, mins, sec = int(match0[1]), int(match0[2]), float(match0[3])
            else:
                hour, mins, sec = int(match0[1]), int(match0[2]), 0
            dt_str = dt_str[len(match0.group(0)) :]
        elif dt_str.startswith(SUN_EVENT_SUNRISE) or dt_str.startswith(SUN_EVENT_SUNSET):
            event = SUN_EVENT_SUNRISE if dt_str.startswith(SUN_EVENT_SUNRISE) else SUN_EVENT_SUNSET
            time_sun = sun.get_astral_event_date(cls.hass, event, dt.date(year, month, day))
            if time_sun is None:
                _LOGGER.warning("'%s' not defined at this latitude", dt_str)
                # return something in the past so it is ignored
                return now - dt.timedelta(days=100), fixed_date
            time_sun = dt_util.as_local(time_sun)
            dt_str = dt_str[len(event) :]
            now += time_sun.date() - now.date()
            hour, mins, sec = time_sun.hour, time_sun.minute, time_sun.second
        elif dt_str.startswith("noon"):
            hour, mins, sec = 12, 0, 0
            dt_str = dt_str[4:]
        elif dt_str.startswith("midnight"):
            hour, mins, sec = 0, 0, 0
            dt_str = dt_str[8:]
        elif dt_str.startswith("now") and dt_str_orig == dt_str:
            #
            # "now" means the first time, and only matches if there was no date specification
            #
            hour, mins, sec = 0, 0, 0
            now = startup_time
            dt_str = dt_str[3:]
        else:
            hour, mins, sec = 0, 0, 0
        now += dt.timedelta(seconds=sec + 60 * (mins + 60 * hour))
        #
        # parse the offset
        #
        dt_str = dt_str.strip()
        if len(dt_str) > 0:
            now = now + dt.timedelta(seconds=parse_time_offset(dt_str))
        return now, fixed_date

    @classmethod
    async def timer_active_check(cls, time_spec, now, startup_time):
        """Check if the given time matches the time specification."""
        results = {"+": [], "-": []}
        for entry in time_spec if isinstance(time_spec, list) else [time_spec]:
            this_match = False
            negate = False
            active_str = entry.strip()
            if active_str.startswith("not"):
                negate = True
                active_str = active_str.replace("not ", "")

            cron_match = re.match(r"cron\((?P<cron_expr>.*)\)", active_str)
            range_expr = re.match(r"range\(([^,]+),\s?([^,]+)\)", active_str)
            if cron_match:
                if not croniter.is_valid(cron_match.group("cron_expr")):
                    _LOGGER.error("Invalid cron expression: %s", cron_match)
                    return False

                this_match = croniter.match(cron_match.group("cron_expr"), now)

            elif range_expr:
                try:
                    dt_start, dt_end = range_expr.groups()
                except ValueError as exc:
                    _LOGGER.error("Invalid range expression: %s", exc)
                    return False

                start, _ = await cls.parse_date_time(dt_start.strip(), 0, now, startup_time)
                end, _ = await cls.parse_date_time(dt_end.strip(), 0, start, startup_time)

                if start <= end:
                    this_match = start <= now <= end
                else:  # Over midnight
                    this_match = now >= start or now <= end
            else:
                _LOGGER.error("Invalid time_active expression: %s", active_str)
                return False

            if negate:
                results["-"].append(not this_match)
            else:
                results["+"].append(this_match)

        # An empty spec, or only neg specs, is True
        result = (any(results["+"]) if results["+"] else True) and all(results["-"])

        return result

    @classmethod
    async def timer_trigger_next(cls, time_spec, now, startup_time):
        """Return the next trigger time based on the given time and time specification."""
        next_time = None
        next_time_adj = None
        if not isinstance(time_spec, list):
            time_spec = [time_spec]
        for spec in time_spec:
            cron_match = re.search(r"cron\((?P<cron_expr>.*)\)", spec)
            match1 = re.split(r"once\((.*)\)", spec)
            match2 = re.split(r"period\(([^,]*),([^,]*)(?:,([^,]*))?\)", spec)
            if cron_match:
                if not croniter.is_valid(cron_match.group("cron_expr")):
                    _LOGGER.error("Invalid cron expression: %s", cron_match)
                    continue

                #
                # Handling DST changes is tricky; all times in pyscript are naive (no timezone).  This is the
                # one part of the code where we do check timezones, in case now and next_time bracket a DST
                # change.  We return next_time as the local time of the next trigger according to the cron
                # spec, and next_time_adj is potentially adjusted so that (next_time_adj - now) is the correct
                # timedelta to wait (eg: if cron is a daily trigger at 6am, next_time will always be 6am
                # tomorrow, and next_time_adj will also by 6am, except on the day of a DST change, when it
                # will be 5am or 7am, such that (next_time_adj - now) is 23 hours or 25 hours.
                #
                # We might have to fetch multiple croniter times, in case (next_time_adj - now) is non-positive
                # after a DST change.
                #
                # Also, datetime doesn't correctly subtract datetimes in different timezones, so we need to compute
                # the different in UTC.  See https://blog.ganssle.io/articles/2018/02/aware-datetime-arithmetic.html.
                #
                cron_iter = croniter(cron_match.group("cron_expr"), now, dt.datetime)
                delta = None
                while delta is None or delta.total_seconds() <= 0:
                    val = cron_iter.get_next()
                    delta = dt_util.as_local(val).astimezone(dt_util.UTC) - dt_util.as_local(now).astimezone(
                        dt_util.UTC
                    )

                if next_time is None or val < next_time:
                    next_time = val
                    next_time_adj = now + delta

            elif len(match1) == 3:
                this_t, _ = await cls.parse_date_time(match1[1].strip(), 0, now, startup_time)
                day_offset = (now - this_t).days + 1
                if day_offset != 0 and this_t != startup_time:
                    #
                    # Try a day offset (won't make a difference if spec has full date)
                    #
                    this_t, _ = await cls.parse_date_time(match1[1].strip(), day_offset, now, startup_time)
                startup = now == this_t and now == startup_time
                if (now < this_t or startup) and (next_time is None or this_t < next_time):
                    next_time_adj = next_time = this_t

            elif len(match2) == 5:
                start_str, period_str = match2[1].strip(), match2[2].strip()
                start, fixed_date_start = await cls.parse_date_time(start_str, 0, now, startup_time)
                period = parse_time_offset(period_str)
                if period <= 0:
                    _LOGGER.error("Invalid non-positive period %s in period(): %s", period, time_spec)
                    continue

                if match2[3] is None:
                    startup = now == start and now == startup_time
                    if (now < start or startup) and (next_time is None or start < next_time):
                        next_time_adj = next_time = start
                    if now >= start and not startup:
                        secs = period * (1.0 + math.floor((now - start).total_seconds() / period))
                        this_t = start + dt.timedelta(seconds=secs)
                        if now < this_t and (next_time is None or this_t < next_time):
                            next_time_adj = next_time = this_t
                    continue
                end_str = match2[3].strip()
                end, fixed_date_end = await cls.parse_date_time(end_str, 0, now, startup_time)
                if not fixed_date_start and not fixed_date_end:
                    end_offset = 1 if end < start else 0
                    day_dither = [-1, 0, 1]
                else:
                    end_offset = 0
                    day_dither = [0]
                for day in day_dither:
                    start, _ = await cls.parse_date_time(start_str, day, now, startup_time)
                    end, _ = await cls.parse_date_time(end_str, day + end_offset, now, startup_time)
                    if (now < start or (now == start and now == startup_time)) and start <= end:
                        if next_time is None or start < next_time:
                            next_time_adj = next_time = start
                        break
                    secs = period * (1.0 + math.floor((now - start).total_seconds() / period))
                    this_t = start + dt.timedelta(seconds=secs)
                    if start <= this_t <= end:
                        if next_time is None or this_t < next_time:
                            next_time_adj = next_time = this_t
                        break

            else:
                _LOGGER.warning("Can't parse %s in time_trigger check", spec)
        return next_time, next_time_adj


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

    async def validate(self) -> None:
        """Validate the decorator arguments."""
        await super().validate()
        TrigTime.init(self.dm.hass)

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
                if await TrigTime.timer_active_check(time_spec, now, self.dm.startup_time):
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
        TrigTime.init(self.dm.hass)
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
                time_next, time_next_adj = await TrigTime.timer_trigger_next(
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
