"""Implements all the trigger logic."""

import asyncio
import datetime as dt
import functools
import locale
import logging
import math
import re
import time

from croniter import croniter

from homeassistant.core import Context
from homeassistant.helpers import sun
from homeassistant.util import dt as dt_util

from .const import LOGGER_PATH
from .eval import AstEval, EvalFunc, EvalFuncVar
from .event import Event
from .function import Function
from .mqtt import Mqtt
from .state import STATE_VIRTUAL_ATTRS, State
from .webhook import Webhook

_LOGGER = logging.getLogger(LOGGER_PATH + ".trigger")


STATE_RE = re.compile(r"\w+\.\w+(\.((\w+)|\*))?$")


def dt_now():
    """Return current time."""
    return dt.datetime.now()


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


def ident_any_values_changed(func_args, ident):
    """Check for any changes to state or attributes on ident vars."""
    var_name = func_args.get("var_name", None)

    if var_name is None:
        return False
    value = func_args["value"]
    old_value = func_args["old_value"]

    for check_var in ident:
        if check_var == var_name and old_value != value:
            return True

        if check_var.startswith(f"{var_name}."):
            var_pieces = check_var.split(".")
            if len(var_pieces) == 3 and f"{var_pieces[0]}.{var_pieces[1]}" == var_name:
                if var_pieces[2] == "*":
                    # catch all has been requested, check all attributes for change
                    all_attrs = set()
                    if value is not None:
                        all_attrs |= set(value.__dict__.keys())
                    if old_value is not None:
                        all_attrs |= set(old_value.__dict__.keys())
                    for attr in all_attrs - STATE_VIRTUAL_ATTRS:
                        if getattr(value, attr, None) != getattr(old_value, attr, None):
                            return True
                elif getattr(value, var_pieces[2], None) != getattr(old_value, var_pieces[2], None):
                    return True

    return False


def ident_values_changed(func_args, ident):
    """Check for changes to state or attributes on ident vars."""
    var_name = func_args.get("var_name", None)

    if var_name is None:
        return False
    value = func_args["value"]
    old_value = func_args["old_value"]

    for check_var in ident:
        var_pieces = check_var.split(".")
        if len(var_pieces) < 2 or len(var_pieces) > 3:
            continue
        var_root = f"{var_pieces[0]}.{var_pieces[1]}"
        if var_root == var_name and (len(var_pieces) == 2 or var_pieces[2] == "old"):
            if value != old_value:
                return True
        elif len(var_pieces) == 3 and var_root == var_name:
            if getattr(value, var_pieces[2], None) != getattr(old_value, var_pieces[2], None):
                return True

    return False


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
    dow2int = {}

    def __init__(self):
        """Warn on TrigTime instantiation."""
        _LOGGER.error("TrigTime class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize TrigTime."""
        cls.hass = hass

        def wait_until_factory(ast_ctx):
            """Return wapper to call to astFunction with the ast context."""

            async def wait_until_call(*arg, **kw):
                return await cls.wait_until(ast_ctx, *arg, **kw)

            return wait_until_call

        def user_task_create_factory(ast_ctx):
            """Return wapper to call to astFunction with the ast context."""

            async def user_task_create(func, *args, **kwargs):
                """Implement task.create()."""

                async def func_call(func, func_name, new_ast_ctx, *args, **kwargs):
                    """Call user function inside task.create()."""
                    ret = await new_ast_ctx.call_func(func, func_name, *args, **kwargs)
                    if new_ast_ctx.get_exception_obj():
                        new_ast_ctx.get_logger().error(new_ast_ctx.get_exception_long())
                    return ret

                try:
                    if isinstance(func, (EvalFunc, EvalFuncVar)):
                        func_name = func.get_name()
                    else:
                        func_name = func.__name__
                except Exception:
                    func_name = "<function>"

                new_ast_ctx = AstEval(
                    f"{ast_ctx.get_global_ctx_name()}.{func_name}", ast_ctx.get_global_ctx()
                )
                Function.install_ast_funcs(new_ast_ctx)
                task = Function.create_task(
                    func_call(func, func_name, new_ast_ctx, *args, **kwargs), ast_ctx=new_ast_ctx
                )
                Function.task_done_callback_ctx(task, new_ast_ctx)
                return task

            return user_task_create

        ast_funcs = {
            "task.wait_until": wait_until_factory,
            "task.create": user_task_create_factory,
        }
        Function.register_ast(ast_funcs)

        async def user_task_add_done_callback(task, callback, *args, **kwargs):
            """Implement task.add_done_callback()."""
            ast_ctx = None
            if type(callback) is EvalFuncVar:
                ast_ctx = callback.get_ast_ctx()
            Function.task_add_done_callback(task, ast_ctx, callback, *args, **kwargs)

        funcs = {
            "task.add_done_callback": user_task_add_done_callback,
            "task.executor": cls.user_task_executor,
        }
        Function.register(funcs)

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
    async def wait_until(
        cls,
        ast_ctx,
        state_trigger=None,
        state_check_now=True,
        time_trigger=None,
        event_trigger=None,
        mqtt_trigger=None,
        webhook_trigger=None,
        webhook_local_only=True,
        webhook_methods=None,
        timeout=None,
        state_hold=None,
        state_hold_false=None,
        __test_handshake__=None,
    ):
        """Wait for zero or more triggers, until an optional timeout."""
        if (
            state_trigger is None
            and time_trigger is None
            and event_trigger is None
            and mqtt_trigger is None
            and webhook_trigger is None
        ):
            if timeout is not None:
                await asyncio.sleep(timeout)
                return {"trigger_type": "timeout"}
            return {"trigger_type": "none"}
        state_trig_ident = set()
        state_trig_ident_any = set()
        state_trig_eval = None
        event_trig_expr = None
        mqtt_trig_expr = None
        webhook_trig_expr = None
        exc = None
        notify_q = asyncio.Queue(0)

        last_state_trig_time = None
        state_trig_waiting = False
        state_trig_notify_info = [None, None]
        state_false_time = None
        check_state_expr_on_start = state_check_now or state_hold_false is not None

        if state_trigger is not None:
            state_trig = []
            if isinstance(state_trigger, str):
                state_trigger = [state_trigger]
            elif isinstance(state_trigger, set):
                state_trigger = list(state_trigger)
            #
            # separate out the entries that are just state var names, which mean trigger
            # on any change (no expr)
            #
            for trig in state_trigger:
                if STATE_RE.match(trig):
                    state_trig_ident_any.add(trig)
                else:
                    state_trig.append(trig)

            if len(state_trig) > 0:
                if len(state_trig) == 1:
                    state_trig_expr = state_trig[0]
                else:
                    state_trig_expr = f"any([{', '.join(state_trig)}])"
                state_trig_eval = AstEval(
                    f"{ast_ctx.name} state_trigger",
                    ast_ctx.get_global_ctx(),
                    logger_name=ast_ctx.get_logger_name(),
                )
                Function.install_ast_funcs(state_trig_eval)
                state_trig_eval.parse(state_trig_expr, mode="eval")
                state_trig_ident = await state_trig_eval.get_names()
                exc = state_trig_eval.get_exception_obj()
                if exc is not None:
                    raise exc

            state_trig_ident.update(state_trig_ident_any)
            if check_state_expr_on_start and state_trig_eval:
                #
                # check straight away to see if the condition is met
                #
                new_vars = State.notify_var_get(state_trig_ident, {})
                state_trig_ok = await state_trig_eval.eval(new_vars)
                exc = state_trig_eval.get_exception_obj()
                if exc is not None:
                    raise exc
                if state_hold_false is not None and not state_check_now:
                    #
                    # if state_trig_ok we wait until it is false;
                    # otherwise we consider now to be the start of the false hold time
                    #
                    state_false_time = None if state_trig_ok else time.monotonic()
                elif state_hold is not None and state_trig_ok:
                    state_trig_waiting = True
                    state_trig_notify_info = [None, {"trigger_type": "state"}]
                    last_state_trig_time = time.monotonic()
                    _LOGGER.debug(
                        "trigger %s wait_until: state trigger immediately true; now waiting for state_hold of %g seconds",
                        ast_ctx.name,
                        state_hold,
                    )
                elif state_trig_ok:
                    return {"trigger_type": "state"}

            _LOGGER.debug(
                "trigger %s wait_until: watching vars %s",
                ast_ctx.name,
                state_trig_ident,
            )
            if len(state_trig_ident) > 0:
                await State.notify_add(state_trig_ident, notify_q)
        if event_trigger is not None:
            if isinstance(event_trigger, str):
                event_trigger = [event_trigger]
            if len(event_trigger) > 1:
                event_trig_expr = AstEval(
                    f"{ast_ctx.name} event_trigger",
                    ast_ctx.get_global_ctx(),
                    logger_name=ast_ctx.get_logger_name(),
                )
                Function.install_ast_funcs(event_trig_expr)
                event_trig_expr.parse(event_trigger[1], mode="eval")
                exc = event_trig_expr.get_exception_obj()
                if exc is not None:
                    if len(state_trig_ident) > 0:
                        State.notify_del(state_trig_ident, notify_q)
                    raise exc
            Event.notify_add(event_trigger[0], notify_q)
        if mqtt_trigger is not None:
            if isinstance(mqtt_trigger, str):
                mqtt_trigger = [mqtt_trigger]
            if len(mqtt_trigger) > 1:
                mqtt_trig_expr = AstEval(
                    f"{ast_ctx.name} mqtt_trigger",
                    ast_ctx.get_global_ctx(),
                    logger_name=ast_ctx.get_logger_name(),
                )
                Function.install_ast_funcs(mqtt_trig_expr)
                mqtt_trig_expr.parse(mqtt_trigger[1], mode="eval")
                exc = mqtt_trig_expr.get_exception_obj()
                if exc is not None:
                    if len(state_trig_ident) > 0:
                        State.notify_del(state_trig_ident, notify_q)
                    raise exc
            await Mqtt.notify_add(mqtt_trigger[0], notify_q)
        if webhook_trigger is not None:
            if isinstance(webhook_trigger, str):
                webhook_trigger = [webhook_trigger]
            if len(webhook_trigger) > 1:
                webhook_trig_expr = AstEval(
                    f"{ast_ctx.name} webhook_trigger",
                    ast_ctx.get_global_ctx(),
                    logger_name=ast_ctx.get_logger_name(),
                )
                Function.install_ast_funcs(webhook_trig_expr)
                webhook_trig_expr.parse(webhook_trigger[1], mode="eval")
                exc = webhook_trig_expr.get_exception_obj()
                if exc is not None:
                    if len(state_trig_ident) > 0:
                        State.notify_del(state_trig_ident, notify_q)
                    raise exc
            if webhook_methods is None:
                webhook_methods = {"POST", "PUT"}
            Webhook.notify_add(webhook_trigger[0], webhook_local_only, webhook_methods, notify_q)

        time0 = time.monotonic()

        if __test_handshake__:
            #
            # used for testing to avoid race conditions
            # we use this as a handshake that we are about to
            # listen to the queue
            #
            State.set(__test_handshake__[0], __test_handshake__[1])

        while True:
            ret = None
            this_timeout = None
            state_trig_timeout = False
            time_next = None
            startup_time = None
            now = dt_now()
            if startup_time is None:
                startup_time = now
            if time_trigger is not None:
                time_next, time_next_adj = await cls.timer_trigger_next(time_trigger, now, startup_time)
                _LOGGER.debug(
                    "trigger %s wait_until time_next = %s, now = %s",
                    ast_ctx.name,
                    time_next,
                    now,
                )
                if time_next is not None:
                    this_timeout = (time_next_adj - now).total_seconds()
            if timeout is not None:
                time_left = time0 + timeout - time.monotonic()
                if time_left <= 0:
                    ret = {"trigger_type": "timeout"}
                    break
                if this_timeout is None or this_timeout > time_left:
                    ret = {"trigger_type": "timeout"}
                    this_timeout = time_left
                    time_next = now + dt.timedelta(seconds=this_timeout)
            if state_trig_waiting:
                time_left = last_state_trig_time + state_hold - time.monotonic()
                if this_timeout is None or time_left < this_timeout:
                    this_timeout = time_left
                    state_trig_timeout = True
                    time_next = now + dt.timedelta(seconds=this_timeout)
            if this_timeout is None:
                if (
                    state_trigger is None
                    and event_trigger is None
                    and mqtt_trigger is None
                    and webhook_trigger is None
                ):
                    _LOGGER.debug(
                        "trigger %s wait_until no next time - returning with none",
                        ast_ctx.name,
                    )
                    ret = {"trigger_type": "none"}
                    break
                _LOGGER.debug("trigger %s wait_until no timeout", ast_ctx.name)
                notify_type, notify_info = await notify_q.get()
            else:
                timeout_occured = False
                while True:
                    try:
                        this_timeout = max(0, this_timeout)
                        _LOGGER.debug("trigger %s wait_until %.6g secs", ast_ctx.name, this_timeout)
                        notify_type, notify_info = await asyncio.wait_for(
                            notify_q.get(), timeout=this_timeout
                        )
                        state_trig_timeout = False
                    except asyncio.TimeoutError:
                        actual_now = dt_now()
                        if actual_now < time_next:
                            this_timeout = (time_next - actual_now).total_seconds()
                            # tests/tests_function's simple now() requires us to ignore
                            # timeouts that are up to 1us too early; otherwise wait for
                            # longer until we are sure we are at or past time_next
                            if this_timeout > 1e-6:
                                continue
                        if not state_trig_timeout:
                            if not ret:
                                ret = {"trigger_type": "time"}
                                if time_next is not None:
                                    ret["trigger_time"] = time_next
                            timeout_occured = True
                    break
                if timeout_occured:
                    break
            if state_trig_timeout:
                ret = state_trig_notify_info[1]
                state_trig_waiting = False
                break
            if notify_type == "state":
                if notify_info:
                    new_vars, func_args = notify_info
                else:
                    new_vars, func_args = None, {}

                state_trig_ok = True

                if not ident_any_values_changed(func_args, state_trig_ident_any):
                    # if var_name not in func_args we are state_check_now
                    if "var_name" in func_args and not ident_values_changed(func_args, state_trig_ident):
                        continue

                    if state_trig_eval:
                        state_trig_ok = await state_trig_eval.eval(new_vars)
                        exc = state_trig_eval.get_exception_obj()
                        if exc is not None:
                            break

                        if state_hold_false is not None:
                            if state_false_time is None:
                                if state_trig_ok:
                                    #
                                    # wasn't False, so ignore
                                    #
                                    continue
                                #
                                # first False, so remember when it is
                                #
                                state_false_time = time.monotonic()
                            elif state_trig_ok:
                                too_soon = time.monotonic() - state_false_time < state_hold_false
                                state_false_time = None
                                if too_soon:
                                    #
                                    # was False but not for long enough, so start over
                                    #
                                    continue

                if state_hold is not None:
                    if state_trig_ok:
                        if not state_trig_waiting:
                            state_trig_waiting = True
                            state_trig_notify_info = notify_info
                            last_state_trig_time = time.monotonic()
                            _LOGGER.debug(
                                "trigger %s wait_until: got %s trigger; now waiting for state_hold of %g seconds",
                                notify_type,
                                ast_ctx.name,
                                state_hold,
                            )
                        else:
                            _LOGGER.debug(
                                "trigger %s wait_until: got %s trigger; still waiting for state_hold of %g seconds",
                                notify_type,
                                ast_ctx.name,
                                state_hold,
                            )
                        continue
                    if state_trig_waiting:
                        state_trig_waiting = False
                        _LOGGER.debug(
                            "trigger %s wait_until: %s trigger now false during state_hold; waiting for new trigger",
                            notify_type,
                            ast_ctx.name,
                        )
                        continue
                if state_trig_ok:
                    ret = notify_info[1] if notify_info else None
                    break
            elif notify_type == "event":
                if event_trig_expr is None:
                    ret = notify_info
                    break
                event_trig_ok = await event_trig_expr.eval(notify_info)
                exc = event_trig_expr.get_exception_obj()
                if exc is not None:
                    break
                if event_trig_ok:
                    ret = notify_info
                    break
            elif notify_type == "mqtt":
                if mqtt_trig_expr is None:
                    ret = notify_info
                    break
                mqtt_trig_ok = await mqtt_trig_expr.eval(notify_info)
                exc = mqtt_trig_expr.get_exception_obj()
                if exc is not None:
                    break
                if mqtt_trig_ok:
                    ret = notify_info
                    break
            elif notify_type == "webhook":
                if webhook_trig_expr is None:
                    ret = notify_info
                    break
                webhook_trig_ok = await webhook_trig_expr.eval(notify_info)
                exc = webhook_trig_expr.get_exception_obj()
                if exc is not None:
                    break
                if webhook_trig_ok:
                    ret = notify_info
                    break
            else:
                _LOGGER.error(
                    "trigger %s wait_until got unexpected queue message %s",
                    ast_ctx.name,
                    notify_type,
                )

        if len(state_trig_ident) > 0:
            State.notify_del(state_trig_ident, notify_q)
        if event_trigger is not None:
            Event.notify_del(event_trigger[0], notify_q)
        if mqtt_trigger is not None:
            Mqtt.notify_del(mqtt_trigger[0], notify_q)
        if webhook_trigger is not None:
            Webhook.notify_del(webhook_trigger[0], notify_q)
        if exc:
            raise exc
        return ret

    @classmethod
    async def user_task_executor(cls, func, *args, **kwargs):
        """Implement task.executor()."""
        if asyncio.iscoroutinefunction(func) or not callable(func):
            raise TypeError(f"function {func} is not callable by task.executor")
        if isinstance(func, EvalFuncVar):
            raise TypeError(
                "pyscript functions can't be called from task.executor - must be a regular python function"
            )
        return await cls.hass.async_add_executor_job(functools.partial(func, **kwargs), *args)

    @classmethod
    async def parse_date_time(cls, date_time_str, day_offset, now, startup_time):
        """Parse a date time string, returning datetime."""
        year = now.year
        month = now.month
        day = now.day

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
            dt_str = dt_str[len(match0.group(0)) :]
        elif match1:
            skip = True
            if match1[1] in cls.dow2int:
                dow = cls.dow2int[match1[1]]
                if dow >= (now.isoweekday() % 7):
                    day_offset = dow - (now.isoweekday() % 7)
                else:
                    day_offset = 7 + dow - (now.isoweekday() % 7)
            elif match1[1] == "today":
                day_offset = 0
            elif match1[1] == "tomorrow":
                day_offset = 1
            else:
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
            return now

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
        elif dt_str.startswith("sunrise") or dt_str.startswith("sunset"):
            location = sun.get_astral_location(cls.hass)
            if isinstance(location, tuple):
                # HA core-2021.5.0 included this breaking change: https://github.com/home-assistant/core/pull/48573.
                # As part of the upgrade to astral 2.2, sun.get_astral_location() now returns a tuple including the
                # elevation.  We just want the astral.location.Location object.
                location = location[0]
            try:
                if dt_str.startswith("sunrise"):
                    time_sun = await cls.hass.async_add_executor_job(
                        location.sunrise, dt.date(year, month, day)
                    )
                    dt_str = dt_str[7:]
                else:
                    time_sun = await cls.hass.async_add_executor_job(
                        location.sunset, dt.date(year, month, day)
                    )
                    dt_str = dt_str[6:]
            except Exception:
                _LOGGER.warning("'%s' not defined at this latitude", dt_str)
                # return something in the past so it is ignored
                return now - dt.timedelta(days=100)
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
        return now

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

                start = await cls.parse_date_time(dt_start.strip(), 0, now, startup_time)
                end = await cls.parse_date_time(dt_end.strip(), 0, start, startup_time)

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
                this_t = await cls.parse_date_time(match1[1].strip(), 0, now, startup_time)
                day_offset = (now - this_t).days + 1
                if day_offset != 0 and this_t != startup_time:
                    #
                    # Try a day offset (won't make a difference if spec has full date)
                    #
                    this_t = await cls.parse_date_time(match1[1].strip(), day_offset, now, startup_time)
                startup = now == this_t and now == startup_time
                if (now < this_t or startup) and (next_time is None or this_t < next_time):
                    next_time_adj = next_time = this_t

            elif len(match2) == 5:
                start_str, period_str = match2[1].strip(), match2[2].strip()
                start = await cls.parse_date_time(start_str, 0, now, startup_time)
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
                end = await cls.parse_date_time(end_str, 0, now, startup_time)
                end_offset = 1 if end < start else 0
                for day in [-1, 0, 1]:
                    start = await cls.parse_date_time(start_str, day, now, startup_time)
                    end = await cls.parse_date_time(end_str, day + end_offset, now, startup_time)
                    if now < start or (now == start and now == startup_time):
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


class TrigInfo:
    """Class for all trigger-decorated functions."""

    def __init__(
        self,
        name,
        trig_cfg,
        global_ctx=None,
    ):
        """Create a new TrigInfo."""
        self.name = name
        self.task = None
        self.global_ctx = global_ctx
        self.trig_cfg = trig_cfg
        self.state_trigger = trig_cfg.get("state_trigger", {}).get("args", None)
        self.state_trigger_kwargs = trig_cfg.get("state_trigger", {}).get("kwargs", {})
        self.state_hold = self.state_trigger_kwargs.get("state_hold", None)
        self.state_hold_false = self.state_trigger_kwargs.get("state_hold_false", None)
        self.state_check_now = self.state_trigger_kwargs.get("state_check_now", False)
        self.state_user_watch = self.state_trigger_kwargs.get("watch", None)
        self.time_trigger = trig_cfg.get("time_trigger", {}).get("args", None)
        self.time_trigger_kwargs = trig_cfg.get("time_trigger", {}).get("kwargs", {})
        self.event_trigger = trig_cfg.get("event_trigger", {}).get("args", None)
        self.event_trigger_kwargs = trig_cfg.get("event_trigger", {}).get("kwargs", {})
        self.mqtt_trigger = trig_cfg.get("mqtt_trigger", {}).get("args", None)
        self.mqtt_trigger_kwargs = trig_cfg.get("mqtt_trigger", {}).get("kwargs", {})
        self.webhook_trigger = trig_cfg.get("webhook_trigger", {}).get("args", None)
        self.webhook_trigger_kwargs = trig_cfg.get("webhook_trigger", {}).get("kwargs", {})
        self.webhook_local_only = self.webhook_trigger_kwargs.get("local_only", True)
        self.webhook_methods = self.webhook_trigger_kwargs.get("methods", {"POST", "PUT"})
        self.state_active = trig_cfg.get("state_active", {}).get("args", None)
        self.time_active = trig_cfg.get("time_active", {}).get("args", None)
        self.time_active_hold_off = trig_cfg.get("time_active", {}).get("kwargs", {}).get("hold_off", None)
        self.task_unique = trig_cfg.get("task_unique", {}).get("args", None)
        self.task_unique_kwargs = trig_cfg.get("task_unique", {}).get("kwargs", None)
        self.action = trig_cfg.get("action")
        self.global_sym_table = trig_cfg.get("global_sym_table", {})
        self.notify_q = asyncio.Queue(0)
        self.active_expr = None
        self.state_active_ident = None
        self.state_trig_expr = None
        self.state_trig_eval = None
        self.state_trig_ident = None
        self.state_trig_ident_any = set()
        self.event_trig_expr = None
        self.mqtt_trig_expr = None
        self.webhook_trig_expr = None
        self.have_trigger = False
        self.setup_ok = False
        self.run_on_startup = False
        self.run_on_shutdown = False

        if self.state_active is not None:
            self.active_expr = AstEval(
                f"{self.name} @state_active()", self.global_ctx, logger_name=self.name
            )
            Function.install_ast_funcs(self.active_expr)
            self.active_expr.parse(self.state_active, mode="eval")
            exc = self.active_expr.get_exception_long()
            if exc is not None:
                self.active_expr.get_logger().error(exc)
                return

        if "time_trigger" in trig_cfg and self.time_trigger is None:
            self.run_on_startup = True
        if self.time_trigger is not None:
            while "startup" in self.time_trigger:
                self.run_on_startup = True
                self.time_trigger.remove("startup")
            while "shutdown" in self.time_trigger:
                self.run_on_shutdown = True
                self.time_trigger.remove("shutdown")
            if len(self.time_trigger) == 0:
                self.time_trigger = None

        if self.state_trigger is not None:
            state_trig = []
            for triggers in self.state_trigger:
                if isinstance(triggers, str):
                    triggers = [triggers]
                elif isinstance(triggers, set):
                    triggers = list(triggers)
                #
                # separate out the entries that are just state var names, which mean trigger
                # on any change (no expr)
                #
                for trig in triggers:
                    if STATE_RE.match(trig):
                        self.state_trig_ident_any.add(trig)
                    else:
                        state_trig.append(trig)

            if len(state_trig) > 0:
                if len(state_trig) == 1:
                    self.state_trig_expr = state_trig[0]
                else:
                    self.state_trig_expr = f"any([{', '.join(state_trig)}])"
                self.state_trig_eval = AstEval(
                    f"{self.name} @state_trigger()", self.global_ctx, logger_name=self.name
                )
                Function.install_ast_funcs(self.state_trig_eval)
                self.state_trig_eval.parse(self.state_trig_expr, mode="eval")
                exc = self.state_trig_eval.get_exception_long()
                if exc is not None:
                    self.state_trig_eval.get_logger().error(exc)
                    return
            self.have_trigger = True

        if self.event_trigger is not None:
            if len(self.event_trigger) == 2:
                self.event_trig_expr = AstEval(
                    f"{self.name} @event_trigger()",
                    self.global_ctx,
                    logger_name=self.name,
                )
                Function.install_ast_funcs(self.event_trig_expr)
                self.event_trig_expr.parse(self.event_trigger[1], mode="eval")
                exc = self.event_trig_expr.get_exception_long()
                if exc is not None:
                    self.event_trig_expr.get_logger().error(exc)
                    return
            self.have_trigger = True

        if self.mqtt_trigger is not None:
            if len(self.mqtt_trigger) == 2:
                self.mqtt_trig_expr = AstEval(
                    f"{self.name} @mqtt_trigger()",
                    self.global_ctx,
                    logger_name=self.name,
                )
                Function.install_ast_funcs(self.mqtt_trig_expr)
                self.mqtt_trig_expr.parse(self.mqtt_trigger[1], mode="eval")
                exc = self.mqtt_trig_expr.get_exception_long()
                if exc is not None:
                    self.mqtt_trig_expr.get_logger().error(exc)
                    return
            self.have_trigger = True

        if self.webhook_trigger is not None:
            if len(self.webhook_trigger) == 2:
                self.webhook_trig_expr = AstEval(
                    f"{self.name} @webhook_trigger()",
                    self.global_ctx,
                    logger_name=self.name,
                )
                Function.install_ast_funcs(self.webhook_trig_expr)
                self.webhook_trig_expr.parse(self.webhook_trigger[1], mode="eval")
                exc = self.webhook_trig_expr.get_exception_long()
                if exc is not None:
                    self.webhook_trig_expr.get_logger().error(exc)
                    return
            self.have_trigger = True

        self.setup_ok = True

    def stop(self):
        """Stop this trigger task."""

        if self.task:
            if self.state_trig_ident:
                State.notify_del(self.state_trig_ident, self.notify_q)
            if self.event_trigger is not None:
                Event.notify_del(self.event_trigger[0], self.notify_q)
            if self.mqtt_trigger is not None:
                Mqtt.notify_del(self.mqtt_trigger[0], self.notify_q)
            if self.webhook_trigger is not None:
                Webhook.notify_del(self.webhook_trigger[0], self.notify_q)
            if self.task:
                Function.reaper_cancel(self.task)
                self.task = None
        if self.run_on_shutdown:
            notify_type = "shutdown"
            notify_info = {"trigger_type": "time", "trigger_time": "shutdown"}
            notify_info.update(self.time_trigger_kwargs.get("kwargs", {}))
            action_future = self.call_action(notify_type, notify_info, run_task=False)
            Function.waiter_await(action_future)

    def start(self):
        """Start this trigger task."""
        if not self.task and self.setup_ok:
            self.task = Function.create_task(self.trigger_watch())
            _LOGGER.debug("trigger %s is active", self.name)

    async def trigger_watch(self):
        """Task that runs for each trigger, waiting for the next trigger and calling the function."""

        try:

            if self.state_trigger is not None:
                self.state_trig_ident = set()
                if self.state_user_watch:
                    if isinstance(self.state_user_watch, list):
                        self.state_trig_ident = set(self.state_user_watch)
                    else:
                        self.state_trig_ident = self.state_user_watch
                else:
                    if self.state_trig_eval:
                        self.state_trig_ident = await self.state_trig_eval.get_names()
                    self.state_trig_ident.update(self.state_trig_ident_any)
                _LOGGER.debug("trigger %s: watching vars %s", self.name, self.state_trig_ident)
                if len(self.state_trig_ident) == 0 or not await State.notify_add(
                    self.state_trig_ident, self.notify_q
                ):
                    _LOGGER.error(
                        "trigger %s: @state_trigger is not watching any variables; will never trigger",
                        self.name,
                    )

            if self.active_expr:
                self.state_active_ident = await self.active_expr.get_names()

            if self.event_trigger is not None:
                _LOGGER.debug("trigger %s adding event_trigger %s", self.name, self.event_trigger[0])
                Event.notify_add(self.event_trigger[0], self.notify_q)
            if self.mqtt_trigger is not None:
                _LOGGER.debug("trigger %s adding mqtt_trigger %s", self.name, self.mqtt_trigger[0])
                await Mqtt.notify_add(self.mqtt_trigger[0], self.notify_q)
            if self.webhook_trigger is not None:
                _LOGGER.debug("trigger %s adding webhook_trigger %s", self.name, self.webhook_trigger[0])
                Webhook.notify_add(
                    self.webhook_trigger[0], self.webhook_local_only, self.webhook_methods, self.notify_q
                )

            last_trig_time = None
            last_state_trig_time = None
            state_trig_waiting = False
            state_trig_notify_info = [None, None]
            state_false_time = None
            now = startup_time = None
            check_state_expr_on_start = self.state_check_now or self.state_hold_false is not None

            while True:
                timeout = None
                state_trig_timeout = False
                notify_info = None
                notify_type = None
                now = dt_now()
                if startup_time is None:
                    startup_time = now
                if self.run_on_startup:
                    #
                    # first time only - skip waiting for other triggers
                    #
                    notify_type = "startup"
                    notify_info = {"trigger_type": "time", "trigger_time": "startup"}
                    self.run_on_startup = False
                elif check_state_expr_on_start:
                    #
                    # first time only - skip wait and check state trigger
                    #
                    notify_type = "state"
                    if self.state_trig_ident:
                        notify_vars = State.notify_var_get(self.state_trig_ident, {})
                    else:
                        notify_vars = {}
                    notify_info = [notify_vars, {"trigger_type": notify_type}]
                    check_state_expr_on_start = False
                else:
                    if self.time_trigger:
                        time_next, time_next_adj = await TrigTime.timer_trigger_next(
                            self.time_trigger, now, startup_time
                        )
                        _LOGGER.debug(
                            "trigger %s time_next = %s, now = %s",
                            self.name,
                            time_next,
                            now,
                        )
                        if time_next is not None:
                            timeout = (time_next_adj - now).total_seconds()
                    if state_trig_waiting:
                        time_left = last_state_trig_time + self.state_hold - time.monotonic()
                        if timeout is None or time_left < timeout:
                            timeout = time_left
                            time_next = now + dt.timedelta(seconds=timeout)
                            state_trig_timeout = True
                    if timeout is not None:
                        while True:
                            try:
                                timeout = max(0, timeout)
                                _LOGGER.debug("trigger %s waiting for %.6g secs", self.name, timeout)
                                notify_type, notify_info = await asyncio.wait_for(
                                    self.notify_q.get(), timeout=timeout
                                )
                                state_trig_timeout = False
                                now = dt_now()
                            except asyncio.TimeoutError:
                                actual_now = dt_now()
                                if actual_now < time_next:
                                    timeout = (time_next - actual_now).total_seconds()
                                    continue
                                now = time_next
                                if not state_trig_timeout:
                                    notify_type = "time"
                                    notify_info = {
                                        "trigger_type": "time",
                                        "trigger_time": time_next,
                                    }
                            break
                    elif self.have_trigger:
                        _LOGGER.debug("trigger %s waiting for state change or event", self.name)
                        notify_type, notify_info = await self.notify_q.get()
                        now = dt_now()
                    else:
                        _LOGGER.debug("trigger %s finished", self.name)
                        return

                #
                # check the trigger-specific expressions
                #
                trig_ok = True
                new_vars = {}
                user_kwargs = {}
                if state_trig_timeout:
                    new_vars, func_args = state_trig_notify_info
                    state_trig_waiting = False
                elif notify_type == "state":
                    new_vars, func_args = notify_info
                    user_kwargs = self.state_trigger_kwargs.get("kwargs", {})

                    if not ident_any_values_changed(func_args, self.state_trig_ident_any):
                        #
                        # if var_name not in func_args we are check_state_expr_on_start
                        #
                        if "var_name" in func_args and not ident_values_changed(
                            func_args, self.state_trig_ident
                        ):
                            continue

                        if self.state_trig_eval:
                            trig_ok = await self.state_trig_eval.eval(new_vars)
                            exc = self.state_trig_eval.get_exception_long()
                            if exc is not None:
                                self.state_trig_eval.get_logger().error(exc)
                                trig_ok = False

                            if self.state_hold_false is not None:
                                if "var_name" not in func_args:
                                    #
                                    # this is check_state_expr_on_start check
                                    # if immediately true, force wait until False
                                    # otherwise start False wait now
                                    #
                                    state_false_time = None if trig_ok else time.monotonic()
                                    if not self.state_check_now:
                                        continue
                                if state_false_time is None:
                                    if trig_ok:
                                        #
                                        # wasn't False, so ignore after initial check
                                        #
                                        if "var_name" in func_args:
                                            continue
                                    else:
                                        #
                                        # first False, so remember when it is
                                        #
                                        state_false_time = time.monotonic()
                                elif trig_ok and "var_name" in func_args:
                                    too_soon = time.monotonic() - state_false_time < self.state_hold_false
                                    state_false_time = None
                                    if too_soon:
                                        #
                                        # was False but not for long enough, so start over
                                        #
                                        continue
                        else:
                            trig_ok = False

                    if self.state_hold is not None:
                        if trig_ok:
                            if not state_trig_waiting:
                                state_trig_waiting = True
                                state_trig_notify_info = notify_info
                                last_state_trig_time = time.monotonic()
                                _LOGGER.debug(
                                    "trigger %s got %s trigger; now waiting for state_hold of %g seconds",
                                    notify_type,
                                    self.name,
                                    self.state_hold,
                                )
                            else:
                                _LOGGER.debug(
                                    "trigger %s got %s trigger; still waiting for state_hold of %g seconds",
                                    notify_type,
                                    self.name,
                                    self.state_hold,
                                )
                            func_args.update(user_kwargs)
                            continue
                        if state_trig_waiting:
                            state_trig_waiting = False
                            _LOGGER.debug(
                                "trigger %s %s trigger now false during state_hold; waiting for new trigger",
                                notify_type,
                                self.name,
                            )
                            continue

                elif notify_type == "event":
                    func_args = notify_info
                    user_kwargs = self.event_trigger_kwargs.get("kwargs", {})
                    if self.event_trig_expr:
                        trig_ok = await self.event_trig_expr.eval(notify_info)
                elif notify_type == "mqtt":
                    func_args = notify_info
                    user_kwargs = self.mqtt_trigger_kwargs.get("kwargs", {})
                    if self.mqtt_trig_expr:
                        trig_ok = await self.mqtt_trig_expr.eval(notify_info)
                elif notify_type == "webhook":
                    func_args = notify_info
                    user_kwargs = self.webhook_trigger_kwargs.get("kwargs", {})
                    if self.webhook_trig_expr:
                        trig_ok = await self.webhook_trig_expr.eval(notify_info)

                else:
                    user_kwargs = self.time_trigger_kwargs.get("kwargs", {})
                    func_args = notify_info

                #
                # now check the state and time active expressions
                #
                if trig_ok and self.active_expr:
                    active_vars = State.notify_var_get(self.state_active_ident, new_vars)
                    trig_ok = await self.active_expr.eval(active_vars)
                    exc = self.active_expr.get_exception_long()
                    if exc is not None:
                        self.active_expr.get_logger().error(exc)
                        trig_ok = False
                if trig_ok and self.time_active:
                    trig_ok = await TrigTime.timer_active_check(self.time_active, now, startup_time)

                if not trig_ok:
                    _LOGGER.debug(
                        "trigger %s got %s trigger, but not active",
                        self.name,
                        notify_type,
                    )
                    continue

                if (
                    self.time_active_hold_off is not None
                    and last_trig_time is not None
                    and time.monotonic() < last_trig_time + self.time_active_hold_off
                ):
                    _LOGGER.debug(
                        "trigger %s got %s trigger, but less than %s seconds since last trigger, so skipping",
                        notify_type,
                        self.name,
                        self.time_active_hold_off,
                    )
                    continue

                func_args.update(user_kwargs)
                if self.call_action(notify_type, func_args):
                    last_trig_time = time.monotonic()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            # _LOGGER.error(f"{self.name}: " + traceback.format_exc(-1))
            _LOGGER.error("%s: %s", self.name, exc)
            if self.state_trig_ident:
                State.notify_del(self.state_trig_ident, self.notify_q)
            if self.event_trigger is not None:
                Event.notify_del(self.event_trigger[0], self.notify_q)
            if self.mqtt_trigger is not None:
                Mqtt.notify_del(self.mqtt_trigger[0], self.notify_q)
            if self.webhook_trigger is not None:
                Webhook.notify_del(self.webhook_trigger[0], self.notify_q)
            return

    def call_action(self, notify_type, func_args, run_task=True):
        """Call the trigger action function."""
        action_ast_ctx = AstEval(f"{self.action.global_ctx_name}.{self.action.name}", self.action.global_ctx)
        Function.install_ast_funcs(action_ast_ctx)
        task_unique_func = None
        if self.task_unique is not None:
            task_unique_func = Function.task_unique_factory(action_ast_ctx)

        #
        # check for @task_unique with kill_me=True
        #
        if (
            self.task_unique is not None
            and self.task_unique_kwargs
            and self.task_unique_kwargs["kill_me"]
            and Function.unique_name_used(action_ast_ctx, self.task_unique)
        ):
            _LOGGER.debug(
                "trigger %s got %s trigger, @task_unique kill_me=True prevented new action",
                notify_type,
                self.name,
            )
            return False

        # Create new HASS Context with incoming as parent
        if "context" in func_args and isinstance(func_args["context"], Context):
            hass_context = Context(parent_id=func_args["context"].id)
        else:
            hass_context = Context()

        # Fire an event indicating that pyscript is running
        # Note: the event must have an entity_id for logbook to work correctly.
        ev_name = self.name.replace(".", "_")
        ev_entity_id = f"pyscript.{ev_name}"

        event_data = {"name": ev_name, "entity_id": ev_entity_id, "func_args": func_args}
        Function.hass.bus.async_fire("pyscript_running", event_data, context=hass_context)

        _LOGGER.debug(
            "trigger %s got %s trigger, running action (kwargs = %s)",
            self.name,
            notify_type,
            func_args,
        )

        async def do_func_call(func, ast_ctx, task_unique, task_unique_func, hass_context, **kwargs):
            # Store HASS Context for this Task
            Function.store_hass_context(hass_context)

            if task_unique and task_unique_func:
                await task_unique_func(task_unique)
            await ast_ctx.call_func(func, None, **kwargs)
            if ast_ctx.get_exception_obj():
                ast_ctx.get_logger().error(ast_ctx.get_exception_long())

        func = do_func_call(
            self.action,
            action_ast_ctx,
            self.task_unique,
            task_unique_func,
            hass_context,
            **func_args,
        )
        if run_task:
            task = Function.create_task(func, ast_ctx=action_ast_ctx)
            Function.task_done_callback_ctx(task, action_ast_ctx)
            return True
        return func
