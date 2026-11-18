"""Implements all the trigger logic."""

import asyncio
import datetime as dt
import logging
import re
import time

from homeassistant.core import Context

from . import decorator_abc
from .const import LOGGER_PATH
from .decorators.state import (
    any_trigger_exprs,
    ident_any_values_changed,
    ident_values_changed,
    strip_trigger_expr,
)
from .decorators.timing import TrigTime
from .eval import AstEval
from .event import Event
from .function import Function
from .logbook import RUNNING_EVENT_FUNC_ARGS
from .mqtt import Mqtt
from .state import State
from .webhook import Webhook

_LOGGER = logging.getLogger(LOGGER_PATH + ".trigger")


STATE_RE = re.compile(r"\w+\.\w+(\.((\w+)|\*))?$")


def dt_now():
    """Return current time."""
    return decorator_abc.dt_now()


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
        self.mqtt_trigger_encoding = self.mqtt_trigger_kwargs.get("encoding", None)
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
            self.active_expr.parse_trigger_expr(self.state_active)

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
                    name = strip_trigger_expr(trig)
                    if STATE_RE.match(name):
                        self.state_trig_ident_any.add(name)
                    else:
                        state_trig.append(trig)

            if len(state_trig) > 0:
                if len(state_trig) == 1:
                    self.state_trig_expr = state_trig[0]
                else:
                    self.state_trig_expr = any_trigger_exprs(state_trig)
                self.state_trig_eval = AstEval(
                    f"{self.name} @state_trigger()", self.global_ctx, logger_name=self.name
                )
                Function.install_ast_funcs(self.state_trig_eval)
                try:
                    self.state_trig_eval.parse_trigger_expr(self.state_trig_expr)
                except Exception as exc:
                    self.state_trig_eval.log_exception(exc)
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
                try:
                    self.event_trig_expr.parse_trigger_expr(self.event_trigger[1])
                except Exception as exc:
                    self.event_trig_expr.log_exception(exc)
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
                try:
                    self.mqtt_trig_expr.parse_trigger_expr(self.mqtt_trigger[1])
                except Exception as exc:
                    self.mqtt_trig_expr.log_exception(exc)
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
                try:
                    self.webhook_trig_expr.parse_trigger_expr(self.webhook_trigger[1])
                except Exception as exc:
                    self.webhook_trig_expr.log_exception(exc)
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
                await Mqtt.notify_add(
                    self.mqtt_trigger[0], self.notify_q, encoding=self.mqtt_trigger_encoding
                )
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
                            trig_ok = await self._call_expression(self.state_trig_eval, new_vars)

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
                        trig_ok = await self._call_expression(self.event_trig_expr, notify_info)
                elif notify_type == "mqtt":
                    func_args = notify_info
                    user_kwargs = self.mqtt_trigger_kwargs.get("kwargs", {})
                    if self.mqtt_trig_expr:
                        trig_ok = await self._call_expression(self.mqtt_trig_expr, notify_info)
                elif notify_type == "webhook":
                    func_args = notify_info
                    user_kwargs = self.webhook_trigger_kwargs.get("kwargs", {})
                    if self.webhook_trig_expr:
                        trig_ok = await self._call_expression(self.webhook_trig_expr, notify_info)

                else:
                    user_kwargs = self.time_trigger_kwargs.get("kwargs", {})
                    func_args = notify_info

                #
                # now check the state and time active expressions
                #
                if trig_ok and self.active_expr:
                    active_vars = State.notify_var_get(self.state_active_ident, new_vars)
                    try:
                        trig_ok = await self.active_expr.eval(active_vars)
                    except Exception as e:
                        self.active_expr.log_exception(e)
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

    async def _call_expression(self, ast_expr, notify_info):
        try:
            return await ast_expr.eval(notify_info)
        except Exception as exc:
            ast_expr.log_exception(exc)
            return False

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

        event_func_args = {key: func_args[key] for key in RUNNING_EVENT_FUNC_ARGS if key in func_args}
        event_data = {"name": ev_name, "entity_id": ev_entity_id, "func_args": event_func_args}
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
            try:
                await ast_ctx.call_func(func, None, **kwargs)
            except Exception as e:
                ast_ctx.log_exception(e)

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
