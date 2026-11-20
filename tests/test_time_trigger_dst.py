"""Test @time_trigger timing across a daylight saving time change."""

import asyncio
import datetime as dt
from itertools import pairwise
from types import SimpleNamespace

import pytest

from custom_components.pyscript import trigger
from custom_components.pyscript.decorator_abc import DecoratorManagerStatus
from custom_components.pyscript.decorators import timing
from custom_components.pyscript.decorators.timing import TimeTriggerDecorator
from custom_components.pyscript.function import Function
from custom_components.pyscript.trigger import TrigTime
from homeassistant.util import dt as dt_util


class FakeClock:
    """A UTC clock whose naive local reading repeats an hour when the clocks go back."""

    def __init__(self, utc: dt.datetime) -> None:
        """Start the clock at the given aware UTC time."""
        self.utc = utc

    def utcnow(self) -> dt.datetime:
        """Return the current UTC time."""
        return self.utc

    def now(self) -> dt.datetime:
        """Return naive local time, like datetime.now()."""
        return dt_util.as_local(self.utc).replace(tzinfo=None)

    async def sleep(self, seconds: float) -> None:
        """Advance the clock instead of sleeping."""
        self.utc += dt.timedelta(seconds=max(0, seconds))


async def run_time_trigger(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, spec: str, count: int
) -> list[dt.datetime]:
    """Run a time trigger's cycle and return the UTC time of each of its first count dispatches."""
    monkeypatch.setattr(trigger, "dt_now", clock.now)
    monkeypatch.setattr(timing, "dt_now", clock.now)
    monkeypatch.setattr(timing, "dt_utcnow", clock.utcnow)
    monkeypatch.setattr(
        timing, "asyncio", SimpleNamespace(sleep=clock.sleep, CancelledError=asyncio.CancelledError)
    )

    decorator = TimeTriggerDecorator([spec], {})
    decorator.timespec = [spec]
    decorator.dm = SimpleNamespace(
        name="dst_test",
        status=DecoratorManagerStatus.RUNNING,
        # Started a day earlier: a period() that started inside the repeated hour
        # would wait for its start time to come round again.
        startup_time=clock.now() - dt.timedelta(days=1),
    )

    fired = []

    async def dispatch(data):
        fired.append(clock.utcnow())
        if len(fired) >= count:
            decorator.dm.status = DecoratorManagerStatus.STOPPED

    decorator.dispatch = dispatch
    await decorator._cycle()  # pylint: disable=protected-access
    return fired


@pytest.fixture
async def los_angeles(hass):
    """Use a time zone whose clocks go back at 2:00 on 2019-11-03."""
    await hass.config.async_set_time_zone("America/Los_Angeles")
    Function.init(hass)
    TrigTime.init(hass)
    yield
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


@pytest.mark.asyncio
async def test_period_keeps_running_when_clocks_go_back(hass, los_angeles, monkeypatch):
    """A period() trigger keeps its interval through the repeated hour (issue #649)."""
    # 01:59:40 PDT; the clocks go back to 01:00 PST at 09:00 UTC.
    clock = FakeClock(dt.datetime(2019, 11, 3, 8, 59, 40, tzinfo=dt.UTC))
    fired = await run_time_trigger(monkeypatch, clock, "period(now, 5s)", 8)
    gaps = [(b - a).total_seconds() for a, b in pairwise(fired)]
    assert gaps == [5.0] * 7
    assert fired[0] < dt.datetime(2019, 11, 3, 9, 0, tzinfo=dt.UTC) < fired[-1]


@pytest.mark.asyncio
async def test_cron_waits_once_when_clocks_go_back(hass, los_angeles, monkeypatch):
    """A cron() trigger crossing the change waits the computed time, not an extra hour."""
    # 01:50 PDT. 01:55 PDT is 08:55 UTC; the next match, 02:00 PST, is 10:00 UTC.
    clock = FakeClock(dt.datetime(2019, 11, 3, 8, 50, tzinfo=dt.UTC))
    fired = await run_time_trigger(monkeypatch, clock, "cron(*/5 * * * *)", 3)
    assert fired == [
        dt.datetime(2019, 11, 3, 8, 55, tzinfo=dt.UTC),
        dt.datetime(2019, 11, 3, 10, 0, tzinfo=dt.UTC),
        dt.datetime(2019, 11, 3, 10, 5, tzinfo=dt.UTC),
    ]


@pytest.mark.asyncio
async def test_period_keeps_running_when_clocks_go_forward(hass, los_angeles, monkeypatch):
    """A period() trigger keeps its interval when the clocks go forward."""
    # 01:59:50 PST on 2020-03-08; the clocks go forward to 03:00 PDT at 10:00 UTC.
    clock = FakeClock(dt.datetime(2020, 3, 8, 9, 59, 50, tzinfo=dt.UTC))
    fired = await run_time_trigger(monkeypatch, clock, "period(now, 5s)", 6)
    gaps = [(b - a).total_seconds() for a, b in pairwise(fired)]
    assert gaps == [5.0] * 5
