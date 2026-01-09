"""Service decorator implementation."""

from __future__ import annotations

import ast
from collections import OrderedDict
import io
import logging
import typing

import voluptuous as vol
import yaml

from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers.service import async_set_service_schema

from .. import DOMAIN, SERVICE_JUPYTER_KERNEL_START, AstEval, Function, State
from ..decorator import FunctionDecoratorManager
from ..decorator_abc import Decorator

_LOGGER = logging.getLogger(__name__)


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


class ServiceDecorator(Decorator):
    """Implementation for @service."""

    name = "service"
    args_schema = vol.Schema(vol.All(vol.Length(max=1), service_validator))
    kwargs_schema = vol.Schema(
        {vol.Optional("supports_response", default=SupportsResponse.NONE): vol.Coerce(SupportsResponse)}
    )

    description: dict

    async def validate(self) -> None:
        """Validate the arguments."""
        await super().validate()

        if len(self.args) != 2:
            self.args = [DOMAIN, self.dm.func_name]
        # FIXME This condition doesn't verify the domain - it may not be Pyscript.
        #       The error is kept for backward compatibility.
        if self.args[1] in (SERVICE_RELOAD, SERVICE_JUPYTER_KERNEL_START):
            # FIXME For test compatibility. Update the message in the future.
            raise SyntaxError(
                f"function '{self.dm.func_name}' defined in {self.dm.ast_ctx.get_global_ctx_name()}: "
                f"@service conflicts with builtin service"
            )

        ast_funcdef = typing.cast(FunctionDecoratorManager, self.dm).eval_func.func_def
        desc = ast.get_docstring(ast_funcdef)
        if desc is None or desc == "":
            desc = f"pyscript function {ast_funcdef.name}()"
        desc = desc.lstrip(" \n\r")
        if desc.startswith("yaml"):
            try:
                desc = desc[4:].lstrip(" \n\r")
                file_desc = io.StringIO(desc)
                self.description = yaml.load(file_desc, Loader=yaml.BaseLoader) or OrderedDict()
                file_desc.close()
            except Exception as exc:
                self.dm.logger.error(
                    "Unable to decode yaml doc_string for %s(): %s",
                    ast_funcdef.name,
                    str(exc),
                )
                raise exc
        else:
            fields = OrderedDict()
            for arg in ast_funcdef.args.posonlyargs + ast_funcdef.args.args:
                fields[arg.arg] = OrderedDict(description=f"argument {arg.arg}")
            self.description = {"description": desc, "fields": fields}

    async def _service_callback(self, call: ServiceCall) -> None:
        _LOGGER.info("Service callback: %s", call.service)

        # use a new AstEval context so it can run    fully independently
        # of other instances (except for global_ctx which is common)
        global_ctx = self.dm.eval_func.global_ctx
        ast_ctx = AstEval(self.dm.name, global_ctx)
        Function.install_ast_funcs(ast_ctx)
        func_args = {
            "trigger_type": "service",
            "context": call.context,
        }
        func_args.update(call.data)

        async def do_service_call(func, ast_ctx, data):
            try:
                _LOGGER.debug("Service call start: %s", func.name)
                retval = await func.call(ast_ctx, **data)
                _LOGGER.debug("Service call done: %s", ast_ctx.get_exception_long())
                if ast_ctx.get_exception_obj():
                    ast_ctx.get_logger().error(ast_ctx.get_exception_long())
                return retval
            except Exception as exc:
                _LOGGER.exception(exc)
                return None

        task = Function.create_task(do_service_call(self.dm.eval_func, ast_ctx, func_args))
        await task
        return task.result()

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
