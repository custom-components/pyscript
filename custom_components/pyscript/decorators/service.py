"""Service decorator implementation."""

from __future__ import annotations

import ast
from collections import OrderedDict
import copy
import io
import logging

import voluptuous as vol
import yaml

from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_set_service_schema

from .. import DOMAIN, SERVICE_JUPYTER_KERNEL_START, Function, State
from ..decorator import FunctionDecoratorManager
from ..decorator_abc import DispatchData
from .base import RequestResponseDecorator

_LOGGER = logging.getLogger(__name__)

# the UI input for a parameter annotated with a simple type
_ANNOTATION_SELECTORS = {
    "bool": {"boolean": {}},
    "int": {"number": {}},
    "float": {"number": {"mode": "box", "step": "any"}},
    "str": {"text": {}},
    "dict": {"object": {}},
    "list": {"object": {}},
}


def _annotation_selector(annotation: ast.expr | None) -> dict | None:
    """Return the selector for a parameter's type annotation, or None if there's no fitting one."""
    match annotation:
        case (
            ast.BinOp(op=ast.BitOr(), left=inner, right=ast.Constant(value=None))
            | ast.BinOp(op=ast.BitOr(), left=ast.Constant(value=None), right=inner)
        ):
            return _annotation_selector(inner)
        case ast.Subscript(value=ast.Name(id="Optional") | ast.Attribute(attr="Optional"), slice=inner):
            return _annotation_selector(inner)
        case ast.Subscript(value=inner):
            # a parameterized type, eg: list[int]
            return _annotation_selector(inner)
        case ast.Name(id=name) if name in _ANNOTATION_SELECTORS:
            return copy.deepcopy(_ANNOTATION_SELECTORS[name])
    return None


def _constant_default(default: ast.expr) -> bool | int | float | str | None:
    """
    Return a default value written as a constant in the signature, or None.

    The description is published to every client, so a computed default (eg: a secret from
    the config) must not end up in it.
    """
    match default:
        case ast.Constant(value=bool() | int() | float() | str() as value):
            return value
        case ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=int() | float() as value)) if (
            not isinstance(value, bool)
        ):
            return -value
    return None


def service_validator(args: list[str]) -> list[str]:
    """Validate and normalize service name."""
    if len(args) == 0:
        return []
    s = str(args[0]).strip()

    if not isinstance(s, str):
        raise vol.Invalid("must be string")
    s = s.strip()
    if s.count(".") != 1:
        raise vol.Invalid("argument 1 should be a string with one period")
    domain, name = s.split(".", 1)
    return [domain, name]


class ServiceDecorator(RequestResponseDecorator):
    """
    Implementation for @service.

    A service call is an explicit call, not a trigger: it starts the call directly, so trigger
    handlers (@state_active, @time_active) don't apply, while call handlers (@task_unique) do.
    """

    name = "service"
    args_schema = vol.Schema(vol.All(vol.Length(max=1), service_validator))
    kwargs_schema = vol.Schema(
        {vol.Optional("supports_response", default=SupportsResponse.NONE): vol.Coerce(SupportsResponse)}
    )

    # @service is only applied to functions
    dm: FunctionDecoratorManager
    description: dict
    data_schema: vol.Schema

    async def validate(self) -> None:
        """Validate the arguments."""
        await super().validate()

        if len(self.args) != 2:
            self.args = [DOMAIN, self.dm.func_name]
        # This condition still does not verify the domain. Keep the behavior
        # for transition compatibility and revisit it after the legacy
        # subsystem is removed.
        if self.args[1] in (SERVICE_RELOAD, SERVICE_JUPYTER_KERNEL_START):
            # Keep this wording for transition compatibility. Once the legacy
            # subsystem is removed, update the message and related tests.
            raise SyntaxError(
                f"function '{self.dm.func_name}' defined in {self.dm.ast_ctx.get_global_ctx_name()}: "
                f"@service conflicts with builtin service"
            )

        ast_funcdef = self.dm.eval_func.func_def
        args = ast_funcdef.args
        # the default value expressions; they belong to the last positional parameters,
        # positional-only ones included
        positional = args.posonlyargs + args.args
        with_default = positional[len(positional) - len(args.defaults) :]
        defaults = {arg.arg: default for arg, default in zip(with_default, args.defaults, strict=True)}
        defaults |= {
            arg.arg: default
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            if default is not None
        }
        # the arguments a service call passes to the function besides the service data
        call_added = ("trigger_type", "context")
        # the parameters the caller fills: positional-only ones can't be passed by name,
        # and the ones a service call adds itself are set even when not passed
        params = [arg.arg for arg in args.args + args.kwonlyargs if arg.arg not in call_added]
        required = {name for name in params if name not in defaults}

        desc = ast.get_docstring(ast_funcdef)
        if desc is None or desc == "":
            desc = f"pyscript function {ast_funcdef.name}()"
        desc = desc.lstrip(" \n\r")
        if desc.startswith("yaml"):
            try:
                desc = desc[4:].lstrip(" \n\r")
                file_desc = io.StringIO(desc)
                self.description = yaml.safe_load(file_desc) or OrderedDict()
                file_desc.close()
            except Exception as exc:
                self.dm.logger.error(
                    "Unable to decode yaml doc_string for %s(): %s",
                    ast_funcdef.name,
                    str(exc),
                )
                raise exc
        else:
            annotations = {arg.arg: arg.annotation for arg in args.args + args.kwonlyargs}
            fields = OrderedDict()
            for name in params:
                field = fields[name] = OrderedDict()
                if name in required:
                    field["required"] = True
                elif (default := _constant_default(defaults[name])) is not None:
                    field["default"] = default
                if selector := _annotation_selector(annotations[name]):
                    field["selector"] = selector
            self.description = {"description": desc, "fields": fields}

        # Like other integrations' services, Home Assistant checks the service data against this
        # schema before the call, so a typo in a field name or a missing argument fails the call
        self.data_schema = vol.Schema(
            {(vol.Required if name in required else vol.Optional)(name): object for name in params},
            extra=vol.ALLOW_EXTRA if args.kwarg else vol.PREVENT_EXTRA,
        )

    async def _service_callback(self, call: ServiceCall) -> ServiceResponse:
        _LOGGER.info("Service callback: %s", call.service)

        func_args = {
            "trigger_type": "service",
            "context": call.context,
        }
        func_args.update(call.data)

        result = await self.handle_request(func_args)
        if call.return_response and result is None:
            # like HA scripts: a call that was skipped or returned nothing gives an empty response
            return {}
        return result

    async def dispatch_request(self, data: DispatchData) -> None:
        """Start the call directly: a service call is an explicit call, not a trigger."""
        await self.dm.start_call(data)

    async def handle_call_exception(self, data: DispatchData, exc: Exception) -> None:
        """Fail the service call when the function raised; the manager still logs the exception."""
        if not isinstance(exc, HomeAssistantError):
            error = HomeAssistantError(f"{self.dm.name}() raised {type(exc).__name__}: {exc}")
            error.__cause__ = exc
            exc = error
        self.respond(data, exc=exc)

    async def start(self) -> None:
        """Register the service."""
        domain = self.args[0]
        name = self.args[1]
        _LOGGER.debug("Registering service: %s.%s", domain, name)
        Function.service_register(
            self.dm.ast_ctx.name,
            domain,
            name,
            self._service_callback,
            self.kwargs.get("supports_response"),
            self.data_schema,
        )
        async_set_service_schema(Function.hass, domain, name, self.description)

        # update service params. In the legacy implementation, Pyscript services were registered
        # right after the function definition, then decorators were executed, and finally the
        # service cache was updated.
        await State.get_service_params()

    async def stop(self) -> None:
        """Unregister the service."""
        _LOGGER.debug("Unregistering service: %s.%s", self.args[0], self.args[1])
        Function.service_remove(self.dm.ast_ctx.global_ctx.get_name(), self.args[0], self.args[1])
