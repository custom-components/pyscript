"""Unit tests for decorator managers."""

from __future__ import annotations

import logging
from typing import ClassVar
from unittest.mock import patch

import pytest
import voluptuous as vol

from custom_components.pyscript.const import CONF_HASS_IS_GLOBAL, CONFIG_ENTRY, DOMAIN
from custom_components.pyscript.decorator import (
    DecoratorRegistry,
    FunctionDecoratorManager,
    WaitUntilDecoratorManager,
)
from custom_components.pyscript.decorator_abc import (
    CallHandlerDecorator,
    CallResultHandlerDecorator,
    Decorator,
    DecoratorManager,
    DecoratorManagerStatus,
    DispatchData,
)
import custom_components.pyscript.decorators.base as decorators_base_module
from custom_components.pyscript.decorators.base import AutoKwargsDecorator, ExpressionDecorator
from custom_components.pyscript.function import Function
import custom_components.pyscript.global_ctx as global_ctx_module
from custom_components.pyscript.global_ctx import GlobalContext
from homeassistant.core import Context, HomeAssistant

_MISSING = object()
_REGISTRY_ATTR = "_decorators"
_EXPRESSION_ATTR = "_ast_expression"
_CALL_METHOD = "_call"


@pytest.fixture(autouse=True)
def restore_manager_globals():
    """Restore shared class state touched by these unit tests."""
    old_decorators = get_registry_decorators(default=_MISSING)
    old_hass = getattr(DecoratorManager, "hass", _MISSING)

    yield

    if old_decorators is _MISSING:
        if hasattr(DecoratorRegistry, _REGISTRY_ATTR):
            delattr(DecoratorRegistry, _REGISTRY_ATTR)
    else:
        set_registry_decorators(old_decorators)

    if old_hass is _MISSING:
        if hasattr(DecoratorManager, "hass"):
            delattr(DecoratorManager, "hass")
    else:
        DecoratorManager.hass = old_hass


class DummyAstCtx:
    """Minimal AstEval stub for manager unit tests."""

    def __init__(self, name: str = "file.hello.func") -> None:
        """Initialize a dummy AST context."""
        self.name = name
        self.global_ctx = object()
        self.logged_exceptions: list[Exception] = []
        self._logger = logging.getLogger(__name__)

    def get_logger(self):
        """Return test logger."""
        return self._logger

    def get_global_ctx_name(self) -> str:
        """Return global context name."""
        return "file.hello"

    def log_exception(self, exc: Exception) -> None:
        """Record exceptions passed to the AST context."""
        self.logged_exceptions.append(exc)


class DummyManager(DecoratorManager):
    """Concrete manager used to unit-test the abstract base logic."""

    def __init__(self, ast_ctx: DummyAstCtx, name: str = "file.hello.func") -> None:
        """Initialize the dummy manager."""
        super().__init__(ast_ctx, name)
        self.dispatched: list[DispatchData] = []

    async def dispatch(self, data: DispatchData) -> None:
        """Store dispatched payloads."""
        self.dispatched.append(data)


class RecordingDecorator(Decorator):
    """Decorator that records lifecycle calls."""

    name = "recording"

    label: str
    events: list[tuple[str, str]]
    validate_exc: Exception | None = None
    start_exc: Exception | None = None
    stop_exc: Exception | None = None

    async def validate(self) -> None:
        """Record validation and optionally fail."""
        self.events.append(("validate", self.label))
        if self.validate_exc is not None:
            raise self.validate_exc

    async def start(self) -> None:
        """Record startup and optionally fail."""
        self.events.append(("start", self.label))
        if self.start_exc is not None:
            raise self.start_exc

    async def stop(self) -> None:
        """Record shutdown and optionally fail."""
        self.events.append(("stop", self.label))
        if self.stop_exc is not None:
            raise self.stop_exc


class CancelCallHandler(CallHandlerDecorator):
    """Call handler that cancels the action call."""

    name = "cancel_call"
    seen: list[dict]

    async def handle_call(self, data: DispatchData) -> bool:
        """Cancel the action call."""
        self.seen.append(data.func_args.copy())
        return False


class RecordingResultHandler(CallResultHandlerDecorator):
    """Result handler that stores received results."""

    name = "record_result"
    results: list[object]

    async def handle_call_result(self, data: DispatchData, result: object) -> None:
        """Record action result."""
        self.results.append(result)


class AutoKwargsTestDecorator(AutoKwargsDecorator):
    """Decorator used to test AutoKwargsDecorator behavior."""

    name = "auto_kwargs_test"
    kwargs_schema = vol.Schema(
        {
            vol.Optional("enabled"): bool,
            vol.Optional("count"): int,
            vol.Optional("ignored"): str,
        }
    )

    enabled: bool | None
    count: int | None


class ExpressionTestDecorator(ExpressionDecorator):
    """Decorator used to test ExpressionDecorator behavior."""

    name = "expression_test"


class FailingExpression:
    """Expression stub that always raises during evaluation."""

    async def eval(self, state_vars: dict[str, object]) -> bool:
        """Raise an evaluation error."""
        raise RuntimeError(f"eval failed: {state_vars['value']}")


def make_recording_decorator(
    label: str,
    events: list[tuple[str, str]],
    *,
    validate_exc: Exception | None = None,
    start_exc: Exception | None = None,
    stop_exc: Exception | None = None,
) -> RecordingDecorator:
    """Create a RecordingDecorator without overriding Decorator.__init__."""
    decorator = RecordingDecorator([], {})
    decorator.label = label
    decorator.events = events
    decorator.validate_exc = validate_exc
    decorator.start_exc = start_exc
    decorator.stop_exc = stop_exc
    return decorator


def make_cancel_call_handler() -> CancelCallHandler:
    """Create a canceling call handler."""
    handler = CancelCallHandler([], {})
    handler.seen = []
    return handler


def make_recording_result_handler() -> RecordingResultHandler:
    """Create a recording result handler."""
    handler = RecordingResultHandler([], {})
    handler.results = []
    return handler


def get_registry_decorators(default: object | None = None) -> object | None:
    """Return the decorator registry mapping."""
    return getattr(DecoratorRegistry, _REGISTRY_ATTR, default)


def set_registry_decorators(decorators: object) -> None:
    """Replace the decorator registry mapping."""
    setattr(DecoratorRegistry, _REGISTRY_ATTR, decorators)


def set_decorator_ast_expression(decorator: ExpressionDecorator, expression: object) -> None:
    """Set the internal AstEval expression for a test decorator."""
    setattr(decorator, _EXPRESSION_ATTR, expression)


async def call_function_manager(manager: FunctionDecoratorManager, data: DispatchData) -> None:
    """Invoke the protected function-manager call path in tests."""
    await getattr(manager, _CALL_METHOD)(data)


class FakeAstEvalForExpression:
    """AstEval stub that records create_expression inputs."""

    instances: ClassVar[list["FakeAstEvalForExpression"]] = []

    def __init__(self, name: str, global_ctx: object, local_name: str) -> None:
        """Initialize the fake AstEval stub."""
        self.name = name
        self.global_ctx = global_ctx
        self.local_name = local_name
        self.parse_calls: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def parse(self, expression: str, mode: str) -> None:
        """Record parse invocations."""
        self.parse_calls.append((expression, mode))


class DummyEvalFunc:
    """Minimal EvalFunc stub for FunctionDecoratorManager tests."""

    def __init__(self, name: str = "func") -> None:
        """Initialize the dummy eval function."""
        self.name = name
        self.global_ctx_name = "file.hello"
        self.logger = logging.getLogger(__name__)


class DummyEvalFuncVar:
    """Minimal EvalFuncVar stub for FunctionDecoratorManager tests."""

    def __init__(self, name: str = "func") -> None:
        """Initialize the dummy eval function wrapper."""
        self.func = DummyEvalFunc(name)

    def get_name(self) -> str:
        """Return function name."""
        return self.func.name


class DummyCallAstCtx:
    """Minimal action AstEval stub for manager call tests."""

    def __init__(self, result: object) -> None:
        """Initialize the dummy action context."""
        self.result = result
        self.calls: list[tuple[object, object, dict]] = []

    async def call_func(self, func: object, func_name: object, **kwargs: object) -> object:
        """Record the function call and return the configured result."""
        self.calls.append((func, func_name, kwargs))
        return self.result


class DummyConfigEntry:
    """Minimal config entry stub for GlobalContext tests."""

    def __init__(self, data: dict) -> None:
        """Initialize the dummy config entry."""
        self.data = data


class DummyAsyncManager:
    """Minimal async manager stub for GlobalContext start/stop tests."""

    def __init__(self) -> None:
        """Initialize the dummy async manager."""
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        """Record manager start."""
        self.start_calls += 1

    async def stop(self) -> None:
        """Record manager stop."""
        self.stop_calls += 1


class FakeFunctionDecoratorManager:
    """Patchable manager stub for GlobalContext.create_decorator_manager tests."""

    instances: ClassVar[list["FakeFunctionDecoratorManager"]] = []
    status_after_validate: ClassVar[DecoratorManagerStatus] = DecoratorManagerStatus.VALIDATED
    validate_exception: ClassVar[Exception | None] = None

    def __init__(self, ast_ctx: DummyAstCtx, func_var: DummyEvalFuncVar) -> None:
        """Initialize the fake function decorator manager."""
        self.ast_ctx = ast_ctx
        self.func_var = func_var
        self.status = DecoratorManagerStatus.INIT
        self.added = []
        self.validate_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.__class__.instances.append(self)

    async def validate(self) -> None:
        """Record validation and apply the configured result."""
        self.validate_calls += 1
        if self.__class__.validate_exception is not None:
            raise self.__class__.validate_exception
        self.status = self.__class__.status_after_validate

    def add(self, decorator: Decorator) -> None:
        """Record added decorators."""
        self.added.append(decorator)

    async def start(self) -> None:
        """Record manager start."""
        self.start_calls += 1

    async def stop(self) -> None:
        """Record manager stop."""
        self.stop_calls += 1


def make_dispatch_data(
    func_args: dict[str, object],
    *,
    call_ast_ctx: DummyCallAstCtx | None = None,
    hass_context: Context | None = None,
) -> DispatchData:
    """Build DispatchData from test doubles."""
    return DispatchData(func_args, call_ast_ctx=call_ast_ctx, hass_context=hass_context)


def setup_global_context_function_hass(hass: HomeAssistant, config_data: dict | None = None) -> None:
    """Configure Function.hass prerequisites needed by GlobalContext."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: DummyConfigEntry(config_data or {})}


@pytest.mark.asyncio
async def test_decorator_manager_no_decorators_and_accessors():
    """Validate empty-manager lifecycle behavior."""
    dm = DummyManager(DummyAstCtx())
    await dm.validate()
    assert dm.status is DecoratorManagerStatus.NO_DECORATORS

    decorators = dm.get_decorators()
    decorators.append("sentinel")
    assert dm.get_decorators() == []

    dm.update_status(DecoratorManagerStatus.NO_DECORATORS)
    assert dm.status is DecoratorManagerStatus.NO_DECORATORS

    with pytest.raises(RuntimeError, match="Starting not valid"):
        await dm.start()


@pytest.mark.asyncio
async def test_decorator_manager_start_rolls_back_started_decorators():
    """A later start failure should stop already-started decorators."""
    events: list[tuple[str, str]] = []
    dm = DummyManager(DummyAstCtx())
    first = make_recording_decorator("first", events)
    second = make_recording_decorator("second", events, start_exc=RuntimeError("start failed"))
    dm.add(first)
    dm.add(second)

    await dm.validate()

    with pytest.raises(RuntimeError, match="start failed"):
        await dm.start()

    assert ("start", "first") in events
    assert ("start", "second") in events
    assert ("stop", "first") in events
    assert ("stop", "second") not in events
    assert dm.status is DecoratorManagerStatus.INVALID
    assert dm.startup_time is None
    assert dm.get_decorators() == []


@pytest.mark.asyncio
async def test_auto_kwargs_decorator_validate_sets_only_annotated_attrs():
    """AutoKwargsDecorator should materialize only annotated kwargs."""
    dm = DummyManager(DummyAstCtx())
    decorator = AutoKwargsTestDecorator([], {"enabled": True, "ignored": "x"})
    dm.add(decorator)
    await decorator.validate()

    assert decorator.enabled is True
    assert decorator.count is None
    assert not hasattr(decorator, "ignored")


@pytest.mark.asyncio
async def test_expression_decorator_requires_expression_before_eval():
    """ExpressionDecorator should raise if no expression was created."""
    dm = DummyManager(DummyAstCtx())
    decorator = ExpressionTestDecorator([], {})
    dm.add(decorator)

    with pytest.raises(AttributeError, match="has no expression defined"):
        await decorator.check_expression_vars({})


@pytest.mark.asyncio
async def test_expression_decorator_logs_eval_exceptions_via_manager():
    """ExpressionDecorator should route eval exceptions through the manager."""
    ast_ctx = DummyAstCtx()
    dm = DummyManager(ast_ctx)
    decorator = ExpressionTestDecorator([], {})
    dm.add(decorator)
    set_decorator_ast_expression(decorator, FailingExpression())

    assert await decorator.check_expression_vars({"value": 7}) is False
    assert len(ast_ctx.logged_exceptions) == 1
    assert str(ast_ctx.logged_exceptions[0]) == "eval failed: 7"


def test_expression_decorator_create_expression_uses_manager_context():
    """create_expression() should build AstEval with the manager context."""
    FakeAstEvalForExpression.instances = []
    dm = DummyManager(DummyAstCtx())
    decorator = ExpressionTestDecorator([], {})
    dm.add(decorator)

    with (
        patch.object(decorators_base_module, "AstEval", FakeAstEvalForExpression),
        patch.object(Function, "install_ast_funcs") as install_ast_funcs,
    ):
        decorator.create_expression("value > 1")

    assert decorator.has_expression() is True
    assert len(FakeAstEvalForExpression.instances) == 1
    ast_eval = FakeAstEvalForExpression.instances[0]
    assert ast_eval.name == "file.hello.func expression_test"
    assert ast_eval.global_ctx is dm.ast_ctx.global_ctx
    assert ast_eval.local_name == dm.name
    assert ast_eval.parse_calls == [("value > 1", "eval")]
    install_ast_funcs.assert_called_once_with(ast_eval)


def test_expression_decorator_create_expression_formats_function_manager_name():
    """create_expression() should use @name() form for function decorator managers."""
    FakeAstEvalForExpression.instances = []
    manager = FunctionDecoratorManager(DummyAstCtx(), DummyEvalFuncVar())
    decorator = ExpressionTestDecorator([], {})
    manager.add(decorator)

    with (
        patch.object(decorators_base_module, "AstEval", FakeAstEvalForExpression),
        patch.object(Function, "install_ast_funcs") as install_ast_funcs,
    ):
        decorator.create_expression("value > 1")

    assert len(FakeAstEvalForExpression.instances) == 1
    ast_eval = FakeAstEvalForExpression.instances[0]
    assert ast_eval.name == "file.hello.func @expression_test()"
    assert ast_eval.global_ctx is manager.ast_ctx.global_ctx
    assert ast_eval.local_name == manager.name
    assert ast_eval.parse_calls == [("value > 1", "eval")]
    install_ast_funcs.assert_called_once_with(ast_eval)


@pytest.mark.asyncio
async def test_wait_until_rejects_unknown_arguments(hass):
    """task.wait_until should reject kwargs that do not map to decorators."""
    DecoratorManager.hass = hass
    set_registry_decorators({})

    with pytest.raises(ValueError, match="Unknown arguments"):
        await DecoratorRegistry.wait_until(DummyAstCtx(), unexpected=1)


@pytest.mark.asyncio
async def test_wait_until_ignores_dispatch_after_completion(hass):
    """Repeated dispatches after completion should be ignored."""
    DecoratorManager.hass = hass
    dm = WaitUntilDecoratorManager(DummyAstCtx())
    dm.update_status(DecoratorManagerStatus.RUNNING)
    trigger = object()

    await dm.dispatch(DispatchData({"value": 1}, trigger=trigger))
    await dm.dispatch(DispatchData({"value": 2}, trigger=trigger))

    assert await dm.wait_until() == {"value": 1}
    assert dm.status is DecoratorManagerStatus.STOPPED


@pytest.mark.asyncio
async def test_wait_until_ignores_exception_after_completion(hass):
    """Late exceptions should not override an already completed result."""
    DecoratorManager.hass = hass
    dm = WaitUntilDecoratorManager(DummyAstCtx())
    dm.update_status(DecoratorManagerStatus.RUNNING)
    trigger = object()

    await dm.dispatch(DispatchData({"value": 1}, trigger=trigger))
    await dm.handle_exception(RuntimeError("late"))

    assert await dm.wait_until() == {"value": 1}


@pytest.mark.asyncio
async def test_function_decorator_manager_cancel_calls_result_handlers(hass):
    """Canceled calls should still notify result handlers with None."""
    DecoratorManager.hass = hass
    manager = FunctionDecoratorManager(DummyAstCtx(), DummyEvalFuncVar())
    call_handler = make_cancel_call_handler()
    result_handler = make_recording_result_handler()
    call_ast_ctx = DummyCallAstCtx(result="unused")
    manager.add(call_handler)
    manager.add(result_handler)

    await call_function_manager(
        manager,
        make_dispatch_data({"arg1": 1}, call_ast_ctx=call_ast_ctx, hass_context=Context(id="call-parent")),
    )

    assert call_handler.seen == [{"arg1": 1}]
    assert result_handler.results == [None]
    assert not call_ast_ctx.calls


@pytest.mark.asyncio
async def test_function_decorator_manager_success_calls_result_handlers(hass):
    """Successful calls should pass the function result to result handlers."""
    DecoratorManager.hass = hass
    manager = FunctionDecoratorManager(DummyAstCtx(), DummyEvalFuncVar())
    result_handler = make_recording_result_handler()
    call_ast_ctx = DummyCallAstCtx(result="ok")
    manager.add(result_handler)
    hass_context = Context(id="call-parent")
    fired_events = []

    def event_listener(event):
        fired_events.append(event)

    hass.bus.async_listen("pyscript_running", event_listener)

    with patch.object(Function, "store_hass_context") as store_hass_context:
        await call_function_manager(
            manager, make_dispatch_data({"arg1": 1}, call_ast_ctx=call_ast_ctx, hass_context=hass_context)
        )
        await hass.async_block_till_done()

    assert call_ast_ctx.calls == [(manager.eval_func, None, {"arg1": 1})]
    assert result_handler.results == ["ok"]
    assert len(fired_events) == 1
    assert fired_events[0].data == {
        "name": "file_hello_func",
        "entity_id": "pyscript.file_hello_func",
        "func_args": {"arg1": 1},
    }
    store_hass_context.assert_called_once_with(hass_context)


def test_decorator_registry_register_requires_name():
    """Registry should reject decorators without a declared name."""

    class NamelessDecorator(Decorator):
        pass

    set_registry_decorators({})

    with pytest.raises(TypeError, match="Decorator name is required"):
        DecoratorRegistry.register(NamelessDecorator)


def test_decorator_registry_warns_on_override(caplog):
    """Registering the same decorator name twice should warn."""

    class FirstDecorator(Decorator):
        name = "duplicate"

    class SecondDecorator(Decorator):
        name = "duplicate"

    set_registry_decorators({})

    DecoratorRegistry.register(FirstDecorator)
    with caplog.at_level(logging.WARNING):
        DecoratorRegistry.register(SecondDecorator)

    assert "Overriding decorator: duplicate" in caplog.text
    assert get_registry_decorators()["duplicate"] is SecondDecorator


def test_global_context_initializes_hass_and_app_config(hass):
    """GlobalContext should expose hass and copy app_config when configured."""
    setup_global_context_function_hass(hass, {CONF_HASS_IS_GLOBAL: True})
    app_config = {"name": "demo"}

    with patch.object(Function, "hass", hass):
        global_ctx = GlobalContext("file.hello", app_config=app_config)

    assert global_ctx.global_sym_table["hass"] is hass
    assert global_ctx.global_sym_table["pyscript.app_config"] == {"name": "demo"}
    assert global_ctx.global_sym_table["pyscript.app_config"] is not app_config


@pytest.mark.asyncio
async def test_global_context_start_and_stop_schedule_decorator_managers(hass):
    """start() and stop() should fan out to delayed decorator managers."""
    setup_global_context_function_hass(hass)

    with patch.object(Function, "hass", hass):
        global_ctx = GlobalContext("file.hello")
        manager = DummyAsyncManager()

        global_ctx.dms.add(manager)
        global_ctx.dms_delay_start.add(manager)

        global_ctx.start()
        await hass.async_block_till_done()

        assert manager.start_calls == 1
        assert global_ctx.dms_delay_start == set()

        global_ctx.stop()
        await hass.async_block_till_done()

    assert manager.stop_calls == 1
    assert global_ctx.dms == set()
    assert global_ctx.dms_delay_start == set()
    assert global_ctx.auto_start is False


@pytest.mark.asyncio
async def test_global_context_create_decorator_manager_delays_or_autostarts(hass):
    """Validated decorator managers should be delayed or started based on auto_start."""
    setup_global_context_function_hass(hass)
    FakeFunctionDecoratorManager.instances = []
    FakeFunctionDecoratorManager.status_after_validate = DecoratorManagerStatus.VALIDATED
    FakeFunctionDecoratorManager.validate_exception = None
    delayed_ast_ctx = DummyAstCtx("file.hello.func_delayed")
    immediate_ast_ctx = DummyAstCtx("file.hello.func_immediate")
    func_var = DummyEvalFuncVar()
    decorators = [make_recording_decorator("one", [])]

    with (
        patch.object(Function, "hass", hass),
        patch.object(global_ctx_module, "FunctionDecoratorManager", FakeFunctionDecoratorManager),
    ):
        delayed_ctx = GlobalContext("file.hello")
        await delayed_ctx.create_decorator_manager(decorators, delayed_ast_ctx, func_var)

        immediate_ctx = GlobalContext("file.hello2")
        immediate_ctx.set_auto_start(True)
        await immediate_ctx.create_decorator_manager(decorators, immediate_ast_ctx, func_var)

    assert len(FakeFunctionDecoratorManager.instances) == 2
    delayed_dm = FakeFunctionDecoratorManager.instances[0]
    immediate_dm = FakeFunctionDecoratorManager.instances[1]

    assert delayed_dm.added == decorators
    assert delayed_dm.validate_calls == 1
    assert delayed_dm.start_calls == 0
    assert delayed_dm in delayed_ctx.dms
    assert delayed_dm in delayed_ctx.dms_delay_start

    assert immediate_dm.added == decorators
    assert immediate_dm.validate_calls == 1
    assert immediate_dm.start_calls == 1
    assert immediate_dm in immediate_ctx.dms
    assert immediate_dm not in immediate_ctx.dms_delay_start


@pytest.mark.asyncio
async def test_global_context_create_decorator_manager_ignores_non_validated_status(hass):
    """Managers that do not validate successfully should not be registered."""
    setup_global_context_function_hass(hass)
    FakeFunctionDecoratorManager.instances = []
    FakeFunctionDecoratorManager.status_after_validate = DecoratorManagerStatus.NO_DECORATORS
    FakeFunctionDecoratorManager.validate_exception = None
    ast_ctx = DummyAstCtx()

    with (
        patch.object(Function, "hass", hass),
        patch.object(global_ctx_module, "FunctionDecoratorManager", FakeFunctionDecoratorManager),
    ):
        global_ctx = GlobalContext("file.hello")
        await global_ctx.create_decorator_manager(
            [make_recording_decorator("one", [])], ast_ctx, DummyEvalFuncVar()
        )

    assert FakeFunctionDecoratorManager.instances[0].validate_calls == 1
    assert global_ctx.dms == set()
    assert global_ctx.dms_delay_start == set()
    assert not ast_ctx.logged_exceptions


@pytest.mark.asyncio
async def test_global_context_create_decorator_manager_logs_validation_exception(hass):
    """Validation exceptions should be logged on the AST context."""
    setup_global_context_function_hass(hass)
    FakeFunctionDecoratorManager.instances = []
    FakeFunctionDecoratorManager.status_after_validate = DecoratorManagerStatus.VALIDATED
    FakeFunctionDecoratorManager.validate_exception = RuntimeError("validation failed")
    ast_ctx = DummyAstCtx()

    with (
        patch.object(Function, "hass", hass),
        patch.object(global_ctx_module, "FunctionDecoratorManager", FakeFunctionDecoratorManager),
    ):
        global_ctx = GlobalContext("file.hello")
        await global_ctx.create_decorator_manager(
            [make_recording_decorator("one", [])], ast_ctx, DummyEvalFuncVar()
        )

    assert FakeFunctionDecoratorManager.instances[0].validate_calls == 1
    assert len(ast_ctx.logged_exceptions) == 1
    assert str(ast_ctx.logged_exceptions[0]) == "validation failed"
    assert global_ctx.dms == set()
    assert global_ctx.dms_delay_start == set()


def test_decorator_registry_init_legacy_mode_skips_new_registry(hass, caplog, monkeypatch):
    """Legacy-mode env should disable the new decorator registry."""
    monkeypatch.setenv("NODM", "1")

    with patch.object(Function, "register_ast") as register_ast:
        DecoratorRegistry.init(hass)

    assert "Using legacy decorators" in caplog.text
    register_ast.assert_not_called()
    assert not get_registry_decorators()
