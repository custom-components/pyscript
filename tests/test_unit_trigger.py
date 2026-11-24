"""Unit tests for time trigger functions."""

import ast as ast_module
from datetime import datetime as dt, timedelta
import random
from unittest.mock import patch
import warnings

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.pyscript.const import CONF_ALLOW_ALL_IMPORTS, CONFIG_ENTRY, DOMAIN
from custom_components.pyscript.decorators.state import strip_trigger_expr
from custom_components.pyscript.decorators.timing import TrigTime
from custom_components.pyscript.eval import AstEval
from custom_components.pyscript.function import Function
from custom_components.pyscript.global_ctx import GlobalContext, GlobalContextMgr

parseDateTimeTests = [
    ["2019/9/12 13:45", 0, dt(2019, 9, 12, 13, 45, 0, 0)],
    ["9/12 13:45", 0, dt(2019, 9, 12, 13, 45, 0, 0)],
    ["2019/9/12 13:45:23", 0, dt(2019, 9, 12, 13, 45, 23, 0)],
    ["2019/9/12", 0, dt(2019, 9, 12, 0, 0, 0, 0)],
    ["2019/9/12 noon", 1, dt(2019, 9, 12, 12, 0, 0, 0)],
    ["2019/9/12 noon+1 min", 2, dt(2019, 9, 12, 12, 1, 0, 0)],
    ["2019/9/12 noon +2.5min", 1, dt(2019, 9, 12, 12, 2, 30, 0)],
    ["2019/9/12 noon+3 hr", 4, dt(2019, 9, 12, 15, 0, 0, 0)],
    ["2019/9/12 noon - 30 sec", 3, dt(2019, 9, 12, 11, 59, 30, 0)],
    ["2019/9/12 midnight", 3, dt(2019, 9, 12, 0, 0, 0, 0)],
    ["2019/9/12 midnight + 30 min", 3, dt(2019, 9, 12, 0, 30, 0, 0)],
    ["tomorrow", 0, dt(2019, 9, 2, 0, 0, 0, 0)],
    ["tomorrow 9:23:00", 0, dt(2019, 9, 2, 9, 23, 0, 0)],
    ["tomorrow 9:23", 0, dt(2019, 9, 2, 9, 23, 0, 0)],
    ["tomorrow noon", 0, dt(2019, 9, 2, 12, 0, 0, 0)],
    ["today", 0, dt(2019, 9, 1, 0, 0, 0, 0)],
    ["sunday", 0, dt(2019, 9, 1, 0, 0, 0, 0)],
    ["monday+2.5 hours", 0, dt(2019, 9, 2, 2, 30, 0, 0)],
    ["monday + 1 day", 0, dt(2019, 9, 3, 0, 0, 0, 0)],
    ["monday + 1 week", 0, dt(2019, 9, 9, 0, 0, 0, 0)],
    ["tuesday", 0, dt(2019, 9, 3, 0, 0, 0, 0)],
    ["wednesday", 0, dt(2019, 9, 4, 0, 0, 0, 0)],
    ["thursday", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["thursday 00:00", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["thursday 0:0:0", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["friday", 0, dt(2019, 9, 6, 0, 0, 0, 0)],
    ["saturday", 0, dt(2019, 9, 7, 0, 0, 0, 0)],
    ["sun", 0, dt(2019, 9, 1, 0, 0, 0, 0)],
    ["mon", 0, dt(2019, 9, 2, 0, 0, 0, 0)],
    ["tue", 0, dt(2019, 9, 3, 0, 0, 0, 0)],
    ["wed 9:45 + 42 sec", 0, dt(2019, 9, 4, 9, 45, 42, 0)],
    ["thu", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["fri 16:11:15.5", 0, dt(2019, 9, 6, 16, 11, 15, 500000)],
    ["sat", 0, dt(2019, 9, 7, 0, 0, 0, 0)],
    ["14:56", 0, dt(2019, 9, 1, 14, 56, 0, 0)],
    ["8:00", 0, dt(2019, 9, 1, 8, 0, 0, 0)],
    ["16:01", 0, dt(2019, 9, 1, 16, 1, 0, 0)],
    ["14:56", 1, dt(2019, 9, 2, 14, 56, 0, 0)],
    ["8:00:23.6", 1, dt(2019, 9, 2, 8, 0, 23, 600000)],
    ["sunrise", 0, dt(2019, 9, 1, 6, 37, 58, 0)],
    ["sunrise", 1, dt(2019, 9, 2, 6, 38, 49, 0)],
    ["sunrise", 2, dt(2019, 9, 3, 6, 39, 41, 0)],
    ["tuesday sunrise", 0, dt(2019, 9, 3, 6, 39, 41, 0)],
    ["sunrise + 1hr", 0, dt(2019, 9, 1, 7, 37, 58, 0)],
    ["sunset", 0, dt(2019, 9, 1, 19, 37, 23, 0)],
    ["now", 0, dt(2019, 9, 1, 13, 0, 0, 0)],
    ["now +1 min", 0, dt(2019, 9, 1, 13, 1, 0, 0)],
    ["now +1 hours", 0, dt(2019, 9, 1, 14, 0, 0, 0)],
    ["2019/11/4 sunset + 1min", 0, dt(2019, 11, 4, 17, 6, 37, 0)],
    ["11/4 sunset + 2min", 0, dt(2019, 11, 4, 17, 7, 37, 0)],
    ["+5 min", 0, dt(2019, 9, 1, 0, 5, 0, 0)],
]


@pytest.mark.asyncio
async def test_parse_date_time(hass, caplog):
    """Run time parse datetime tests."""

    #
    # Hardcode a location and timezone so we can check sunrise
    # and sunset.
    #
    hass.config.latitude = 38
    hass.config.longitude = -122
    hass.config.elevation = 0
    hass.config.time_zone = "America/Los_Angeles"

    Function.init(hass)
    TrigTime.init(hass)

    #
    # This set of tests assumes it's currently 13:00 on 2019/9/1
    #
    now = dt(2019, 9, 1, 13, 0, 0, 0)

    with (
        patch("homeassistant.util.dt.utcnow", return_value=now),
    ):
        for test_data in parseDateTimeTests:
            spec, date_offset, expect = test_data
            out, _ = await TrigTime.parse_date_time(spec, date_offset, now, now)
            assert out == expect
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


parseDateTimeTestsDayNames = [
    ["thu", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["fri noon", 0, dt(2019, 9, 6, 12, 0, 0, 0)],
    ["sunday", 0, dt(2019, 9, 8, 0, 0, 0, 0)],
    ["monday", 0, dt(2019, 9, 9, 0, 0, 0, 0)],
]


@pytest.mark.asyncio
async def test_parse_date_time_day_names(hass, caplog):
    """Run time parse datetime on day of week names."""
    #
    # Hardcode a location and timezone so we can check sunrise
    # and sunset.
    #
    hass.config.latitude = 38
    hass.config.longitude = -122
    hass.config.elevation = 0
    hass.config.time_zone = "America/Los_Angeles"

    Function.init(hass)
    TrigTime.init(hass)

    #
    # This set of tests assumes it's currently 13:00 on 2019/9/3
    #
    now = dt(2019, 9, 3, 13, 0, 0, 0)
    with (
        patch("homeassistant.util.dt.utcnow", return_value=now),
    ):
        for test_data in parseDateTimeTestsDayNames:
            spec, date_offset, expect = test_data
            out, _ = await TrigTime.parse_date_time(spec, date_offset, now, now)
            assert out == expect
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


@pytest.mark.parametrize(
    "spec,now,expected",
    [
        (
            ["range(1:00, 4:00)", "not range(2:00, 3:00)", "range(22:00, 0:00)", "not range(3:30, 3:45)"],
            dt(2019, 8, 31, 22, 0, 0, 0),
            True,
        ),
        ("range(2019/9/1 8:00, 2019/9/1 18:00)", dt(2019, 8, 31, 8, 0, 0, 0), False),
        ("range(2019/9/1 8:00, 2019/9/1 18:00)", dt(2019, 9, 1, 7, 59, 59, 0), False),
        ("range(2019/9/1 8:00, 2019/9/1 18:00)", dt(2019, 9, 1, 8, 0, 0, 0), True),
        ("range(2019/9/1 8:00, 2019/9/1 18:00)", dt(2019, 9, 1, 18, 0, 0, 0), True),
        ("range(2019/9/1 8:00, 2019/9/1 18:00)", dt(2019, 9, 1, 18, 0, 0, 1), False),
        ("range(2019/9/1 8:00, 2019/9/3  6:00)", dt(2019, 8, 31, 8, 0, 0, 0), False),
        ("range(2019/9/1 8:00, 2019/9/3  6:00)", dt(2019, 9, 1, 7, 59, 59, 0), False),
        ("range(2019/9/1 8:00, 2019/9/3  6:00)", dt(2019, 9, 1, 8, 0, 0, 0), True),
        ("range(2019/9/1 8:00, 2019/9/3  6:00)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("range(2019/9/1 8:00, 2019/9/3  6:00)", dt(2019, 9, 3, 6, 0, 0, 1), False),
        ("range(now, now)", dt(2019, 9, 1, 12, 59, 59, 999999), False),
        ("range(now, now)", dt(2019, 9, 1, 13, 0, 0, 0), True),
        ("range(now, now)", dt(2019, 9, 1, 13, 0, 0, 1), False),
        ("range(now + 1min, now + 1hour)", dt(2019, 9, 1, 13, 0, 59, 999999), False),
        ("range(now + 1min, now + 1hour)", dt(2019, 9, 1, 13, 1, 0, 0), True),
        ("range(now + 1min, now + 1hour)", dt(2019, 9, 1, 14, 0, 0, 0), True),
        ("range(now + 1min, now + 1hour)", dt(2019, 9, 1, 14, 0, 0, 1), False),
        ("range(10:00, 20:00)", dt(2019, 9, 3, 9, 59, 59, 999999), False),
        ("range(10:00, 20:00)", dt(2019, 9, 3, 10, 0, 0, 0), True),
        ("range(10:00, 20:00)", dt(2019, 9, 3, 20, 0, 0, 0), True),
        ("range(10:00, 20:00)", dt(2019, 9, 3, 20, 0, 0, 1), False),
        ("range(20:00, 10:00)", dt(2019, 9, 3, 9, 59, 59, 999999), True),
        ("range(20:00, 10:00)", dt(2019, 9, 3, 10, 0, 0, 0), True),
        ("range(20:00, 10:00)", dt(2019, 9, 3, 10, 0, 0, 1), False),
        ("range(20:00, 10:00)", dt(2019, 9, 3, 9, 59, 59, 999999), True),
        ("range(20:00, 10:00)", dt(2019, 9, 3, 20, 0, 0, 1), True),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 10, 0, 0, 0), False),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 9, 59, 59, 999999), False),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 20, 0, 0, 0), False),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 19, 59, 59, 999999), True),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 12, 0, 0, 0), True),
        ("not range(20:00, 10:00)", dt(2019, 9, 3, 0, 0, 0, 0), False),
        ("range(sunrise, sunset)", dt(2019, 9, 1, 6, 37, 57, 0), False),
        ("range(sunrise, sunset)", dt(2019, 9, 1, 6, 37, 59, 0), True),
        ("range(sunrise, sunset - 20m)", dt(2019, 9, 1, 19, 17, 22, 0), True),
        ("range(sunrise, sunset - 20m)", dt(2019, 9, 1, 19, 17, 24, 0), False),
        ("range(sunrise + 20m, sunset)", dt(2019, 9, 2, 6, 58, 48, 0), False),
        ("range(sunrise + 20m, sunset)", dt(2019, 9, 2, 6, 58, 50, 0), True),
        ("range(sunrise, sunset + 1m)", dt(2019, 11, 4, 17, 6, 36, 0), True),
        ("range(sunrise, sunset + 1m)", dt(2019, 11, 4, 17, 6, 38, 0), False),
        ("cron(* * * * *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("cron(* * * 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("cron(* * 3 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("cron(* 6 3 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("cron(0 6 3 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
        ("cron(* * 4 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), False),
        ("not cron(0 6 3 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), False),
        ("not cron(* * 4 9 *)", dt(2019, 9, 3, 6, 0, 0, 0), True),
    ],
    ids=lambda x: x if not isinstance(x, (dt, list)) else str(x),
)
@pytest.mark.asyncio
async def test_timer_active_check(hass, spec, now, expected):
    """Run time active check tests."""

    #
    # Hardcode a location and timezone so we can check sunrise
    # and sunset.
    #
    hass.config.latitude = 38
    hass.config.longitude = -122
    hass.config.elevation = 0
    hass.config.time_zone = "America/Los_Angeles"

    startup_time = dt(2019, 9, 1, 13, 0, 0, 0)
    Function.init(hass)
    TrigTime.init(hass)
    # print(f"calling timer_active_check({spec}, {now}, {startup_time})")
    out = await TrigTime.timer_active_check(spec, now, startup_time)
    assert out == expected
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


timerTriggerNextTests = [
    [["once(2019/9/1 8:00)"], [None]],
    [["once(2019/9/1 15:00)"], [dt(2019, 9, 1, 15, 0, 0, 0)]],
    [["once(15:00)"], [dt(2019, 9, 1, 15, 0, 0, 0)]],
    [["once(15:00 + 2d)"], [dt(2019, 9, 1, 15, 0, 0, 0), dt(2019, 9, 2, 15, 0, 0, 0)]],
    [["once(15:00 - 48h)"], [dt(2019, 9, 1, 15, 0, 0, 0), dt(2019, 9, 2, 15, 0, 0, 0)]],
    [["once(11:00 + 2d)"], [dt(2019, 9, 2, 11, 0, 0, 0), dt(2019, 9, 3, 11, 0, 0, 0)]],
    [["once(11:00 - 48h)"], [dt(2019, 9, 2, 11, 0, 0, 0), dt(2019, 9, 3, 11, 0, 0, 0)]],
    [["once(13:00:0.09)"], [dt(2019, 9, 2, 13, 0, 0, 90000)]],
    [["once(9:00)"], [dt(2019, 9, 2, 9, 0, 0, 0)]],
    [["once(wed 9:00)"], [dt(2019, 9, 4, 9, 0, 0, 0)]],
    [["once(2019/9/10 23:59:13)"], [dt(2019, 9, 10, 23, 59, 13, 0)]],
    [["once(now)"], [dt(2019, 9, 1, 13, 0, 0, 100000), None]],
    [["once(now + 1min)"], [dt(2019, 9, 1, 13, 1, 0, 100000), None]],
    [["once(now + 1day)"], [dt(2019, 9, 2, 13, 0, 0, 100000), None]],
    [
        ["period(2019/9/1 13:00, 120s)"],
        [dt(2019, 9, 1, 13, 2, 0, 0), dt(2019, 9, 1, 13, 4, 0, 0), dt(2019, 9, 1, 13, 6, 0, 0)],
    ],
    [
        ["period(13:01, 120s)"],
        [dt(2019, 9, 1, 13, 1, 0, 0), dt(2019, 9, 1, 13, 3, 0, 0), dt(2019, 9, 1, 13, 5, 0, 0)],
    ],
    [
        ["period(10:01, 120s, 12:00)"],
        [dt(2019, 9, 2, 10, 1, 0, 0), dt(2019, 9, 2, 10, 3, 0, 0), dt(2019, 9, 2, 10, 5, 0, 0)],
    ],
    [["period(2019/9/1 12:59, 180s)"], [dt(2019, 9, 1, 13, 2, 0, 0)]],
    [["period(2019/9/1 12:50, 180s)"], [dt(2019, 9, 1, 13, 2, 0, 0)]],
    [
        ["period(2019/9/1 0:50, 180s)"],
        [
            dt(2019, 9, 1, 13, 2, 0, 0),
            dt(2019, 9, 1, 13, 5, 0, 0),
            dt(2019, 9, 1, 13, 8, 0, 0),
            dt(2019, 9, 1, 13, 11, 0, 0),
        ],
    ],
    [
        ["period(2019/9/1 13:00, 120s, 2019/9/1 13:04)"],
        [dt(2019, 9, 1, 13, 2, 0, 0), dt(2019, 9, 1, 13, 4, 0, 0), None],
    ],
    [
        ["period(18:00, 4 hr, 6:00)"],
        [
            dt(2019, 9, 1, 18, 0, 0, 0),
            dt(2019, 9, 1, 22, 0, 0, 0),
            dt(2019, 9, 2, 2, 0, 0, 0),
            dt(2019, 9, 2, 6, 0, 0, 0),
            dt(2019, 9, 2, 18, 0, 0, 0),
            dt(2019, 9, 2, 22, 0, 0, 0),
            dt(2019, 9, 3, 2, 0, 0, 0),
            dt(2019, 9, 3, 6, 0, 0, 0),
            dt(2019, 9, 3, 18, 0, 0, 0),
        ],
    ],
    [
        ["period(18:00, 12 hr, 6:00)"],
        [
            dt(2019, 9, 1, 18, 0, 0, 0),
            dt(2019, 9, 2, 6, 0, 0, 0),
            dt(2019, 9, 2, 18, 0, 0, 0),
            dt(2019, 9, 3, 6, 0, 0, 0),
        ],
    ],
    [
        ["period(18:00, 12.0001 hr, 6:00)"],
        [
            dt(2019, 9, 1, 18, 0, 0, 0),
            dt(2019, 9, 2, 18, 0, 0, 0),
            dt(2019, 9, 3, 18, 0, 0, 0),
            dt(2019, 9, 4, 18, 0, 0, 0),
        ],
    ],
    [
        ["period(6:00, 12 hr, 18:00)"],
        [
            dt(2019, 9, 1, 18, 0, 0, 0),
            dt(2019, 9, 2, 6, 0, 0, 0),
            dt(2019, 9, 2, 18, 0, 0, 0),
            dt(2019, 9, 3, 6, 0, 0, 0),
            dt(2019, 9, 3, 18, 0, 0, 0),
            dt(2019, 9, 4, 6, 0, 0, 0),
        ],
    ],
    [
        ["period(6:00, 12.0001 hr, 18:00)"],
        [dt(2019, 9, 2, 6, 0, 0, 0), dt(2019, 9, 3, 6, 0, 0, 0), dt(2019, 9, 4, 6, 0, 0, 0)],
    ],
    [
        ["period(sunset, 4 hours, sunrise)"],
        [
            dt(2019, 9, 1, 19, 37, 23),
            dt(2019, 9, 1, 23, 37, 23),
            dt(2019, 9, 2, 3, 37, 23),
            dt(2019, 9, 2, 19, 35, 53),
            dt(2019, 9, 2, 23, 35, 53),
            dt(2019, 9, 3, 3, 35, 53),
            dt(2019, 9, 3, 19, 34, 23),
            dt(2019, 9, 3, 23, 34, 23),
        ],
    ],
    [
        ["period(11/3 0:01, 1 hr)"],
        [
            dt(2019, 11, 3, 0, 1, 0, 0),
            dt(2019, 11, 3, 1, 1, 0, 0),
            dt(2019, 11, 3, 2, 1, 0, 0),
            dt(2019, 11, 3, 3, 1, 0, 0),
            dt(2019, 11, 3, 4, 1, 0, 0),
        ],
    ],
    [
        ["period(11/3 0:01, 1 day)"],
        [
            dt(2019, 11, 3, 0, 1, 0, 0),
            dt(2019, 11, 4, 0, 1, 0, 0),
            dt(2019, 11, 5, 0, 1, 0, 0),
            dt(2019, 11, 6, 0, 1, 0, 0),
            dt(2019, 11, 7, 0, 1, 0, 0),
        ],
    ],
    [
        ["period(now, 1 day)"],
        [
            dt(2019, 9, 1, 13, 0, 0, 100000),
            dt(2019, 9, 2, 13, 0, 0, 100000),
            dt(2019, 9, 3, 13, 0, 0, 100000),
        ],
    ],
    [
        ["period(now +1 hours, 1 hours, now+4 hours)"],
        [
            dt(2019, 9, 1, 14, 0, 0, 100000),
            dt(2019, 9, 1, 15, 0, 0, 100000),
            dt(2019, 9, 1, 16, 0, 0, 100000),
            dt(2019, 9, 1, 17, 0, 0, 100000),
            None,
        ],
    ],
    # see #714 for these two cases - in 1.6.4 and prior, they keep triggering since the
    # end time is considered tomorrow
    [
        ["period(now, 1 hours, 15:00.1)"],
        [
            dt(2019, 9, 1, 13, 0, 0, 100000),
            dt(2019, 9, 1, 14, 0, 0, 100000),
            dt(2019, 9, 1, 15, 0, 0, 100000),
            None,
        ],
    ],
    [
        ["period(now, 1 hours, 10:00)"],
        [
            None,
        ],
    ],
    [
        ["period(now, 1 hours, today 15:01)"],
        [
            dt(2019, 9, 1, 13, 0, 0, 100000),
            dt(2019, 9, 1, 14, 0, 0, 100000),
            dt(2019, 9, 1, 15, 0, 0, 100000),
            None,
        ],
    ],
    [
        ["period(now, 1 hours, 10:00)"],
        [
            None,
        ],
    ],
    [
        ["period(today 11:00, 1 hours, today 10:00)"],
        [
            None,
        ],
    ],
    [
        ["cron(0 14 * * *)"],
        [
            dt(2019, 9, 1, 14, 0, 0, 0),
            dt(2019, 9, 2, 14, 0, 0, 0),
            dt(2019, 9, 3, 14, 0, 0, 0),
            dt(2019, 9, 4, 14, 0, 0, 0),
        ],
    ],
    [
        ["cron(0 14 10-13 * *)"],
        [
            dt(2019, 9, 10, 14, 0, 0, 0),
            dt(2019, 9, 11, 14, 0, 0, 0),
            dt(2019, 9, 12, 14, 0, 0, 0),
            dt(2019, 9, 13, 14, 0, 0, 0),
            dt(2019, 10, 10, 14, 0, 0, 0),
            dt(2019, 10, 11, 14, 0, 0, 0),
            dt(2019, 10, 12, 14, 0, 0, 0),
            dt(2019, 10, 13, 14, 0, 0, 0),
            dt(2019, 11, 10, 14, 0, 0, 0),
        ],
    ],
    [
        ["cron(0 14 10,11-12,13 * *)"],
        [
            dt(2019, 9, 10, 14, 0, 0, 0),
            dt(2019, 9, 11, 14, 0, 0, 0),
            dt(2019, 9, 12, 14, 0, 0, 0),
            dt(2019, 9, 13, 14, 0, 0, 0),
            dt(2019, 10, 10, 14, 0, 0, 0),
            dt(2019, 10, 11, 14, 0, 0, 0),
            dt(2019, 10, 12, 14, 0, 0, 0),
            dt(2019, 10, 13, 14, 0, 0, 0),
            dt(2019, 11, 10, 14, 0, 0, 0),
        ],
    ],
    [
        ["cron(23 8 * * 2,4-5)"],
        [
            dt(2019, 9, 3, 8, 23, 0, 0),
            dt(2019, 9, 5, 8, 23, 0, 0),
            dt(2019, 9, 6, 8, 23, 0, 0),
            dt(2019, 9, 10, 8, 23, 0, 0),
            dt(2019, 9, 12, 8, 23, 0, 0),
        ],
    ],
    [
        ["cron(23 8 3-4 * 5-6)"],
        [
            dt(2019, 9, 3, 8, 23, 0, 0),
            dt(2019, 9, 4, 8, 23, 0, 0),
            dt(2019, 9, 6, 8, 23, 0, 0),
            dt(2019, 9, 7, 8, 23, 0, 0),
            dt(2019, 9, 13, 8, 23, 0, 0),
            dt(2019, 9, 14, 8, 23, 0, 0),
        ],
    ],
    [
        # test "/n"
        ["cron(*/10 14 * * *)"],
        [
            dt(2019, 9, 1, 14, 0, 0, 0),
            dt(2019, 9, 1, 14, 10, 0, 0),
            dt(2019, 9, 1, 14, 20, 0, 0),
            dt(2019, 9, 1, 14, 30, 0, 0),
            dt(2019, 9, 1, 14, 40, 0, 0),
            dt(2019, 9, 1, 14, 50, 0, 0),
            dt(2019, 9, 2, 14, 0, 0, 0),
            dt(2019, 9, 2, 14, 10, 0, 0),
        ],
    ],
    [
        # test "/n" spanning 2 hours
        ["cron(*/30 14-15 * * *)"],
        [
            dt(2019, 9, 1, 14, 0, 0, 0),
            dt(2019, 9, 1, 14, 30, 0, 0),
            dt(2019, 9, 1, 15, 0, 0, 0),
            dt(2019, 9, 1, 15, 30, 0, 0),
            dt(2019, 9, 2, 14, 0, 0, 0),
            dt(2019, 9, 2, 14, 30, 0, 0),
        ],
    ],
    [
        # test optional 6th argument seconds
        ["cron(0 14 * * * 10,35)"],
        [
            dt(2019, 9, 1, 14, 0, 10, 0),
            dt(2019, 9, 1, 14, 0, 35, 0),
            dt(2019, 9, 2, 14, 0, 10, 0),
            dt(2019, 9, 2, 14, 0, 35, 0),
        ],
    ],
    [
        # invalid cron expression returns None
        ["cron(0 14 1-2-3 x *)"],
        [None],
    ],
]


@pytest.mark.asyncio
async def test_timer_trigger_next(hass):
    """Run trigger next tests."""
    #
    # Hardcode a location and timezone so we can check sunrise
    # and sunset.
    #
    hass.config.latitude = 38
    hass.config.longitude = -122
    hass.config.elevation = 0
    hass.config.time_zone = "America/Los_Angeles"

    Function.init(hass)
    TrigTime.init(hass)

    for test_data in timerTriggerNextTests:
        startup_time = now = dt(2019, 9, 1, 13, 0, 0, 100000)
        spec, expect_seq = test_data
        for expect in expect_seq:
            t_next, _ = await TrigTime.timer_trigger_next(spec, now, startup_time)
            assert t_next == expect
            if t_next is None:
                break
            now = t_next + timedelta(microseconds=1)
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


timerTriggerNextTestsMonthRollover = [
    [
        # 1pm every day
        "cron(0 13 * * *)",
        [dt(2020, 7, 1, 13, 0, 0, 0), dt(2020, 7, 2, 13, 0, 0, 0), dt(2020, 7, 3, 13, 0, 0, 0)],
    ],
    [
        # 1pm on 4th, 5th, 6th of each month
        "cron(0 13 4-6 * *)",
        [
            dt(2020, 7, 4, 13, 0, 0, 0),
            dt(2020, 7, 5, 13, 0, 0, 0),
            dt(2020, 7, 6, 13, 0, 0, 0),
            dt(2020, 8, 4, 13, 0, 0, 0),
            dt(2020, 8, 5, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on 10th of month except nov
        "cron(0 13 10 1-10,12 *)",
        [
            dt(2020, 7, 10, 13, 0, 0, 0),
            dt(2020, 8, 10, 13, 0, 0, 0),
            dt(2020, 9, 10, 13, 0, 0, 0),
            dt(2020, 10, 10, 13, 0, 0, 0),
            dt(2020, 12, 10, 13, 0, 0, 0),
            dt(2021, 1, 10, 13, 0, 0, 0),
            dt(2021, 2, 10, 13, 0, 0, 0),
            dt(2021, 3, 10, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on monday
        ["cron(0 13 * * 1)"],
        [
            dt(2020, 7, 6, 13, 0, 0, 0),
            dt(2020, 7, 13, 13, 0, 0, 0),
            dt(2020, 7, 20, 13, 0, 0, 0),
            dt(2020, 7, 27, 13, 0, 0, 0),
            dt(2020, 8, 3, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on monday, or 28th
        ["cron(0 13 28 * 1)"],
        [
            dt(2020, 7, 6, 13, 0, 0, 0),
            dt(2020, 7, 13, 13, 0, 0, 0),
            dt(2020, 7, 20, 13, 0, 0, 0),
            dt(2020, 7, 27, 13, 0, 0, 0),
            dt(2020, 7, 28, 13, 0, 0, 0),
            dt(2020, 8, 3, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on sunday and tuesday in feb & dec
        "cron(0 13 1 2,12 0,2)",
        [
            dt(2020, 12, 1, 13, 0, 0, 0),
            dt(2020, 12, 6, 13, 0, 0, 0),
            dt(2020, 12, 8, 13, 0, 0, 0),
            dt(2020, 12, 13, 13, 0, 0, 0),
            dt(2020, 12, 15, 13, 0, 0, 0),
            dt(2020, 12, 20, 13, 0, 0, 0),
            dt(2020, 12, 22, 13, 0, 0, 0),
            dt(2020, 12, 27, 13, 0, 0, 0),
            dt(2020, 12, 29, 13, 0, 0, 0),
            dt(2021, 2, 1, 13, 0, 0, 0),
            dt(2021, 2, 2, 13, 0, 0, 0),
            dt(2021, 2, 7, 13, 0, 0, 0),
            dt(2021, 2, 9, 13, 0, 0, 0),
            dt(2021, 2, 14, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on 29th of Jan & Feb (only leap years)
        "cron(0 13 29 1,2 *)",
        [
            dt(2021, 1, 29, 13, 0, 0, 0),
            dt(2022, 1, 29, 13, 0, 0, 0),
            dt(2023, 1, 29, 13, 0, 0, 0),
            dt(2024, 1, 29, 13, 0, 0, 0),
            dt(2024, 2, 29, 13, 0, 0, 0),
            dt(2025, 1, 29, 13, 0, 0, 0),
            dt(2026, 1, 29, 13, 0, 0, 0),
        ],
    ],
    [
        # 1pm on 33rd of Jan & Feb -> no such time
        "cron(0 13 33 1,2 *)",
        [None],
    ],
]


@pytest.mark.asyncio
async def test_timer_trigger_next_month_rollover(hass):
    """Run month rollover tests."""

    Function.init(hass)
    TrigTime.init(hass)

    for test_data in timerTriggerNextTestsMonthRollover:
        startup_time = now = dt(2020, 6, 30, 13, 0, 0, 100000)
        spec, expect_seq = test_data
        for expect in expect_seq:
            t_next, _ = await TrigTime.timer_trigger_next(spec, now, startup_time)
            assert t_next == expect
            if t_next is None:
                break
            now = t_next + timedelta(microseconds=1)
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


timerTriggerDSTNextTests = [
    [
        ["cron(0 0 * * *)"],
        [
            [dt(2019, 11, 4, 0, 0, 0, 0), 90000],
            [dt(2019, 11, 5, 0, 0, 0, 0), 86400],
            [dt(2019, 11, 6, 0, 0, 0, 0), 86400],
        ],
    ],
    [
        ["cron(0 * * * *)"],
        [
            [dt(2019, 11, 3, 1, 0, 0, 0), 3600],
            [dt(2019, 11, 3, 2, 0, 0, 0), 7200],
            [dt(2019, 11, 3, 3, 0, 0, 0), 3600],
            [dt(2019, 11, 3, 4, 0, 0, 0), 3600],
        ],
    ],
    [
        ["cron(1 * * * *)"],
        [
            [dt(2019, 11, 3, 0, 1, 0, 0), 60],
            [dt(2019, 11, 3, 1, 1, 0, 0), 3600],
            [dt(2019, 11, 3, 2, 1, 0, 0), 7200],
            [dt(2019, 11, 3, 3, 1, 0, 0), 3600],
            [dt(2019, 11, 3, 4, 1, 0, 0), 3600],
        ],
    ],
    [
        ["cron(1 * 8,9,10 3 *)"],
        [
            dt(2020, 3, 8, 0, 1, 0, 0),
            [dt(2020, 3, 8, 1, 1, 0, 0), 3600],
            [dt(2020, 3, 8, 2, 1, 0, 0), 3600],
            [dt(2020, 3, 8, 4, 1, 0, 0), 3600],
            [dt(2020, 3, 8, 5, 1, 0, 0), 3600],
        ],
    ],
]


@pytest.mark.asyncio
async def test_timer_trigger_dst_next(hass):
    """Run trigger next tests."""
    #
    # Hardcode a location and timezone so we can check sunrise
    # and sunset.
    #
    hass.config.latitude = 38
    hass.config.longitude = -122
    hass.config.elevation = 0
    hass.config.time_zone = "America/Los_Angeles"

    Function.init(hass)
    TrigTime.init(hass)

    for test_data in timerTriggerDSTNextTests:
        startup_time = now = dt(2019, 11, 3, 0, 0, 0, 0)
        spec, expect_seq = test_data
        for expect in expect_seq:
            t_next, t_next_adj = await TrigTime.timer_trigger_next(spec, now, startup_time)
            delta = (t_next_adj - now).total_seconds()
            # print(f"calling timer_trigger_next({spec}, {dt_util.as_local(now)}, {startup_time}) -> {dt_util.as_local(t_next)} (delta = {delta})")
            if isinstance(expect, list):
                assert t_next == expect[0]
                assert delta == expect[1]
            else:
                assert t_next == expect
            if t_next is None:
                break
            now = t_next
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


TRIGGER_EXPR_STRIP_TESTS = [
    ("x == 1", "x == 1"),
    (" x == 1", "x == 1"),
    ("x == 1 ", "x == 1"),
    ("\nx == 1\n", "x == 1"),
    ("\t x == 1 \t\r\n", "x == 1"),
    ("\n  (x == 1 and\n   y == 2)\n", "(x == 1 and\n   y == 2)"),
    ("", ""),
    ("  \n ", ""),
]


@pytest.mark.parametrize("expr, expected", TRIGGER_EXPR_STRIP_TESTS)
def test_strip_trigger_expr(expr, expected):
    """Surrounding blanks go; the inside of the expression is untouched."""
    assert strip_trigger_expr(expr) == expected


def test_strip_trigger_expr_not_str():
    """Values that aren't strings are left for the argument validation."""
    assert strip_trigger_expr(None) is None
    assert strip_trigger_expr(3) == 3


@pytest.mark.parametrize("expr, expected", TRIGGER_EXPR_STRIP_TESTS[1:6])
def test_strip_trigger_expr_matches_cpython(expr, expected):
    """Python rejects the unstripped text; the stripped text parses the same as the plain expression."""
    if expr[0] in " \t":
        with pytest.raises(IndentationError):
            ast_module.parse(expr, mode="eval")
    assert ast_module.dump(ast_module.parse(strip_trigger_expr(expr), mode="eval")) == ast_module.dump(
        ast_module.parse(expected, mode="eval")
    )


@pytest.fixture
async def trigger_ctx(hass):
    """Return an AstEval for parsing trigger expressions."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: False})}
    Function.init(hass)
    yield AstEval("test", global_ctx=GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr))
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


# one line after the blanks around it are stripped: parsed exactly as before, accepted or not
TRIGGER_EXPR_ONE_LINE = [
    " x == 1",
    "\n x == 1 \t\r\n",
    "x == 1  # comment\n",
    "a, b",
    "\na, b\n",
    "(x := 1)",
    "x := 1",
    "\n  x := 1\n",
    "x == 1 for x in y",
    "a) or (b",
    "a b",
    " a +  \n",
    "a\\\n",
    "  # comment\n",
    "  \n ",
    "é == 1 b",
]


@pytest.mark.parametrize("expr", TRIGGER_EXPR_ONE_LINE)
async def test_parse_trigger_expr_one_line(trigger_ctx, expr):
    """A one-line trigger expression is stripped and parsed as before, with Python's exact errors."""
    stripped = expr.strip(" \t\r\n")
    try:
        expected = ast_module.dump(ast_module.parse(stripped, mode="eval"), include_attributes=True)
    except SyntaxError as exc:
        with pytest.raises(SyntaxError) as ours:
            trigger_ctx.parse_trigger_expr(expr)
        assert (ours.value.msg, ours.value.lineno, ours.value.offset, ours.value.text) == (
            exc.msg,
            exc.lineno,
            exc.offset,
            exc.text,
        )
        return
    trigger_ctx.parse_trigger_expr(expr)
    assert ast_module.dump(trigger_ctx.ast, include_attributes=True) == expected
    assert trigger_ctx.code_str == stripped


# several lines: read as if inside brackets; the user's own brackets work as in Python
TRIGGER_EXPR_SEVERAL_LINES = [
    "\n{_s} in ['a', 'b'] and \n(sensor.t0 == 'unavailable' or float(sensor.t0) <= 110) and \n(sensor.t1 <= 110)\n",
    "a and  # first\n  b  # last\n",
    "# before\na and\nb",
    "\n  a and\n  b\n\n",
    "a and \\\n b",
    "a and\r\n b",
    "a and\r b",
    "é == 'à' and\n  f(b) > 1",
    "f'{a})' == 'x)' and\n b",
    "lambda x:\n x",
    "(x := 1) and\n b",
]
TRIGGER_EXPR_BRACKETED = [
    "(x == 1 for x in y)",
    "any(x == 1\n for x in y)",
    "(a,\n b)",
    "()",
    "(x := 1)",
    "(yield 1)",
    "(yield from a)",
]


def trigger_expr_variants(exprs):
    """Each expression after a comment line, and indented before a comment line."""
    return [
        variant
        for expr in exprs
        for variant in ("# comment\n" + expr, "\n    " + expr.replace("\n", "\n    ") + "\n    # comment\n")
    ]


@pytest.mark.parametrize("expr", TRIGGER_EXPR_SEVERAL_LINES + trigger_expr_variants(TRIGGER_EXPR_BRACKETED))
async def test_parse_trigger_expr_several_lines(trigger_ctx, expr):
    """A trigger expression over several lines parses as inside brackets, and each part keeps its place."""
    trigger_ctx.parse_trigger_expr(expr)
    wrapped = ast_module.parse("(" + expr + "\n)", mode="eval")
    assert ast_module.dump(trigger_ctx.ast) == ast_module.dump(wrapped)
    assert trigger_ctx.code_str == expr
    for ours, theirs in zip(
        ast_module.walk(trigger_ctx.ast.body), ast_module.walk(wrapped.body), strict=True
    ):
        if hasattr(ours, "lineno"):
            assert ours.lineno == theirs.lineno
            # the same text, read from the expression as written
            assert ast_module.get_source_segment(expr, ours) == ast_module.get_source_segment(
                "(" + expr + "\n)", theirs
            )


# several lines, valid only because of the added brackets: each is an error
TRIGGER_EXPR_NEEDS_BRACKETS = [
    ("x == 1\n for x in y", "x == 1"),
    ("a,\n b", "a,"),
    ("*a,\n b", "*a,"),
    ("a and\n b) or (c", "a and"),
    ("x :=\n 1", "x :="),
    ("yield\n 1", "yield"),
    ("yield from\n a", "yield from"),
]


@pytest.mark.parametrize(
    "expr, text",
    [
        (variant, text)
        for expr, text in TRIGGER_EXPR_NEEDS_BRACKETS
        for variant in trigger_expr_variants([expr])
    ],
)
async def test_parse_trigger_expr_needs_brackets(trigger_ctx, expr, text):
    """Over several lines, what would need the added brackets is a SyntaxError at its start."""
    with pytest.raises(SyntaxError) as exc:
        trigger_ctx.parse_trigger_expr(expr)
    assert trigger_ctx.ast is None
    lines = expr.split("\n")
    lineno = 2 if lines[0] in ("# comment", "") else 1
    assert (exc.value.lineno, exc.value.text.strip()) == (lineno, text)
    assert exc.value.offset == len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip()) + 1
    assert exc.value.args[1][1:] == (
        exc.value.lineno,
        exc.value.offset,
        exc.value.text,
        exc.value.end_lineno,
        exc.value.end_offset,
    )


@pytest.mark.parametrize("expr", ["# only a comment\n# and another", "\n  # nothing\n\n"])
async def test_parse_trigger_expr_empty(trigger_ctx, expr):
    """An expression of only comments over several lines is a SyntaxError, as it is on one line."""
    with pytest.raises(SyntaxError):
        trigger_ctx.parse_trigger_expr(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "a b and\n c",
        "é b and\n c",
        "\n a and\n b c\n",
        "\n a ==\n b = 1",
        "a and\n b +\n\n",
        "\n a + (b and\n c",
        "a and\r b +\r",
        "a and\r\n b +\r\n",
    ],
)
async def test_parse_trigger_expr_syntax_error(trigger_ctx, expr):
    """A syntax error over several lines is at the same place as in brackets, on the text as written."""
    with pytest.raises(SyntaxError) as cpython_exc:
        ast_module.parse("(" + expr + "\n)", mode="eval")
    with pytest.raises(SyntaxError) as exc:
        trigger_ctx.parse_trigger_expr(expr)
    lines = expr.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lineno, offset = cpython_exc.value.lineno, cpython_exc.value.offset
    # Python's line with the added ")" (a lone "\r" at the end joins its "\n")
    closer = len(("(" + expr + "\n)").replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    if lineno == closer:
        # stopped short: at the last character of the last line that isn't blank
        lineno = max(idx for idx, line in enumerate(lines, 1) if line.strip())
        offset = len(lines[lineno - 1].rstrip())
    elif lineno == 1:
        # the added "(" is gone; a bracket left open is blamed on it, at the start of the first line
        offset = max(offset - 1, 1)
    assert (exc.value.lineno, exc.value.offset, exc.value.text) == (lineno, offset, lines[lineno - 1])
    assert (exc.value.end_lineno, exc.value.end_offset) > (exc.value.lineno, exc.value.offset)
    assert exc.value.args[1][1:4] == (exc.value.lineno, exc.value.offset, exc.value.text)


# valid once stripped, as before this change, although they only look like tuples needing brackets
TRIGGER_EXPR_STILL_VALID = [
    "a, b\n# trailing comment",
    "a, (\n b\n)",
    "a, \\\n b",
    "a, '''x\ny'''",
    "\n  a, b\n  # comment\n",
    "a, (b,\n c)  # c\n# d",
    "(a\n), b",
]


@pytest.mark.parametrize("expr", TRIGGER_EXPR_STILL_VALID)
async def test_parse_trigger_expr_still_valid(trigger_ctx, expr):
    """What parsed before (stripped) still parses, to the same tree with the same positions."""
    stripped = expr.strip(" \t\r\n")
    trigger_ctx.parse_trigger_expr(expr)
    assert ast_module.dump(trigger_ctx.ast, include_attributes=True) == ast_module.dump(
        ast_module.parse(stripped, mode="eval"), include_attributes=True
    )
    assert trigger_ctx.code_str == stripped


TRIGGER_EXPR_PIECES = [
    "a", "b", "1", ", ", " ", "\n", "\r", "\r\n", "\t", "(", ")", "[", "]", " and ", " or ", " == ", "*",
    " for a in b", " if a else b", " := ", "yield ", "# c\n", "\\\n", "\'\'\'x\ny\'\'\'", "not ", "lambda: ",
]  # fmt: skip


async def test_parse_trigger_expr_fuzz(trigger_ctx):
    """For random text, anything that parsed before (stripped) still parses, to the same tree."""
    rand = random.Random(617)  # noqa: S311 - a repeatable sample, not a secret
    valid = several_lines = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # like "1not"
        for _ in range(20000):
            expr = "".join(rand.choice(TRIGGER_EXPR_PIECES) for _ in range(rand.randint(1, 8)))
            stripped = expr.strip(" \t\r\n")
            try:
                expected = ast_module.dump(ast_module.parse(stripped, mode="eval"))
            except SyntaxError:
                continue
            valid += 1
            several_lines += "\n" in stripped or "\r" in stripped
            trigger_ctx.parse_trigger_expr(expr)
            assert ast_module.dump(trigger_ctx.ast) == expected, repr(expr)
    assert valid > 1000 and several_lines > 100
