"""Implements all the trigger logic."""

import asyncio
import datetime as dt
import locale
import logging
import math
import re
import time

from croniter import croniter

import homeassistant.helpers.sun as sun

from .const import LOGGER_PATH
from .eval import AstEval
from .event import Event
from .function import Function
from .state import State

_LOGGER = logging.getLogger(LOGGER_PATH + ".trigger")


STATE_RE = re.compile(r"[a-zA-Z]\w*\.[a-zA-Z]\w*$")


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
        if match[2] == "m" or match[2] == "min" or match[2] == "minutes":
            scale = 60
        elif match[2] == "h" or match[2] == "hr" or match[2] == "hours":
            scale = 60 * 60
        elif match[2] == "d" or match[2] == "day" or match[2] == "days":
            scale = 60 * 60 * 24
        elif match[2] == "w" or match[2] == "week" or match[2] == "weeks":
            scale = 60 * 60 * 24 * 7
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

        ast_funcs = {
            "task.wait_until": wait_until_factory,
        }
        Function.register_ast(ast_funcs)

        for i in range(0, 7):
            cls.dow2int[locale.nl_langinfo(getattr(locale, f"ABDAY_{i + 1}")).lower()] = i
            cls.dow2int[locale.nl_langinfo(getattr(locale, f"DAY_{i + 1}")).lower()] = i

    @classmethod
    async def wait_until(
        cls,
        ast_ctx,
        state_trigger=None,
        state_check_now=True,
        time_trigger=None,
        event_trigger=None,
        timeout=None,
        **kwargs,
    ):
        """Wait for zero or more triggers, until an optional timeout."""
        if state_trigger is None and time_trigger is None and event_trigger is None:
            if timeout is not None:
                await asyncio.sleep(timeout)
                return {"trigger_type": "timeout"}
            return {"trigger_type": "none"}
        state_trig_ident = set()
        state_trig_ident_any = set()
        state_trig_eval = None
        event_trig_expr = None
        exc = None
        notify_q = asyncio.Queue(0)
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
                state_trig_eval.parse(state_trig_expr)
                state_trig_ident = await state_trig_eval.get_names()
                exc = state_trig_eval.get_exception_obj()
                if exc is not None:
                    raise exc

            state_trig_ident.update(state_trig_ident_any)
            if state_trig_eval:
                #
                # check straight away to see if the condition is met (to avoid race conditions)
                #
                state_trig_ok = await state_trig_eval.eval(State.notify_var_get(state_trig_ident, {}))
                exc = state_trig_eval.get_exception_obj()
                if exc is not None:
                    raise exc
                if state_trig_ok:
                    return {"trigger_type": "state"}

            _LOGGER.debug(
                "trigger %s wait_until: watching vars %s", ast_ctx.name, state_trig_ident,
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
                event_trig_expr.parse(event_trigger[1])
                exc = event_trig_expr.get_exception_obj()
                if exc is not None:
                    if len(state_trig_ident) > 0:
                        State.notify_del(state_trig_ident, notify_q)
                    raise exc
            Event.notify_add(event_trigger[0], notify_q)
        time0 = time.monotonic()

        while True:
            ret = None
            this_timeout = None
            time_next = None
            if time_trigger is not None:
                now = dt_now()
                time_next = cls.timer_trigger_next(time_trigger, now)
                _LOGGER.debug(
                    "trigger %s wait_until time_next = %s, now = %s", ast_ctx.name, time_next, now,
                )
                if time_next is not None:
                    this_timeout = (time_next - now).total_seconds()
            if timeout is not None:
                time_left = time0 + timeout - time.monotonic()
                if time_left <= 0:
                    ret = {"trigger_type": "timeout"}
                    break
                if this_timeout is None or this_timeout > time_left:
                    ret = {"trigger_type": "timeout"}
                    this_timeout = time_left
            if this_timeout is None:
                if state_trigger is None and event_trigger is None:
                    _LOGGER.debug(
                        "trigger %s wait_until no next time - returning with none", ast_ctx.name,
                    )
                    ret = {"trigger_type": "none"}
                    break
                _LOGGER.debug("trigger %s wait_until no timeout", ast_ctx.name)
                notify_type, notify_info = await notify_q.get()
            else:
                try:
                    _LOGGER.debug("trigger %s wait_until %s secs", ast_ctx.name, this_timeout)
                    notify_type, notify_info = await asyncio.wait_for(notify_q.get(), timeout=this_timeout)
                except asyncio.TimeoutError:
                    if not ret:
                        ret = {"trigger_type": "time"}
                        if time_next is not None:
                            ret["trigger_time"] = time_next
                    break
            if notify_type == "state":
                if notify_info:
                    new_vars, func_args = notify_info
                else:
                    new_vars, func_args = None, {}

                state_trig_ok = False
                if func_args.get("var_name", "") in state_trig_ident_any:
                    state_trig_ok = True
                elif state_trig_eval:
                    state_trig_ok = await state_trig_eval.eval(new_vars)
                    exc = state_trig_eval.get_exception_obj()
                    if exc is not None:
                        break
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
            else:
                _LOGGER.error(
                    "trigger %s wait_until got unexpected queue message %s", ast_ctx.name, notify_type,
                )

        if len(state_trig_ident) > 0:
            State.notify_del(state_trig_ident, notify_q)
        if event_trigger is not None:
            Event.notify_del(event_trigger[0], notify_q)
        if exc:
            raise exc
        return ret

    @classmethod
    def parse_date_time(cls, date_time_str, day_offset, now):
        """Parse a date time string, returning datetime."""
        year = now.year
        month = now.month
        day = now.day

        dt_str = date_time_str.strip().lower()
        #
        # parse the date
        #
        skip = True
        match0 = re.split(r"^0*(\d+)[-/]0*(\d+)(?:[-/]0*(\d+))?", dt_str)
        match1 = re.split(r"^(\w+).*", dt_str)
        if len(match0) == 5:
            if match0[3] is None:
                month, day = int(match0[1]), int(match0[2])
            else:
                year, month, day = int(match0[1]), int(match0[2]), int(match0[3])
            day_offset = 0  # explicit date means no offset
        elif len(match1) == 3:
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
        else:
            skip = False
        if day_offset != 0:
            now = dt.datetime(year, month, day) + dt.timedelta(days=day_offset)
            year = now.year
            month = now.month
            day = now.day
        else:
            now = dt.datetime(year, month, day)
        if skip:
            i = dt_str.find(" ")
            if i >= 0:
                dt_str = dt_str[i + 1 :].strip()
            else:
                return now

        #
        # parse the time
        #
        skip = True
        match0 = re.split(r"0*(\d+):0*(\d+)(?::0*(\d*\.?\d+(?:[eE][-+]?\d+)?))?", dt_str)
        if len(match0) == 5:
            if match0[3] is not None:
                hour, mins, sec = int(match0[1]), int(match0[2]), float(match0[3])
            else:
                hour, mins, sec = int(match0[1]), int(match0[2]), 0
        elif dt_str.startswith("sunrise") or dt_str.startswith("sunset"):
            location = sun.get_astral_location(cls.hass)
            try:
                if dt_str.startswith("sunrise"):
                    time_sun = location.sunrise(dt.date(year, month, day))
                else:
                    time_sun = location.sunset(dt.date(year, month, day))
            except Exception:
                _LOGGER.warning("'%s' not defined at this latitude", dt_str)
                # return something in the past so it is ignored
                return now - dt.timedelta(days=100)
            now += time_sun.date() - now.date()
            hour, mins, sec = time_sun.hour, time_sun.minute, time_sun.second
        elif dt_str.startswith("noon"):
            hour, mins, sec = 12, 0, 0
        elif dt_str.startswith("midnight"):
            hour, mins, sec = 0, 0, 0
        else:
            hour, mins, sec = 0, 0, 0
            skip = False
        now += dt.timedelta(seconds=sec + 60 * (mins + 60 * hour))
        if skip:
            i = dt_str.find(" ")
            if i >= 0:
                dt_str = dt_str[i + 1 :].strip()
            else:
                return now
        #
        # parse the offset
        #
        if len(dt_str) > 0 and (dt_str[0] == "+" or dt_str[0] == "-"):
            now = now + dt.timedelta(seconds=parse_time_offset(dt_str))
        return now

    @classmethod
    def timer_active_check(cls, time_spec, now):
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

                start = cls.parse_date_time(dt_start.strip(), 0, now)
                end = cls.parse_date_time(dt_end.strip(), 0, start)

                if start < end:
                    this_match = start <= now <= end
                else:  # Over midnight
                    this_match = now >= start or now <= end

            if negate:
                results["-"].append(not this_match)
            else:
                results["+"].append(this_match)

        # An empty spec, or only neg specs, is True
        result = any(results["+"]) if results["+"] else True and all(results["-"])

        return result

    @classmethod
    def timer_trigger_next(cls, time_spec, now):
        """Return the next trigger time based on the given time and time specification."""
        next_time = None
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

                val = croniter(cron_match.group("cron_expr"), now, dt.datetime).get_next()
                if next_time is None or val < next_time:
                    next_time = val

            elif len(match1) == 3:
                this_t = cls.parse_date_time(match1[1].strip(), 0, now)
                if this_t <= now:
                    #
                    # Try tomorrow (won't make a difference if spec has full date)
                    #
                    this_t = cls.parse_date_time(match1[1].strip(), 1, now)
                if now < this_t and (next_time is None or this_t < next_time):
                    next_time = this_t

            elif len(match2) == 5:
                start_str, period_str = match2[1].strip(), match2[2].strip()
                start = cls.parse_date_time(start_str, 0, now)
                period = parse_time_offset(period_str)
                if period <= 0:
                    _LOGGER.error("Invalid non-positive period %s in period(): %s", period, time_spec)
                    continue

                if match2[3] is None:
                    if now < start and (next_time is None or start < next_time):
                        next_time = start
                    if now >= start:
                        secs = period * (1.0 + math.floor((now - start).total_seconds() / period))
                        this_t = start + dt.timedelta(seconds=secs)
                        if now < this_t and (next_time is None or this_t < next_time):
                            next_time = this_t
                    continue
                end_str = match2[3].strip()
                end = cls.parse_date_time(end_str, 0, now)
                end_offset = 1 if end < start else 0
                for day in [-1, 0, 1]:
                    start = cls.parse_date_time(start_str, day, now)
                    end = cls.parse_date_time(end_str, day + end_offset, now)
                    if now < start:
                        if next_time is None or start < next_time:
                            next_time = start
                        break
                    secs = period * (1.0 + math.floor((now - start).total_seconds() / period))
                    this_t = start + dt.timedelta(seconds=secs)
                    if start <= this_t <= end:
                        if next_time is None or this_t < next_time:
                            next_time = this_t
                        break

            else:
                _LOGGER.warning("Can't parse %s in time_trigger check", spec)
        return next_time


class TrigInfo:
    """Class for all trigger-decorated functions."""

    def __init__(
        self, name, trig_cfg, global_ctx=None,
    ):
        """Create a new TrigInfo."""
        self.name = name
        self.task = None
        self.global_ctx = global_ctx
        self.trig_cfg = trig_cfg
        self.state_trigger = trig_cfg.get("state_trigger", {}).get("args", None)
        self.time_trigger = trig_cfg.get("time_trigger", {}).get("args", None)
        self.event_trigger = trig_cfg.get("event_trigger", {}).get("args", None)
        self.state_active = trig_cfg.get("state_active", {}).get("args", None)
        self.time_active = trig_cfg.get("time_active", {}).get("args", None)
        self.time_active_kwargs = trig_cfg.get("time_active", {}).get("kwargs", None)
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
        self.have_trigger = False
        self.setup_ok = False
        self.run_on_startup = False

        if self.state_active is not None:
            self.active_expr = AstEval(
                f"{self.name} @state_active()", self.global_ctx, logger_name=self.name
            )
            Function.install_ast_funcs(self.active_expr)
            self.active_expr.parse(self.state_active)
            exc = self.active_expr.get_exception_long()
            if exc is not None:
                self.active_expr.get_logger().error(exc)
                return

        if self.time_trigger is not None:
            while "startup" in self.time_trigger:
                self.run_on_startup = True
                self.time_trigger.remove("startup")
            if len(self.time_trigger) == 0:
                self.time_trigger = None
        if "time_trigger" in trig_cfg and self.time_trigger is None:
            self.run_on_startup = True

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
                self.state_trig_eval.parse(self.state_trig_expr)
                exc = self.state_trig_eval.get_exception_long()
                if exc is not None:
                    self.state_trig_eval.get_logger().error(exc)
                    return
            self.have_trigger = True

        if self.event_trigger is not None:
            if len(self.event_trigger) == 2:
                self.event_trig_expr = AstEval(
                    f"{self.name} @event_trigger()", self.global_ctx, logger_name=self.name,
                )
                Function.install_ast_funcs(self.event_trig_expr)
                self.event_trig_expr.parse(self.event_trigger[1])
                exc = self.event_trig_expr.get_exception_long()
                if exc is not None:
                    self.event_trig_expr.get_logger().error(exc)
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
            if self.task:
                Function.task_cancel(self.task)

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
                if self.state_trig_eval:
                    self.state_trig_ident = await self.state_trig_eval.get_names()
                self.state_trig_ident.update(self.state_trig_ident_any)
                _LOGGER.debug("trigger %s: watching vars %s", self.name, self.state_trig_ident)
                if len(self.state_trig_ident) > 0:
                    await State.notify_add(self.state_trig_ident, self.notify_q)

            if self.active_expr:
                self.state_active_ident = await self.active_expr.get_names()

            if self.event_trigger is not None:
                _LOGGER.debug("trigger %s adding event_trigger %s", self.name, self.event_trigger[0])
                Event.notify_add(self.event_trigger[0], self.notify_q)

            last_trigger_time = None

            while True:
                timeout = None
                notify_info = None
                notify_type = None
                if self.run_on_startup:
                    #
                    # first time only - skip waiting for other triggers
                    #
                    notify_info = {"trigger_type": "time", "trigger_time": None}
                    self.run_on_startup = False
                else:
                    if self.time_trigger:
                        now = dt_now()
                        time_next = TrigTime.timer_trigger_next(self.time_trigger, now)
                        _LOGGER.debug(
                            "trigger %s time_next = %s, now = %s", self.name, time_next, now,
                        )
                        if time_next is not None:
                            timeout = (time_next - now).total_seconds()
                    if timeout is not None:
                        try:
                            _LOGGER.debug("trigger %s waiting for %s secs", self.name, timeout)
                            notify_type, notify_info = await asyncio.wait_for(
                                self.notify_q.get(), timeout=timeout
                            )
                        except asyncio.TimeoutError:
                            notify_info = {
                                "trigger_type": "time",
                                "trigger_time": time_next,
                            }
                    elif self.have_trigger:
                        _LOGGER.debug("trigger %s waiting for state change or event", self.name)
                        notify_type, notify_info = await self.notify_q.get()
                    else:
                        _LOGGER.debug("trigger %s finished", self.name)
                        return

                #
                # check the trigger-specific expressions
                #
                trig_ok = True
                new_vars = {}
                if notify_type == "state":
                    new_vars, func_args = notify_info

                    if func_args["var_name"] not in self.state_trig_ident_any:
                        if self.state_trig_eval:
                            trig_ok = await self.state_trig_eval.eval(new_vars)
                            exc = self.state_trig_eval.get_exception_long()
                            if exc is not None:
                                self.state_trig_eval.get_logger().error(exc)
                                trig_ok = False
                        else:
                            trig_ok = False

                elif notify_type == "event":
                    func_args = notify_info
                    if self.event_trig_expr:
                        trig_ok = await self.event_trig_expr.eval(notify_info)

                else:
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
                    trig_ok = TrigTime.timer_active_check(self.time_active, dt_now())

                if not trig_ok:
                    _LOGGER.debug(
                        "trigger %s got %s trigger, but not active", self.name, notify_type,
                    )
                    continue

                action_ast_ctx = AstEval(
                    f"{self.action.global_ctx_name}.{self.action.name}", self.action.global_ctx
                )
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
                    continue

                if (
                    self.time_active_kwargs
                    and "hold_off" in self.time_active_kwargs
                    and last_trigger_time is not None
                    and time.monotonic() < last_trigger_time + self.time_active_kwargs["hold_off"]
                ):
                    _LOGGER.debug(
                        "trigger %s got %s trigger, but less than %s seconds since last trigger, so skipping",
                        notify_type,
                        self.name,
                        self.time_active_kwargs["hold_off"],
                    )
                    continue

                _LOGGER.debug(
                    "trigger %s got %s trigger, running action (kwargs = %s)",
                    self.name,
                    notify_type,
                    func_args,
                )

                async def do_func_call(func, ast_ctx, task_unique, task_unique_func, **kwargs):
                    if task_unique and task_unique_func:
                        await task_unique_func(task_unique)
                    await func.call(ast_ctx, **kwargs)
                    if ast_ctx.get_exception_obj():
                        ast_ctx.get_logger().error(ast_ctx.get_exception_long())

                last_trigger_time = time.monotonic()

                Function.create_task(
                    do_func_call(
                        self.action, action_ast_ctx, self.task_unique, task_unique_func, **func_args
                    )
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            # _LOGGER.error(f"{self.name}: " + traceback.format_exc(-1))
            if self.state_trig_ident:
                State.notify_del(self.state_trig_ident, self.notify_q)
            if self.event_trigger is not None:
                Event.notify_del(self.event_trigger[0], self.notify_q)
            return
