"""State decorators."""

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.helpers import config_validation as cv

from ..decorator import WaitUntilDecoratorManager
from ..decorator_abc import DecoratorManagerStatus, DispatchData, TriggerDecorator, TriggerHandlerDecorator
from ..state import STATE_VIRTUAL_ATTRS, State
from .base import AutoKwargsDecorator, ExpressionDecorator, strip_trigger_expr

STATE_RE = re.compile(r"\w+\.\w+(\.((\w+)|\*))?$")

_LOGGER = logging.getLogger(__name__)


def any_trigger_exprs(exprs):
    """
    Join trigger expressions into one any([...]) expression.

    Each expression goes on its own lines, so a trailing # comment can't swallow the separator
    or the closing bracket that follow it.
    """
    return "any([" + "\n,\n".join(exprs) + "\n])"


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


class StateActiveDecorator(TriggerHandlerDecorator, ExpressionDecorator):
    """Implementation for @state_active."""

    name = "state_active"
    args_schema = vol.Schema(
        vol.All(
            vol.Length(
                min=1, max=1, msg="got 2 arguments, expected 1"
            ),  # Keep this wording for transition compatibility.
            vol.All([str]),
        )
    )

    var_names: set[str]

    async def validate(self) -> None:
        """Validate the decorator arguments."""
        await super().validate()
        self.create_expression(self.args[0])
        self.var_names = await self._ast_expression.get_names()

    async def handle_dispatch(self, data: DispatchData) -> bool:
        """Handle dispatch events."""
        new_vars = data.trigger_context.get("new_vars", {})
        active_vars = State.notify_var_get(self.var_names, new_vars)
        return await self.check_expression_vars(active_vars)


def _validate_state_trigger_args(args: list[Any]) -> list[str]:
    """Validate and normalize @state_trigger positional arguments."""
    if not isinstance(args, list):
        raise vol.Invalid("arguments must be a list")
    if len(args) == 0:
        raise vol.Invalid("needs at least one argument")

    normalized: list[str] = []
    for idx, arg in enumerate(args, start=1):
        if isinstance(arg, str):
            normalized.append(arg)
            continue
        if isinstance(arg, (list, set)):
            if not all(isinstance(expr, str) for expr in arg):
                raise vol.Invalid(f"argument {idx} should be a string, or list, or set")
            normalized.extend(list(arg))
            continue
        raise vol.Invalid(f"argument {idx} should be a string, or list, or set")
    return normalized


class StateTriggerDecorator(TriggerDecorator, ExpressionDecorator, AutoKwargsDecorator):
    """Implementation for @state_trigger."""

    name = "state_trigger"
    args_schema = vol.Schema(vol.All(_validate_state_trigger_args))
    kwargs_schema = vol.Schema(
        {
            vol.Optional("state_hold"): vol.Any(None, cv.positive_float),
            vol.Optional("state_hold_false"): vol.Any(None, cv.positive_float),
            vol.Optional("state_check_now"): cv.boolean,
            vol.Optional("watch"): vol.Any(None, vol.Coerce(set), msg="should be type list or set"),
            vol.Optional("__test_handshake__"): vol.Coerce(list),
        }
    )
    # kwargs
    state_hold: float | None
    state_hold_false: float | None
    state_check_now: bool | None
    __test_handshake__: list[str] | None

    notify_q: asyncio.Queue
    in_wait_until_function: bool
    cycle_task: asyncio.Task = None

    state_trig_ident: set[str]
    state_trig_ident_any: set[str]
    true_entered_at: float | None
    false_entered_at: float | None

    last_func_args: dict[str, Any]
    last_new_vars: dict[str, Any]

    async def validate(self) -> None:
        """Validate and normalize arguments."""
        await super().validate()
        self.state_trig_ident = set()
        self.state_trig_ident_any = set()

        self.in_wait_until_function = isinstance(self.dm, WaitUntilDecoratorManager)

        if self.state_check_now is None and self.in_wait_until_function:
            # check by default for task.wait_until
            self.state_check_now = True

        state_trig = []

        for trig in self.args:
            trig = strip_trigger_expr(trig)
            if STATE_RE.match(trig):
                self.state_trig_ident_any.add(trig)
            else:
                state_trig.append(trig)

        if len(state_trig) > 0:
            if len(state_trig) == 1:
                state_trig_expr = state_trig[0]
            else:
                state_trig_expr = any_trigger_exprs(state_trig)

            self.create_expression(state_trig_expr)

        if self.kwargs.get("watch") is not None:
            self.state_trig_ident = set(self.kwargs.get("watch", []))
        else:
            if self.has_expression():
                self.state_trig_ident = await self._ast_expression.get_names()
            self.state_trig_ident.update(self.state_trig_ident_any)

        _LOGGER.debug("trigger %s: watching vars %s", self.name, self.state_trig_ident)
        _LOGGER.debug("trigger %s: any %s", self.name, self.state_trig_ident_any)
        if len(self.state_trig_ident) == 0:
            self.dm.logger.error(
                "trigger %s: @state_trigger is not watching any variables; will never trigger",
                self.dm.name,
            )

    def _diff(self, dt: float, now: float) -> str:
        if dt is None:
            return "None"
        return f"{(now - dt):g} ago"

    async def _check_new_state(
        self, trig_ok: bool, new_vars: dict[str, Any], func_args: dict[str, Any]
    ) -> None:
        now = asyncio.get_running_loop().time()
        if _LOGGER.isEnabledFor(logging.DEBUG):
            msg = f"check_new_state: {self}"
            msg += f"\ntrig_ok: {trig_ok} now {now} func_args: {func_args} new_vars: {new_vars}"
            if self.true_entered_at:
                msg += f"\ntrue_entered_at: {self.true_entered_at}({(now - self.true_entered_at):g} ago)\n"
            if self.false_entered_at:
                msg += (
                    f"\nfalse_entered_at: {self.false_entered_at}({(now - self.false_entered_at):g} ago)\n"
                )
            _LOGGER.debug(msg)

        state_hold_false_passed = False
        state_hold_true_passed = False
        if trig_ok:
            if self.state_hold_false is None or not self.has_expression():
                state_hold_false_passed = True
            else:
                if self.false_entered_at:
                    false_duration = now - self.false_entered_at
                    if false_duration >= self.state_hold_false:
                        state_hold_false_passed = True
                        _LOGGER.debug(
                            "state_hold_false passed (%g), reset false_entered_at, %s", false_duration, self
                        )
                    self.false_entered_at = None

            if state_hold_false_passed:
                if self.state_hold is None:
                    self.last_new_vars = new_vars
                    self.last_func_args = func_args
                    state_hold_true_passed = True
                else:
                    if self.true_entered_at:
                        true_duration = now - self.true_entered_at
                        if true_duration >= self.state_hold:
                            state_hold_true_passed = True
                            self.true_entered_at = None
                            _LOGGER.debug(
                                "state_hold passed (%g), reset true_entered_at, %s", true_duration, self
                            )
                    else:
                        _LOGGER.debug("state_hold started, %s", self)
                        self.true_entered_at = now
                        self.last_new_vars = new_vars
                        self.last_func_args = func_args

            if state_hold_true_passed:
                await self.dispatch(
                    DispatchData(self.last_func_args, trigger_context={"new_vars": self.last_new_vars})
                )
                self.__test_handshake__ = None
        else:
            self.true_entered_at = None
            if self.state_hold_false is not None:
                if not self.false_entered_at:
                    _LOGGER.debug("state_hold_false started, %s", self)
                    self.false_entered_at = now

    async def _check_state_hold(self) -> None:
        if self.true_entered_at is None:
            raise RuntimeError(f"state_hold not started for {self}")

        now = asyncio.get_running_loop().time()
        true_duration = now - self.true_entered_at
        if true_duration >= self.state_hold:
            self.true_entered_at = None
            await self.dispatch(
                DispatchData(self.last_func_args, trigger_context={"new_vars": self.last_new_vars})
            )

    async def _cycle(self) -> None:
        """Run the trigger cycle with state_hold and state_hold_false logic."""
        loop = asyncio.get_running_loop()

        self.true_entered_at = None
        self.false_entered_at = None

        self.last_func_args = {"trigger_type": "state"}
        self.last_new_vars = {}

        check_state_expr_on_start = self.state_check_now or self.state_hold_false is not None

        if check_state_expr_on_start:
            new_vars = State.notify_var_get(self.state_trig_ident, {})
            trig_ok = await self._is_trig_ok(new_vars)

            if self.in_wait_until_function and trig_ok and self.state_check_now is True:
                self.state_hold_false = None

            if self.state_check_now and self.has_expression():
                await self._check_new_state(trig_ok, new_vars, self.last_func_args)
            else:
                if not trig_ok and self.state_hold_false is not None:
                    self.false_entered_at = loop.time()

        if self.__test_handshake__ is not None:
            #
            # used for testing to avoid race conditions
            # we use this as a handshake that we are about to
            # listen to the queue
            #
            _LOGGER.debug("__test_handshake__ handshake: %s", self.__test_handshake__)
            State.set(self.__test_handshake__[0], self.__test_handshake__[1])
            self.__test_handshake__ = None

        while self.dm.status is DecoratorManagerStatus.RUNNING:
            if self.true_entered_at is None:
                effective_timeout = None
            else:
                effective_timeout = self.state_hold
                if self.true_entered_at is not None:
                    effective_timeout -= loop.time() - self.true_entered_at

                if effective_timeout <= 1e-6:
                    # ignore deltas smaller than 1us.
                    await self._check_state_hold()
                    continue

            try:
                if effective_timeout is None:
                    notify_type, notify_info = await self.notify_q.get()
                else:
                    notify_type, notify_info = await asyncio.wait_for(self.notify_q.get(), effective_timeout)
                if notify_type != "state":
                    raise RuntimeError(f"Invalid notify_type {notify_type}, {self}")
                new_vars = notify_info[0]
                func_args = notify_info[1]

                if ident_any_values_changed(func_args, self.state_trig_ident_any):
                    trig_ok = True
                elif ident_values_changed(func_args, self.state_trig_ident):
                    trig_ok = await self._is_trig_ok(new_vars)
                else:
                    continue
                await self._check_new_state(trig_ok, new_vars, func_args)
            except TimeoutError:
                await self._check_state_hold()

    async def _is_trig_ok(self, new_vars: dict[str, Any]) -> bool:
        if self.has_expression():
            return await self.check_expression_vars(new_vars)
        return True

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.dm.logger.error("%s failed", self, exc_info=exc)

    async def start(self) -> None:
        """Start the trigger."""
        await super().start()
        self.notify_q = asyncio.Queue(0)
        if not await State.notify_add(self.state_trig_ident, self.notify_q):
            self.dm.logger.error(
                "trigger %s: @state_trigger is not watching any variables; will never trigger",
                self.dm.name,
            )
            return
        _LOGGER.debug("trigger %s: starting", self.name)

        self.cycle_task = self.dm.hass.async_create_background_task(self._cycle(), repr(self))
        self.cycle_task.add_done_callback(self._on_task_done)

    async def stop(self):
        """Stop the trigger."""
        await super().stop()
        if self.cycle_task is not None:
            self.cycle_task.cancel()
        State.notify_del(self.state_trig_ident, self.notify_q)
