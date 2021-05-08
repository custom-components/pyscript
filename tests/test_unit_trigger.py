"""Unit tests for time trigger functions."""

from datetime import datetime as dt, timedelta
from unittest.mock import patch

from custom_components.pyscript.function import Function
from custom_components.pyscript.trigger import TrigTime
import pytest

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

    with patch("homeassistant.helpers.condition.dt_util.utcnow", return_value=now), patch(
        "homeassistant.util.dt.utcnow", return_value=now
    ):
        for test_data in parseDateTimeTests:
            spec, date_offset, expect = test_data
            out = TrigTime.parse_date_time(spec, date_offset, now, now)
            assert out == expect


parseDateTimeTestsDayNames = [
    ["thu", 0, dt(2019, 9, 5, 0, 0, 0, 0)],
    ["fri noon", 0, dt(2019, 9, 6, 12, 0, 0, 0)],
    ["sunday", 0, dt(2019, 9, 8, 0, 0, 0, 0)],
    ["monday", 0, dt(2019, 9, 9, 0, 0, 0, 0)],
]


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
    with patch("homeassistant.helpers.condition.dt_util.utcnow", return_value=now), patch(
        "homeassistant.util.dt.utcnow", return_value=now
    ):
        for test_data in parseDateTimeTestsDayNames:
            spec, date_offset, expect = test_data
            out = TrigTime.parse_date_time(spec, date_offset, now, now)
            assert out == expect


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
def test_timer_active_check(hass, spec, now, expected):
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
    print(f"calling timer_active_check({spec}, {now}, {startup_time})")
    out = TrigTime.timer_active_check(spec, now, startup_time)
    assert out == expected


timerTriggerNextTests = [
    [["once(2019/9/1 8:00)"], [None]],
    [["once(2019/9/1 15:00)"], [dt(2019, 9, 1, 15, 0, 0, 0)]],
    [["once(15:00)"], [dt(2019, 9, 1, 15, 0, 0, 0)]],
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


def test_timer_trigger_next(hass):
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
            print(f"calling timer_trigger_next({spec}, {now}, {startup_time})")
            t_next = TrigTime.timer_trigger_next(spec, now, startup_time)
            assert t_next == expect
            if t_next is None:
                break
            now = t_next + timedelta(microseconds=1)


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


def test_timer_trigger_next_month_rollover(hass):
    """Run month rollover tests."""

    Function.init(hass)
    TrigTime.init(hass)

    for test_data in timerTriggerNextTestsMonthRollover:
        startup_time = now = dt(2020, 6, 30, 13, 0, 0, 100000)
        spec, expect_seq = test_data
        for expect in expect_seq:
            t_next = TrigTime.timer_trigger_next(spec, now, startup_time)
            assert t_next == expect
            now = t_next
