"""Helper stub that exposes pyscript's dynamic built-ins to static analyzers.

The real implementations are injected by pyscript at runtime; only signatures
and documentation live here.
"""

# pylint: disable=unnecessary-ellipsis, invalid-name, redefined-outer-name
from __future__ import annotations

from asyncio import Task
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from homeassistant.core import HomeAssistant

hass: HomeAssistant


def service(
    *service_name: str, supports_response: Literal["none", "only", "optional"] = "none"
) -> Callable[..., Any]:
    """Register the wrapped function as a Home Assistant service.

    Args:
        service_name: Optional ``DOMAIN.SERVICE`` aliases; defaults to ``pyscript.<function>``.
        supports_response: Advertised response mode (``"none"``, ``"only"``, or ``"optional"``).
    """
    ...


def state_trigger(
    *str_expr: str,
    state_hold: int | float | None = None,
    state_hold_false: int | float | None = None,
    state_check_now: bool = False,
    kwargs: dict | None = None,
    watch: list[str] | set[str] | None = None,
) -> Callable[..., Any]:
    """Trigger when any provided state expression evaluates truthy.

    Args:
        str_expr: One or more state expressions (strings, lists, or sets) that are ORed together.
        state_hold: Seconds the expression must stay true before firing; cancelled if it reverts.
        state_hold_false: Seconds the expression must stay false before another trigger; ``0`` enforces edges.
        state_check_now: Evaluate at registration time and fire immediately if the expression is true.
        kwargs: Extra keywords injected into each call in addition to the standard trigger context.
        watch: Explicit entities or attributes to monitor when autodetection from the expression is insufficient.

    Trigger kwargs include ``trigger_type="state"``, ``var_name``, ``value`` and ``old_value`` when available.
    """
    ...


def state_active(str_expr: str) -> Callable[..., Any]:
    """Restrict trigger execution to state-based condition.

    Args:
        str_expr: Expression that must evaluate truthy for the trigger to run; ``.old`` values are available for state triggers.
    """
    ...


def time_trigger(*time_spec: str | None, kwargs: dict | None = None) -> Callable[..., Any]:
    """Schedule the function using time specifications.

    Args:
        time_spec: Time expressions such as ``startup``, ``shutdown``, ``once()``, ``period()``, or ``cron()``.
        kwargs: Optional trigger keywords merged into each invocation.
    """
    ...


def task_unique(name: str, kill_me: bool = False) -> Callable[..., Any]:
    """Ensure only one running instance of the decorated task.

    Args:
        name: Identifier used to reclaim prior tasks that called ``task.unique`` or ``@task_unique``.
        kill_me: Cancel the new run instead of the existing one when a conflict is found.
    """
    ...


def event_trigger(
    *event_type: str, str_expr: str | None = None, kwargs: dict | None = None
) -> Callable[..., Any]:
    """Trigger when a Home Assistant event matches the criteria.

    Args:
        event_type: Event types to subscribe to; multiple values act as aliases.
        str_expr: Optional filter evaluated against the event payload and context variables.
        kwargs: Extra keyword arguments merged into each call.

    Trigger kwargs include ``trigger_type="event"`` and the event data fields.
    """
    ...


def time_active(*time_spec: str, hold_off: int | float | None = None) -> Callable[..., Any]:
    """Restrict trigger execution to specific time windows.

    Args:
        time_spec: ``range()`` or ``cron()`` expressions (optionally prefixed with ``not``) checked on each trigger.
        hold_off: Seconds to suppress further triggers after a successful run.

    """
    ...


def mqtt_trigger(
    topic: str, str_expr: str | None = None, encoding: str = "utf-8", kwargs: dict | None = None
) -> Callable[..., Any]:
    """Trigger when a subscribed MQTT message matches the specification.

    Args:
        topic: MQTT topic to monitor; wildcards ``+`` and ``#`` are supported.
        str_expr: Optional expression evaluated against ``payload``, ``payload_obj``, ``retain``, ``topic``, and ``qos``.
        encoding: Character encoding for MQTT payload decoding; defaults to ``"utf-8"``.
        kwargs: Extra keyword arguments merged into each invocation.
    """
    ...


def pyscript_compile() -> Callable[..., Any]:
    """Compile the wrapped function into native (synchronous) Python.

    Compiled functions cannot use pyscript-only features but run at full CPython speed.
    """
    ...


def pyscript_executor() -> Callable[..., Any]:
    """Compile the wrapped function and run it transparently in ``task.executor``.

    Use it for blocking or I/O-bound code so each call runs in a background thread.
    """
    ...


class log:
    """Logging helpers that mirror Home Assistant's logging levels."""

    @staticmethod
    def debug(msg: Any, *args, **kwargs) -> None:
        """Log a debug-level message scoped to the current pyscript context.

        Args:
            msg: Message or format string to log.
        """
        ...

    @staticmethod
    def info(msg: Any, *args, **kwargs) -> None:
        """Log an info-level message scoped to the current pyscript context.

        Args:
            msg: Message or format string to log.
        """
        ...

    @staticmethod
    def warning(msg: Any, *args, **kwargs) -> None:
        """Log a warning-level message scoped to the current pyscript context.

        Args:
            msg: Message or format string to log.
        """
        ...

    @staticmethod
    def error(msg: Any, *args, **kwargs) -> None:
        """Log an error-level message scoped to the current pyscript context.

        Args:
            msg: Message or format string to log.
        """
        ...


class state:
    """Utility functions for accessing and managing Home Assistant state."""

    @staticmethod
    def delete(name: str) -> None:
        """Remove an entity or attribute identified by ``name``.

        Args:
            name: Fully qualified entity or entity attribute to delete (``DOMAIN.entity[.attr]``).
        """
        ...

    @staticmethod
    def exist(name: str) -> bool:
        """Check whether a state variable or attribute exists.

        Args:
            name: Fully qualified entity or entity attribute name.

        Returns:
            bool: ``True`` if the entity or attribute is present, otherwise ``False``.
        """
        ...

    @staticmethod
    def get(name: str) -> Any:
        """Return the current value for an entity or attribute.

        Args:
            name: Fully qualified entity or entity attribute name.

        Returns:
            Any: State value or attribute value; raises ``NameError``/``AttributeError`` if missing.
        """
        ...

    @staticmethod
    def getattr(name: str) -> dict[str, Any] | None:
        """Return the attribute dictionary for an entity, if present.

        Args:
            name: Entity id or attribute path that resolves to an entity.

        Returns:
            dict[str, Any]: Attribute mapping, or ``None`` when the entity is unknown.
        """
        ...

    @staticmethod
    def names(domain: str | None = None) -> list[str]:
        """List entity ids within an optional domain.

        Args:
            domain: Domain prefix to filter by; returns every entity when omitted.

        Returns:
            list[str]: Entity ids known to Home Assistant.
        """
        ...

    @staticmethod
    def persist(
        entity_id: str, default_value: Any = None, default_attributes: dict[str, Any] | None = None
    ) -> None:
        """Persist a ``pyscript.`` entity across restarts with optional defaults.

        Args:
            entity_id: Entity id that must live in the ``pyscript`` domain.
            default_value: Value to seed when the entity is missing.
            default_attributes: Attribute dictionary to seed when attributes are absent.
        """
        ...

    @staticmethod
    def set(
        entity_id: str,
        value: Any = None,
        new_attributes: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Set an entity value and optionally update or replace attributes.

        Args:
            entity_id: Fully qualified entity id to update.
            value: New state value; omit to leave the current value unchanged.
            new_attributes: Attribute dictionary that replaces existing attributes.
        """
        ...

    @staticmethod
    def setattr(name: str, value: Any) -> None:
        """Assign a single attribute on the specified entity.

        Args:
            name: Entity attribute path in ``DOMAIN.entity.attr`` form.
            value: Attribute value to write.
        """
        ...


class event:
    """Helpers for interacting with Home Assistant's event bus."""

    @staticmethod
    def fire(event_type: str, **kwargs: Any) -> None:
        """Send an event on the Home Assistant event bus.

        Args:
            event_type: Name of the event to publish.
            **kwargs: Event payload delivered as event data.
        """
        ...


class task:
    """Asynchronous task utilities built on top of ``asyncio``."""

    @staticmethod
    def create(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Task:
        """Spawn a new pyscript task that executes ``func`` with the supplied arguments.

        Args:
            func: Callable to execute in the new task.
            *args: Positional arguments forwarded to ``func``.
            **kwargs: Keyword arguments forwarded to ``func``.

        Returns:
            Task: Newly created asyncio task.
        """
        ...

    @staticmethod
    def cancel(task_id: Task | None = None) -> None:
        """Cancel a task, defaulting to the current task.

        Args:
            task_id: Task returned by ``task.create``; cancels the current task when omitted.
        """
        ...

    @staticmethod
    def current_task() -> Task:
        """Return the currently running pyscript task.

        Returns:
            Task: Task representing the active pyscript coroutine.
        """
        ...

    @staticmethod
    def name2id(name: str | None = None) -> Task | dict[str, Task]:
        """Resolve registered task names (from ``task.unique``) to task objects.

        Args:
            name: Specific task name to resolve; return a mapping of all names when omitted.

        Returns:
            Task | dict[str, Task]: Task matching the name, or mapping of all names to tasks.
        """
        ...

    @staticmethod
    def wait(
        task_set: list[Task],
        timeout: int | float | None = None,
        return_when: Literal["ALL_COMPLETED", "FIRST_COMPLETED", "FIRST_EXCEPTION"] = "ALL_COMPLETED",
    ) -> tuple[set[Task], set[Task]]:
        """Wait for tasks using ``asyncio.wait`` semantics.

        Args:
            task_set: List of asyncio tasks to monitor.
            timeout: Seconds to wait before returning pending tasks; ``None`` waits forever.
            return_when: Condition that ends the wait (see ``asyncio.wait``).

        Returns:
            tuple[set[Task], set[Task]]: Two sets ``(done, pending)`` mirroring ``asyncio.wait``.
        """
        ...

    @staticmethod
    def add_done_callback(
        task_id: Task,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Register a callback that runs when the task completes.

        Args:
            task_id: Task to monitor for completion.
            func: Callback to invoke when the task finishes.
            *args: Positional arguments forwarded to ``func``.
            **kwargs: Keyword arguments forwarded to ``func``.
        """
        ...

    @staticmethod
    def remove_done_callback(task_id: Task, func: Callable[..., Any]) -> None:
        """Remove a previously registered completion callback.

        Args:
            task_id: Task the callback was attached to.
            func: Callback function that should be removed.
        """
        ...

    @staticmethod
    def executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a blocking callable in a background thread and return its result.

        Args:
            func: Synchronous callable to execute.
            *args: Positional arguments forwarded to ``func``.
            **kwargs: Keyword arguments forwarded to ``func``.

        Returns:
            Any: Result returned by ``func``.
        """
        ...

    @staticmethod
    def sleep(seconds: int | float) -> None:
        """Yield control for the given seconds without blocking the event loop.

        Args:
            seconds: Duration to suspend execution; fractional values are allowed.
        """
        ...

    @staticmethod
    def unique(task_name: str, kill_me: bool = False) -> None:
        """Assign a unique name to the current task, optionally killing peers.

        Args:
            task_name: Identifier shared with ``task.name2id`` and other callers.
            kill_me: Cancel the current task if another live task already claimed the name.
        """
        ...

    @staticmethod
    def wait_until(
        state_trigger: str | list[str] | None = None,
        time_trigger: str | list[str] | None = None,
        event_trigger: str | list[str] | None = None,
        mqtt_trigger: str | list[str] | None = None,
        mqtt_trigger_encoding: str | None = None,
        webhook_trigger: str | list[str] | None = None,
        webhook_local_only: bool = True,
        webhook_methods: list[str] = ("POST", "PUT"),
        timeout: int | float | None = None,
        state_check_now: bool = True,
        state_hold: int | float | None = None,
        state_hold_false: int | float | None = None,
    ) -> dict[str, Any]:
        """Block until any supplied trigger fires or a timeout occurs.

        Args:
            state_trigger: State expressions matching ``@state_trigger`` semantics.
            time_trigger: Time specifications matching ``@time_trigger`` semantics.
            event_trigger: Event types or filters matching ``@event_trigger`` semantics.
            mqtt_trigger: MQTT topics or filters matching ``@mqtt_trigger`` semantics.
            mqtt_trigger_encoding: Character encoding for MQTT payload decoding; defaults to ``"utf-8"`` when omitted.
            webhook_trigger: Webhook ids matching ``@webhook_trigger`` semantics.
            webhook_local_only: Limit webhooks to local network clients when ``True``.
            webhook_methods: Allowed HTTP methods for webhook triggers.
            timeout: Seconds to wait before returning ``trigger_type="timeout"``.
            state_check_now: Evaluate state expressions immediately when ``True``.
            state_hold: Seconds a state expression must remain true before returning.
            state_hold_false: Seconds a state expression must remain false before it can trigger again.

        Returns:
            dict[str, Any]: Trigger context mirroring decorator kwargs and always including ``trigger_type``.
        """
        ...


class pyscript(Any):
    """Runtime helpers for inspecting and switching pyscript global contexts."""

    app_config: dict[str, Any]

    @staticmethod
    def get_global_ctx() -> str:
        """Return the name of the current pyscript global context.

        Returns:
            str: Active global context identifier.
        """
        ...

    @staticmethod
    def set_global_ctx(new_ctx_name: str) -> None:
        """Switch the active global context to ``new_ctx_name``.

        Args:
            new_ctx_name: Name of an existing global context to activate.
        """
        ...

    @staticmethod
    def list_global_ctx() -> list[str]:
        """Return available global context names, current first.

        Returns:
            list[str]: Global context names ordered with the active context first.
        """
        ...

    @staticmethod
    def reload() -> None:
        """Trigger a full pyscript reload, covering scripts, apps, and modules."""
        ...


class StateVal:
    """Representation of a Home Assistant entity state value."""

    entity_id: str
    friendly_name: str
    device_class: str
    icon: str
    last_changed: datetime
    last_updated: datetime
    last_reported: datetime

    def as_float(self, default: Any = object()) -> float:
        """Convert the state to ``float`` or return ``default`` on failure.

        Args:
            default: Fallback value used when conversion raises an error or the value is empty.

        Returns:
            float: Parsed float, or ``default`` when provided.
        """
        ...

    def as_int(self, default: Any = object(), base: int = 10) -> int:
        """Convert the state to ``int`` (using ``base``) or return ``default``.

        Args:
            default: Fallback value used when conversion raises an error or the value is empty.
            base: Numeric base to use when interpreting the value.

        Returns:
            int: Parsed integer, or ``default`` when provided.
        """
        ...

    def as_bool(self, default: Any = object()) -> bool:
        """Interpret the state as ``bool`` or return ``default``.

        Args:
            default: Fallback value used when conversion raises an error or the value is empty.

        Returns:
            bool: Parsed boolean, or ``default`` when provided.
        """
        ...

    def as_round(
        self,
        precision: int = 0,
        method: Literal["common", "ceil", "floor", "half"] = "common",
        default: Any = object(),
    ) -> float:
        """Convert the state to ``float`` and round it using the requested strategy.

        Args:
            precision: Decimal places to keep after rounding.
            method: Rounding strategy supported by ``homeassistant.helpers.template``.
            default: Fallback value used when conversion fails.

        Returns:
            float: Rounded floating-point value, or ``default`` when provided.
        """
        ...

    def as_datetime(self, default: Any = object()) -> datetime:
        """Parse the state into a timezone-aware ``datetime`` if possible.

        Args:
            default: Fallback value used when parsing fails.

        Returns:
            datetime: Parsed datetime, or ``default`` when provided.
        """
        ...

    def is_unknown(self) -> bool:
        """Return whether the entity reports the ``unknown`` sentinel.

        Returns:
            bool: ``True`` if the state equals ``unknown``.
        """
        ...

    def is_unavailable(self) -> bool:
        """Return whether the entity reports the ``unavailable`` sentinel.

        Returns:
            bool: ``True`` if the state equals ``unavailable``.
        """
        ...

    def has_value(self) -> bool:
        """Return whether the entity has a concrete (non-empty) value.

        Returns:
            bool: ``True`` if a non-empty state value is available.
        """
        ...
