"""Python interpreter for pyscript."""

import ast
import asyncio
import builtins
from collections import OrderedDict
import functools
import importlib
import inspect
import io
import keyword
import logging
import sys
import time
import traceback
from types import CellType, CodeType, FunctionType, MethodType, TracebackType
from typing import TYPE_CHECKING, Any

import yaml

from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import SupportsResponse
from homeassistant.helpers.service import async_set_service_schema

from .const import (
    ALLOWED_IMPORTS,
    CONF_ALLOW_ALL_IMPORTS,
    CONFIG_ENTRY,
    DOMAIN,
    LOGGER_PATH,
    SERVICE_JUPYTER_KERNEL_START,
)
from .function import Function
from .state import State

if TYPE_CHECKING:
    from .global_ctx import GlobalContext

type SymTable = dict[str, Any]

_LOGGER = logging.getLogger(LOGGER_PATH + ".eval")

#
# Built-ins to exclude to improve security or avoid i/o
#
BUILTIN_EXCLUDE = {
    "breakpoint",
    "compile",
    "input",
    "memoryview",
    "open",
    "print",
}

TRIG_DECORATORS = {
    "time_trigger",
    "state_trigger",
    "event_trigger",
    "mqtt_trigger",
    "webhook_trigger",
    "state_active",
    "time_active",
    "task_unique",
}

TRIG_SERV_DECORATORS = TRIG_DECORATORS.union({"service"})

COMP_DECORATORS = {
    "pyscript_compile",
    "pyscript_executor",
}

TRIGGER_KWARGS = {
    "context",
    "event_type",
    "old_value",
    "payload",
    "payload_obj",
    "qos",
    "request",
    "retain",
    "topic",
    "trigger_type",
    "trigger_time",
    "var_name",
    "value",
    "webhook_id",
}

WEBHOOK_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
}

#
# AstEval attributes that hold the scope a generator expression's elements are evaluated in
#
GENEXP_SCOPE_ATTRS = (
    "global_ctx",
    "global_sym_table",
    "sym_table",
    "sym_table_stack",
    "curr_func",
    "genexp_iter",
    "code_str",
    "code_list",
    "class_sym_table",
    "class_global_names",
    "loop_scopes",
)


def ast_eval_exec_factory(ast_ctx, mode):
    """Generate a function that executes eval() or exec() with given ast_ctx."""

    async def eval_func(arg_str, eval_globals=None, eval_locals=None):
        eval_ast = AstEval(ast_ctx.name, ast_ctx.global_ctx)
        eval_ast.parse(arg_str, f"{mode}()", mode=mode)
        eval_ast.local_sym_table = ast_ctx.local_sym_table
        if eval_globals is not None:
            eval_ast.global_sym_table = eval_globals
            if eval_locals is not None:
                eval_ast.sym_table_stack = [eval_globals]
                eval_ast.sym_table = eval_locals
            else:
                eval_ast.sym_table_stack = []
                eval_ast.sym_table = eval_globals
        else:
            eval_ast.sym_table_stack = ast_ctx.sym_table_stack.copy()
            if ast_ctx.sym_table == ast_ctx.global_sym_table:
                eval_ast.sym_table = ast_ctx.sym_table
            else:
                eval_ast.sym_table = ast_ctx.sym_table.copy()
                eval_ast.sym_table.update(ast_ctx.user_locals)
                to_delete = set()
                for var, value in eval_ast.sym_table.items():
                    if isinstance(value, EvalLocalVar):
                        if value.is_defined():
                            eval_ast.sym_table[var] = value.get()
                        else:
                            to_delete.add(var)
                for var in to_delete:
                    del eval_ast.sym_table[var]

        eval_ast.curr_func = None
        eval_result = await eval_ast.aeval(eval_ast.ast)
        #
        # save variables only in the locals scope
        #
        if eval_globals is None and eval_ast.sym_table != ast_ctx.sym_table:
            for var, value in eval_ast.sym_table.items():
                if var in ast_ctx.global_sym_table and value == ast_ctx.global_sym_table[var]:
                    continue
                if var not in ast_ctx.sym_table and (
                    ast_ctx.curr_func is None or var not in ast_ctx.curr_func.local_names
                ):
                    ast_ctx.user_locals[var] = value
        return eval_result

    return eval_func


def ast_eval_factory(ast_ctx):
    """Generate a function that executes eval() with given ast_ctx."""
    return ast_eval_exec_factory(ast_ctx, "eval")


def ast_exec_factory(ast_ctx):
    """Generate a function that executes exec() with given ast_ctx."""
    return ast_eval_exec_factory(ast_ctx, "exec")


def ast_globals_factory(ast_ctx):
    """Generate a globals() function with given ast_ctx."""

    async def globals_func():
        return ast_ctx.global_sym_table

    return globals_func


def ast_locals_factory(ast_ctx):
    """Generate a locals() function with given ast_ctx."""

    async def locals_func():
        if ast_ctx.sym_table == ast_ctx.global_sym_table:
            return ast_ctx.sym_table
        local_sym_table = ast_ctx.sym_table.copy()
        local_sym_table.update(ast_ctx.user_locals)
        to_delete = set()
        for var, value in local_sym_table.items():
            if isinstance(value, EvalLocalVar):
                if value.is_defined():
                    local_sym_table[var] = value.get()
                else:
                    to_delete.add(var)
        for var in to_delete:
            del local_sym_table[var]
        return local_sym_table

    return locals_func


#
# Built-in functions that are also passed the ast context
#
BUILTIN_AST_FUNCS_FACTORY = {
    "eval": ast_eval_factory,
    "exec": ast_exec_factory,
    "globals": ast_globals_factory,
    "locals": ast_locals_factory,
}


#
# Objects returned by return, break and continue statements that change execution flow,
# or objects returned that capture particular information
#
class EvalStopFlow:
    """Denotes a statement or action that stops execution flow, eg: return, break etc."""

    _name = None

    def name(self):
        """Return short name."""
        return self._name


class EvalReturn(EvalStopFlow):
    """Return statement."""

    _name = "return"

    def __init__(self, value):
        """Initialize return statement value."""
        self.value = value


class EvalBreak(EvalStopFlow):
    """Break statement."""

    _name = "break"


class EvalContinue(EvalStopFlow):
    """Continue statement."""

    _name = "continue"


class LoopScope:
    """The bindings a comprehension's loop variables replaced in a function's symbol table."""

    MISSING = object()

    def __init__(self, sym_table, var_names):
        """Save the current bindings of var_names in sym_table."""
        self.sym_table = sym_table
        self.var_names = var_names
        # the replaced bindings; a name that isn't here wasn't bound
        self.saved = {name: sym_table[name] for name in var_names if name in sym_table}

    def bindings(self):
        """Return the current bindings of our names."""
        return {name: self.sym_table.get(name, self.MISSING) for name in self.var_names}

    def set_bindings(self, bindings):
        """Bind each of our names to its entry in bindings, or remove it if it has none."""
        for name in self.var_names:
            entry = bindings.get(name, self.MISSING)
            if entry is self.MISSING:
                self.sym_table.pop(name, None)
            else:
                self.sym_table[name] = entry

    def restore(self):
        """Put back the saved bindings, returning the ones they replaced."""
        current = self.bindings()
        self.set_bindings(self.saved)
        return current

    def keep_writes(self):
        """Save the current bindings that changed since restore(), eg by an assignment."""
        for name, entry in self.bindings().items():
            if entry is not self.saved.get(name, self.MISSING):
                if entry is self.MISSING:
                    del self.saved[name]
                else:
                    self.saved[name] = entry


def lambda_param_names(lambda_ast):
    """Return the parameter names of a lambda."""
    args = lambda_ast.args
    params = [arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs]
    for arg in (args.vararg, args.kwarg):
        if arg is not None:
            params.append(arg.arg)
    return set(params)


def lambda_body_names(lambda_ast):
    """Return the names used anywhere in a lambda's body, a superset of the ones it reads from outside."""
    return {node.id for node in ast.walk(lambda_ast.body) if isinstance(node, ast.Name)}


def strip_qualname_prefix(code, prefix):
    """Return code, and the code nested in it (eg, an inner lambda), without prefix on their qualified names."""
    consts = tuple(
        strip_qualname_prefix(const, prefix) if isinstance(const, CodeType) else const
        for const in code.co_consts
    )
    return code.replace(co_qualname=code.co_qualname.removeprefix(prefix), co_consts=consts)


class EvalLocalVar:
    """Wrapper for local variable symtable entry."""

    def __init__(self, name, **kwargs):
        """Initialize value of local symbol."""
        self.name = name
        # the value is kept in a Python cell, so a lambda compiled to native code can share it
        self.cell = CellType(kwargs["value"]) if "value" in kwargs else CellType()

    def get(self):
        """Get value of local symbol."""
        try:
            return self.cell.cell_contents
        except ValueError:
            raise NameError(f"name '{self.name}' is not defined") from None

    def get_cell(self):
        """Get the Python cell that holds the value of local symbol."""
        return self.cell

    def get_name(self):
        """Get name of local symbol."""
        return self.name

    def set(self, value):
        """Set value of local symbol."""
        self.cell.cell_contents = value

    def is_defined(self):
        """Return whether value is defined."""
        try:
            _ = self.cell.cell_contents
        except ValueError:
            return False
        return True

    def set_undefined(self):
        """Set local symbol to undefined."""
        if self.is_defined():
            del self.cell.cell_contents

    def __getattr__(self, attr):
        """Get attribute of local variable."""
        if attr == "cell":
            # not set yet (eg, while copying an instance), so don't recurse
            raise AttributeError(attr)
        return getattr(self.get(), attr)

    def __repr__(self):
        """Generate string with address and value."""
        return f"EvalLocalVar @{hex(id(self))} = {self.get() if self.is_defined() else 'undefined'}"


def is_pyscript_func(func):
    """Return True if func is a pyscript (async) function."""
    return isinstance(func, (EvalFunc, EvalFuncVar))


def pyscript_property(obj, attr, hook):
    """
    Return the property for obj.attr if Python would use one, otherwise None.

    Python calls a property's getter, setter and deleter itself, which doesn't work for
    pyscript functions since they are async, so pyscript calls them instead. A subclass of
    property is left to Python, since it can override __get__, __set__ and __delete__. hook
    is the name of the attribute method (__getattribute__, __setattr__ or __delattr__) that
    Python would call first; if the class overrides it, the property isn't used here, so
    that the override still runs.
    """
    for klass in type(obj).__mro__:
        if attr in klass.__dict__:
            value = klass.__dict__[attr]
            if type(value) is not property:  # pylint: disable=unidiomatic-typecheck
                return None
            if getattr(type(obj), hook) not in (getattr(object, hook), getattr(type, hook)):
                return None
            return value
    return None


# a super object forwards attribute reads to the classes it searches, and a metaclass can
# intercept a class's, so these read the built-in members directly, as Python does
SUPER_SELF = super.__dict__["__self__"].__get__
SUPER_SELF_CLASS = super.__dict__["__self_class__"].__get__
SUPER_THISCLASS = super.__dict__["__thisclass__"].__get__
TYPE_MRO = type.__dict__["__mro__"].__get__
TYPE_DICT = type.__dict__["__dict__"].__get__


def super_property(sup, attr):
    """
    Return the property whose getter Python would call for sup.attr, otherwise None.

    Like Python, this searches the mro of sup.__self_class__ after sup.__thisclass__, and
    stops at the first class that has attr. Python's super doesn't use the class's own
    __getattribute__ or __getattr__, never searches for __class__, and returns the property
    itself rather than calling its getter when sup.__self__ is sup.__self_class__, as for
    super(C, cls) on a class.
    """
    self_class = SUPER_SELF_CLASS(sup)
    if self_class is None or SUPER_SELF(sup) is self_class or attr == "__class__":
        return None
    mro = TYPE_MRO(self_class)
    thisclass = SUPER_THISCLASS(sup)
    for start, klass in enumerate(mro):
        if klass is thisclass:
            break
    else:
        # __thisclass__ is no longer in the mro (e.g. __bases__ changed); Python raises its own error
        return None
    for klass in mro[start + 1 :]:
        klass_dict = TYPE_DICT(klass)
        if attr in klass_dict:
            value = klass_dict[attr]
            return value if type(value) is property else None  # pylint: disable=unidiomatic-typecheck
    return None


class EvalName:
    """Identifier that hasn't yet been resolved."""

    def __init__(self, name):
        """Initialize identifier to name."""
        self.name = name

    def __getattr__(self, attr):
        """Get attribute for EvalName."""
        raise NameError(f"name '{self.name}.{attr}' is not defined")


class EvalAttrSet:
    """Class for object and attribute on lhs of assignment."""

    def __init__(self, obj, attr):
        """Initialize identifier to name."""
        self.obj = obj
        self.attr = attr

    async def setattr_async(self, ast_ctx, value):
        """Set the attribute value, calling a pyscript property setter ourselves."""
        prop = pyscript_property(self.obj, self.attr, "__setattr__")
        if prop is not None and is_pyscript_func(prop.fset):
            await prop.fset.call(ast_ctx, self.obj, value)
        else:
            self.setattr(value)

    def setattr(self, value):
        """Set the attribute value."""
        setattr(self.obj, self.attr, value)

    def getattr(self):
        """Get the attribute value."""
        return getattr(self.obj, self.attr)

    async def delattr_async(self, ast_ctx):
        """Delete the attribute, calling a pyscript property deleter ourselves."""
        prop = pyscript_property(self.obj, self.attr, "__delattr__")
        if prop is not None and is_pyscript_func(prop.fdel):
            await prop.fdel.call(ast_ctx, self.obj)
        else:
            self.delattr()

    def delattr(self):
        """Delete the attribute."""
        delattr(self.obj, self.attr)


class EvalFunc:
    """Class for a callable pyscript function."""

    def __init__(
        self,
        func_def: ast.FunctionDef,
        code_list: list[str],
        code_str: str,
        global_ctx: "GlobalContext",
        async_func: bool = False,
    ) -> None:
        """Initialize a function calling context."""
        self.func_def = func_def
        self.name = func_def.name
        self.global_ctx = global_ctx
        self.global_ctx_name = global_ctx.get_name()
        self.logger = logging.getLogger(LOGGER_PATH + "." + self.global_ctx_name)
        self.defaults = []
        self.kw_defaults = []
        self.decorators = []
        self.dm_decorators = []
        self.global_names = set()
        self.nonlocal_names = set()
        self.local_names = None
        self.local_sym_table = {}
        self.doc_string = ast.get_docstring(func_def)
        self.num_posonly_arg = len(self.func_def.args.posonlyargs)
        self.num_posn_arg = self.num_posonly_arg + len(self.func_def.args.args) - len(self.defaults)
        # like Python's __class__ cell: holds the class whose body defined this method, for super()
        self.class_cell: list[type | None] | None = None
        self.code_list = code_list
        self.code_str = code_str
        self.trigger = []
        self.trigger_service = set()
        self.has_closure = False
        self.async_func = async_func

    def get_name(self):
        """Return the function name."""
        return self.name

    def set_name(self, name):
        """Set the function name."""
        self.name = name

    async def eval_defaults(self, ast_ctx):
        """Evaluate the default function arguments."""
        self.defaults = []
        for val in self.func_def.args.defaults:
            self.defaults.append(await ast_ctx.aeval(val))
        self.num_posn_arg = self.num_posonly_arg + len(self.func_def.args.args) - len(self.defaults)
        self.kw_defaults = []
        for val in self.func_def.args.kw_defaults:
            self.kw_defaults.append({"ok": bool(val), "val": None if not val else await ast_ctx.aeval(val)})

    async def trigger_init(self, trig_ctx, func_name):
        """Initialize decorator triggers for this function."""
        trig_args = {}
        trig_decs = {}
        trig_ctx_name = trig_ctx.get_name()
        self.logger = logging.getLogger(LOGGER_PATH + "." + trig_ctx_name)
        self.global_ctx.set_logger_name(trig_ctx_name)
        self.global_ctx_name = trig_ctx_name
        got_reqd_dec = False
        exc_mesg = f"function '{func_name}' defined in {trig_ctx_name}"
        trig_decorators_reqd = {
            "event_trigger",
            "mqtt_trigger",
            "state_trigger",
            "time_trigger",
            "webhook_trigger",
        }
        arg_check = {
            "event_trigger": {"arg_cnt": {1, 2, 3}, "rep_ok": True},
            "mqtt_trigger": {"arg_cnt": {1, 2, 3}, "rep_ok": True},
            "state_active": {"arg_cnt": {1}},
            "state_trigger": {"arg_cnt": {"*"}, "type": {list, set}, "rep_ok": True},
            "service": {"arg_cnt": {0, "*"}},
            "task_unique": {"arg_cnt": {1}},
            "time_active": {"arg_cnt": {"*"}},
            "time_trigger": {"arg_cnt": {0, "*"}, "rep_ok": True},
            "webhook_trigger": {"arg_cnt": {1, 2}, "rep_ok": True},
        }
        kwarg_check = {
            "event_trigger": {"kwargs": {dict}},
            "mqtt_trigger": {
                "kwargs": {dict},
                "encoding": {str},
            },
            "time_trigger": {"kwargs": {dict}},
            "task_unique": {"kill_me": {bool, int}},
            "time_active": {"hold_off": {int, float}},
            "service": {"supports_response": {str}},
            "state_trigger": {
                "kwargs": {dict},
                "state_hold": {int, float},
                "state_check_now": {bool, int},
                "state_hold_false": {int, float},
                "watch": {set, list},
            },
            "webhook_trigger": {
                "kwargs": {dict},
                "local_only": {bool},
                "methods": {list, set},
            },
        }

        for dec in self.decorators:
            dec_name, dec_args, dec_kwargs = dec[0], dec[1], dec[2]
            if dec_name not in TRIG_SERV_DECORATORS:
                raise SyntaxError(f"{exc_mesg}: unknown decorator @{dec_name}")
            if dec_name in trig_decorators_reqd:
                got_reqd_dec = True
            arg_info = arg_check.get(dec_name, {})
            #
            # check that we have the right number of arguments, and that they are
            # strings
            #
            arg_cnt = arg_info["arg_cnt"]
            if dec_args is None and 0 not in arg_cnt:
                raise TypeError(f"{exc_mesg}: decorator @{dec_name} needs at least one argument")
            if dec_args:
                if "*" not in arg_cnt and len(dec_args) not in arg_cnt:
                    raise TypeError(
                        f"{exc_mesg}: decorator @{dec_name} got {len(dec_args)}"
                        f" argument{'s' if len(dec_args) > 1 else ''}, expected"
                        f" {' or '.join([str(cnt) for cnt in sorted(arg_cnt)])}"
                    )
                for arg_num, arg in enumerate(dec_args):
                    if isinstance(arg, str):
                        continue
                    mesg = "string"
                    if "type" in arg_info:
                        if type(arg) in arg_info["type"]:
                            for val in arg:
                                if not isinstance(val, str):
                                    break
                            else:
                                continue
                            mesg += ", or " + ", or ".join(
                                sorted([ok_type.__name__ for ok_type in arg_info["type"]])
                            )
                    raise TypeError(
                        f"{exc_mesg}: decorator @{dec_name} argument {arg_num + 1} should be a {mesg}"
                    )
            if arg_cnt == {1}:
                dec_args = dec_args[0]

            if dec_name not in kwarg_check and dec_kwargs is not None:
                raise TypeError(f"{exc_mesg}: decorator @{dec_name} doesn't take keyword arguments")
            if dec_kwargs is None:
                dec_kwargs = {}
            if dec_name in kwarg_check:
                allowed = kwarg_check[dec_name]
                for arg, value in dec_kwargs.items():
                    if arg not in allowed:
                        raise TypeError(
                            f"{exc_mesg}: decorator @{dec_name} invalid keyword argument '{arg}'"
                        )
                    if value is None or type(value) in allowed[arg]:
                        continue
                    ok_types = " or ".join(sorted([t.__name__ for t in allowed[arg]]))
                    raise TypeError(
                        f"{exc_mesg}: decorator @{dec_name} keyword '{arg}' should be type {ok_types}"
                    )
            if dec_name == "service":
                desc = self.doc_string
                if desc is None or desc == "":
                    desc = f"pyscript function {func_name}()"
                desc = desc.lstrip(" \n\r")
                if desc.startswith("yaml"):
                    try:
                        desc = desc[4:].lstrip(" \n\r")
                        file_desc = io.StringIO(desc)
                        service_desc = yaml.safe_load(file_desc) or OrderedDict()
                        file_desc.close()
                    except Exception as exc:
                        self.logger.error(
                            "Unable to decode yaml doc_string for %s(): %s",
                            func_name,
                            str(exc),
                        )
                        raise exc
                else:
                    fields = OrderedDict()
                    for arg in self.get_positional_args():
                        fields[arg] = OrderedDict(description=f"argument {arg}")
                    service_desc = {"description": desc, "fields": fields}

                def pyscript_service_factory(func_name, func):
                    async def pyscript_service_handler(call):
                        """Handle python script service calls."""
                        # self.logger.debug("service call to %s", func_name)
                        #
                        # use a new AstEval context so it can run fully independently
                        # of other instances (except for global_ctx which is common)
                        #
                        ast_ctx = AstEval(f"{trig_ctx_name}.{func_name}", self.global_ctx)
                        Function.install_ast_funcs(ast_ctx)
                        func_args = {
                            "trigger_type": "service",
                            "context": call.context,
                        }
                        func_args.update(call.data)

                        async def do_service_call(func, ast_ctx, data):
                            try:
                                return await func.call(ast_ctx, **data)
                            except Exception as exc:
                                ast_ctx.log_exception(exc)
                            return None

                        task = Function.create_task(do_service_call(func, ast_ctx, func_args))
                        await task
                        return task.result()

                    return pyscript_service_handler

                for srv_name in dec_args if dec_args else [f"{DOMAIN}.{func_name}"]:
                    if type(srv_name) is not str or srv_name.count(".") != 1:
                        raise ValueError(f"{exc_mesg}: @service argument must be a string with one period")
                    domain, name = srv_name.split(".", 1)
                    if name in (SERVICE_RELOAD, SERVICE_JUPYTER_KERNEL_START):
                        raise SyntaxError(f"{exc_mesg}: @service conflicts with builtin service")
                    Function.service_register(
                        trig_ctx_name,
                        domain,
                        name,
                        pyscript_service_factory(func_name, self),
                        dec_kwargs.get("supports_response", SupportsResponse.NONE),
                    )
                    async_set_service_schema(Function.hass, domain, name, service_desc)
                    self.trigger_service.add(srv_name)
                continue

            if dec_name == "webhook_trigger" and "methods" in dec_kwargs:
                if len(bad := set(dec_kwargs["methods"]).difference(WEBHOOK_METHODS)) > 0:
                    raise TypeError(f"{exc_mesg}: {bad} aren't valid {dec_name} methods")

            if dec_name not in trig_decs:
                trig_decs[dec_name] = []
            if len(trig_decs[dec_name]) > 0 and "rep_ok" not in arg_info:
                raise SyntaxError(f"{exc_mesg}: decorator @{dec_name} can only be used once")
            trig_decs[dec_name].append({"args": dec_args, "kwargs": dec_kwargs})

        if not got_reqd_dec and len(trig_decs) > 0:
            self.logger.error(
                "%s defined in %s: needs at least one trigger decorator (ie: %s)",
                func_name,
                trig_ctx_name,
                ", ".join(sorted(trig_decorators_reqd)),
            )
            return

        if len(trig_decs) == 0:
            if len(self.trigger_service) > 0:
                trig_ctx.trigger_register(self)
            return

        #
        # start one or more triggers until they are all consumed
        # each trigger task can handle at most one of each type of
        # trigger; all get the same state_active, time_active and
        # task_unique decorators
        #
        while True:
            trig_args = {
                "action": self,
                "global_sym_table": self.global_ctx.global_sym_table,
            }
            got_trig = False
            for trig in trig_decorators_reqd:
                if trig not in trig_decs or len(trig_decs[trig]) == 0:
                    continue
                trig_args[trig] = trig_decs[trig].pop(0)
                got_trig = True
            if not got_trig:
                break
            for dec_name in ["state_active", "time_active", "task_unique"]:
                if dec_name in trig_decs:
                    trig_args[dec_name] = trig_decs[dec_name][0]

            self.trigger.append(trig_ctx.get_trig_info(f"{trig_ctx_name}.{func_name}", trig_args))

        if trig_ctx.trigger_register(self):
            self.trigger_start()

    def trigger_start(self):
        """Start any triggers for this function."""
        for trigger in self.trigger:
            trigger.start()

    def trigger_stop(self):
        """Stop any triggers for this function."""
        for trigger in self.trigger:
            trigger.stop()
        self.trigger = []
        for srv_name in self.trigger_service:
            domain, name = srv_name.split(".", 1)
            Function.service_remove(self.global_ctx_name, domain, name)
        self.trigger_service = set()

    async def eval_decorators(self, ast_ctx):
        """Evaluate the function decorators arguments."""
        code_str, code_list = ast_ctx.code_str, ast_ctx.code_list
        ast_ctx.code_str, ast_ctx.code_list = self.code_str, self.code_list

        dec_other = []
        dec_trig = []
        dec_dm = []
        for dec in self.func_def.decorator_list:
            if known_dec := await ast_ctx.global_ctx.get_decorator_by_expr(ast_ctx, dec):
                dec_dm.append(known_dec)
            elif (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id in TRIG_SERV_DECORATORS
            ):
                args = await ast_ctx.eval_elt_list(dec.args)
                kwargs = {keyw.arg: await ast_ctx.aeval(keyw.value) for keyw in dec.keywords}
                dec_trig.append([dec.func.id, args, kwargs if len(kwargs) > 0 else None])
            elif isinstance(dec, ast.Name) and dec.id in TRIG_SERV_DECORATORS:
                dec_trig.append([dec.id, None, None])
            else:
                dec_other.append(await ast_ctx.aeval(dec))

        ast_ctx.code_str, ast_ctx.code_list = code_str, code_list
        return dec_trig, reversed(dec_other), dec_dm

    async def resolve_nonlocals(self, ast_ctx):
        """Tag local variables and resolve nonlocals."""

        #
        # determine the list of local variables, nonlocal and global
        # arguments are local variables too
        #
        args = self.get_positional_args()
        if self.func_def.args.vararg:
            args.append(self.func_def.args.vararg.arg)
        if self.func_def.args.kwarg:
            args.append(self.func_def.args.kwarg.arg)
        for kwonlyarg in self.func_def.args.kwonlyargs:
            args.append(kwonlyarg.arg)
        nonlocal_names = set()
        global_names = set()
        var_names = set(args)
        self.local_names = set(args)
        for stmt in self.func_def.body:
            self.has_closure = self.has_closure or await self.check_for_closure(stmt)
            var_names = var_names.union(
                await ast_ctx.get_names(
                    stmt,
                    nonlocal_names=nonlocal_names,
                    global_names=global_names,
                    local_names=self.local_names,
                )
            )
        for var_name in var_names:
            got_dot = var_name.find(".")
            if got_dot >= 0:
                var_name = var_name[0:got_dot]

            if var_name in global_names:
                continue

            if var_name in self.local_names and var_name not in nonlocal_names:
                if self.has_closure:
                    self.local_sym_table[var_name] = EvalLocalVar(var_name)
                continue

            if var_name in nonlocal_names:
                sym_table_idx = 1
            else:
                sym_table_idx = 0
            for sym_table in reversed(ast_ctx.sym_table_stack[sym_table_idx:] + [ast_ctx.sym_table]):
                if var_name in sym_table and isinstance(sym_table[var_name], EvalLocalVar):
                    self.local_sym_table[var_name] = sym_table[var_name]
                    break
            else:
                if var_name in nonlocal_names:
                    val = await ast_ctx.ast_name(ast.Name(id=var_name, ctx=ast.Load()))
                    if isinstance(val, EvalName) and got_dot < 0:
                        raise SyntaxError(f"no binding for nonlocal '{var_name}' found")

    def get_decorators(self):
        """Return the function decorators."""
        return self.decorators

    def get_doc_string(self):
        """Return the function doc_string."""
        return self.doc_string

    def get_positional_args(self):
        """Return the function positional arguments."""
        args = []
        for arg in self.func_def.args.posonlyargs + self.func_def.args.args:
            args.append(arg.arg)
        return args

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the function with the given context and arguments."""
        sym_table = {}
        if args is None:
            args = []
        kwargs = kwargs.copy() if kwargs else {}
        bad_kwargs = []
        has_var_kwargs = self.func_def.args.kwarg is not None
        for i, func_def_arg in enumerate(self.func_def.args.posonlyargs + self.func_def.args.args):
            var_name = func_def_arg.arg
            val = None
            # as in Python, a keyword matching a positional-only parameter goes to **kwargs if present
            by_keyword = var_name in kwargs and not (i < self.num_posonly_arg and has_var_kwargs)
            if i < len(args):
                val = args[i]
                if by_keyword:
                    raise TypeError(f"{self.name}() got multiple values for argument '{var_name}'")
            elif by_keyword:
                if i < self.num_posonly_arg:
                    bad_kwargs.append(var_name)
                val = kwargs[var_name]
                del kwargs[var_name]
            elif self.num_posn_arg <= i < len(self.defaults) + self.num_posn_arg:
                val = self.defaults[i - self.num_posn_arg]
            else:
                raise TypeError(
                    f"{self.name}() missing {self.num_posn_arg - i} required positional arguments"
                )
            sym_table[var_name] = val
        if len(bad_kwargs) > 0:
            raise TypeError(
                f"{self.name}() got some positional-only arguments passed as keyword arguments: '{', '.join(bad_kwargs)}'"
            )

        for i, kwonlyarg in enumerate(self.func_def.args.kwonlyargs):
            var_name = kwonlyarg.arg
            if var_name in kwargs:
                val = kwargs[var_name]
                del kwargs[var_name]
            elif i < len(self.kw_defaults) and self.kw_defaults[i]["ok"]:
                val = self.kw_defaults[i]["val"]
            else:
                raise TypeError(f"{self.name}() missing required keyword-only arguments")
            sym_table[var_name] = val
        if self.func_def.args.kwarg:
            sym_table[self.func_def.args.kwarg.arg] = kwargs
        elif not set(kwargs.keys()).issubset(TRIGGER_KWARGS):
            # don't raise an exception for extra trigger keyword parameters;
            # it's difficult to apply this exception to just trigger functions
            # since they could have non-trigger decorators too
            unexpected = ", ".join(sorted(set(kwargs.keys()) - TRIGGER_KWARGS))
            raise TypeError(f"{self.name}() called with unexpected keyword arguments: {unexpected}")
        num_posn = self.num_posonly_arg + len(self.func_def.args.args)
        if self.func_def.args.vararg:
            if len(args) > num_posn:
                sym_table[self.func_def.args.vararg.arg] = tuple(args[num_posn:])
            else:
                sym_table[self.func_def.args.vararg.arg] = ()
        elif len(args) > num_posn:
            raise TypeError(f"{self.name}() called with too many positional arguments")
        for name, value in self.local_sym_table.items():
            if name in sym_table:
                sym_table[name] = EvalLocalVar(name, value=sym_table[name])
            elif value.is_defined():
                sym_table[name] = value
            else:
                sym_table[name] = EvalLocalVar(name)
        if ast_ctx.global_ctx != self.global_ctx:
            #
            # switch to the global symbol table in the global context
            # where the function was defined
            #
            prev_sym_table = [
                ast_ctx.global_sym_table,
                ast_ctx.sym_table,
                ast_ctx.sym_table_stack,
                ast_ctx.global_ctx,
            ]
            ast_ctx.global_sym_table = self.global_ctx.get_global_sym_table()
            ast_ctx.sym_table_stack = [ast_ctx.global_sym_table]
            ast_ctx.global_ctx = self.global_ctx
        else:
            ast_ctx.sym_table_stack.append(ast_ctx.sym_table)
            prev_sym_table = None
        ast_ctx.sym_table = sym_table
        code_str, code_list = ast_ctx.code_str, ast_ctx.code_list
        ast_ctx.code_str, ast_ctx.code_list = self.code_str, self.code_list
        prev_func = ast_ctx.curr_func
        prev_genexp_iter = ast_ctx.genexp_iter
        ast_ctx.genexp_iter = None
        save_user_locals = ast_ctx.user_locals
        ast_ctx.user_locals = {}
        ast_ctx.curr_func = self
        del args, kwargs
        try:
            for arg1 in self.func_def.body:
                val = await ast_ctx.aeval(arg1)
                if isinstance(val, EvalReturn):
                    return val.value
            # return None at end if there isn't a return
            return None
        finally:
            ast_ctx.curr_func = prev_func
            ast_ctx.genexp_iter = prev_genexp_iter
            ast_ctx.user_locals = save_user_locals
            ast_ctx.code_str, ast_ctx.code_list = code_str, code_list
            if prev_sym_table is not None:
                (
                    ast_ctx.global_sym_table,
                    ast_ctx.sym_table,
                    ast_ctx.sym_table_stack,
                    ast_ctx.global_ctx,
                ) = prev_sym_table
            else:
                ast_ctx.sym_table = ast_ctx.sym_table_stack.pop()

    async def check_for_closure(self, arg):
        """Recursively check ast tree arg and return True if there is an inner function, class or lambda."""
        if isinstance(arg, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            return True
        if isinstance(arg, ast.Lambda) and lambda_body_names(arg) - lambda_param_names(arg):
            # the lambda might read our variables, so they need to be shared with it
            return True
        for child in ast.iter_child_nodes(arg):
            if await self.check_for_closure(child):
                return True
        return False


class EvalFuncVar:
    """Class for a callable pyscript function."""

    # keep our own fields out of __dict__, so functools.wraps() can't copy them onto a wrapper
    __slots__ = ("__dict__", "__weakref__", "ast_ctx", "func")

    def __init__(self, func: EvalFunc) -> None:
        """Initialize instance with given EvalFunc function."""
        self.func = func
        self.ast_ctx: AstEval | None = None

    @property
    def __name__(self):
        """Return the function name, like a Python function."""
        return self.func.get_name()

    @__name__.setter
    def __name__(self, name):
        """Set the function name, eg by functools.wraps()."""
        self.func.set_name(name)

    @property
    def __doc__(self):
        """Return the function doc string, like a Python function."""
        return self.func.get_doc_string()

    @__doc__.setter
    def __doc__(self, doc_string):
        """Set the function doc string, eg by functools.wraps()."""
        self.func.doc_string = doc_string

    def get_func(self) -> EvalFunc:
        """Return the EvalFunc function."""
        return self.func

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the EvalFunc function."""
        return await self.func.call(ast_ctx, *args, **kwargs)

    def get_name(self):
        """Return the function name."""
        return self.func.get_name()

    def set_name(self, name):
        """Set the function name."""
        self.func.set_name(name)

    def set_ast_ctx(self, ast_ctx):
        """Set the ast context."""
        self.ast_ctx = ast_ctx

    def get_ast_ctx(self):
        """Return the ast context."""
        return self.ast_ctx

    def __get__(self, obj, objtype=None):
        """Support descriptor protocol so class attributes bind to instances."""
        if obj is None:
            return self
        # like a Python bound method, hold a strong reference to the instance, so a method
        # of a temporary instance (eg, Foo().method()) still has it when called; this
        # doesn't create a cycle, since a new bound method is made on each attribute access
        return EvalFuncVarClassInst(self.func, self.ast_ctx, obj)

    def __del__(self):
        """On deletion, stop any triggers for this function."""
        if self.func:
            self.func.trigger_stop()

    async def __call__(self, *args, **kwargs):
        """Call the EvalFunc function using our saved ast ctx."""
        return await self.func.call(self.ast_ctx, *args, **kwargs)


class EvalFuncVarClassInst(EvalFuncVar):
    """Class for a callable pyscript class instance function."""

    __slots__ = ("class_inst",)
    # our class doc string would otherwise hide the __doc__ property, which returns the method's
    __doc__ = EvalFuncVar.__doc__

    def __init__(self, func: EvalFunc, ast_ctx: "AstEval", class_inst: Any) -> None:
        """Initialize instance with given EvalFunc function."""
        super().__init__(func)
        self.ast_ctx = ast_ctx
        self.class_inst = class_inst

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the EvalFunc function."""
        return await self.func.call(ast_ctx, self.class_inst, *args, **kwargs)

    async def __call__(self, *args, **kwargs):
        """Call the function using our saved ast ctx and class instance."""
        return await self.func.call(self.ast_ctx, self.class_inst, *args, **kwargs)


class AstEval:
    """Python interpreter AST object evaluator."""

    def __init__(self, name: str, global_ctx: "GlobalContext", logger_name: str | None = None) -> None:
        """Initialize an interpreter execution context."""
        self.name = name
        self.str = None
        self.ast = None
        self.global_ctx = global_ctx
        self.global_sym_table: SymTable = global_ctx.get_global_sym_table() if global_ctx else {}
        self.sym_table_stack: list[SymTable] = []
        self.sym_table = self.global_sym_table
        self.local_sym_table: SymTable = {}
        self.user_locals: SymTable = {}
        self.curr_func: EvalFunc | None = None
        self.genexp_iter = None  # the outermost iterator of the generator expression being evaluated
        self.filename = name
        self.code_str: str | None = None
        self.code_list: list[str] | None = None
        self.logger_handlers = set()
        self.logger = None
        self.set_logger_name(logger_name if logger_name is not None else self.name)
        self.config_entry = Function.hass.data.get(DOMAIN, {}).get(CONFIG_ENTRY, {})
        self.dec_eval_depth = 0
        self.class_sym_table: SymTable | None = None
        self.class_cell: list[type | None] | None = None
        self.class_global_names: set[str] = set()
        self.loop_scopes: tuple[LoopScope, ...] = ()
        self.class_pending_annotations: list[tuple[str, ast.expr]] = []

    async def ast_not_implemented(self, arg, *args):
        """Raise NotImplementedError exception for unimplemented AST types."""
        name = "ast_" + arg.__class__.__name__.lower()
        raise NotImplementedError(f"{self.name}: not implemented ast " + name)

    async def aeval(self, arg, undefined_check=True):
        """Vector to specific function based on ast class type."""
        name = "ast_" + arg.__class__.__name__.lower()
        val = await getattr(self, name, self.ast_not_implemented)(arg)
        if undefined_check and isinstance(val, EvalName):
            raise NameError(f"name '{val.name}' is not defined")
        return val

    # Statements return NONE, EvalBreak, EvalContinue, EvalReturn
    async def ast_module(self, arg):
        """Execute ast_module - a list of statements."""
        val = None
        for arg1 in arg.body:
            val = await self.aeval(arg1)
            if isinstance(val, EvalReturn):
                raise SyntaxError(f"{val.name()} statement outside function")
            if isinstance(val, EvalStopFlow):
                raise SyntaxError(f"{val.name()} statement outside loop")
        return val

    async def ast_import(self, arg):
        """Execute import."""
        for imp in arg.names:
            mod = await self.global_ctx.module_import(imp.name, 0)
            if not mod:
                if (
                    not self.config_entry.data.get(CONF_ALLOW_ALL_IMPORTS, False)
                    and imp.name not in ALLOWED_IMPORTS
                ):
                    raise ModuleNotFoundError(f"import of {imp.name} not allowed")
                if imp.name not in sys.modules:
                    mod = await Function.hass.async_add_executor_job(importlib.import_module, imp.name)
                else:
                    mod = sys.modules[imp.name]
            self.assign_name(imp.name if imp.asname is None else imp.asname, mod)

    async def ast_importfrom(self, arg):
        """Execute from X import Y."""
        if arg.module is None:
            # handle: "from . import xyz"
            for imp in arg.names:
                mod = await self.global_ctx.module_import(imp.name, arg.level)
                if not mod:
                    raise ModuleNotFoundError(f"module '{imp.name}' not found")
                self.assign_name(imp.name if imp.asname is None else imp.asname, mod)
            return
        if arg.module == "stubs" or arg.module.startswith("stubs."):
            for imp in arg.names:
                if imp.asname is not None:
                    raise ModuleNotFoundError(
                        f"from {arg.module} import {imp.name} *as {imp.asname}* not supported for stubs"
                    )
            _LOGGER.debug("Skipping stubs import %s", arg.module)
            return
        mod = await self.global_ctx.module_import(arg.module, arg.level)
        if not mod:
            if (
                not self.config_entry.data.get(CONF_ALLOW_ALL_IMPORTS, False)
                and arg.module not in ALLOWED_IMPORTS
            ):
                raise ModuleNotFoundError(f"import from {arg.module} not allowed")
            if arg.module not in sys.modules:
                mod = await Function.hass.async_add_executor_job(importlib.import_module, arg.module)
            else:
                mod = sys.modules[arg.module]
        for imp in arg.names:
            if imp.name == "*":
                for name, value in mod.__dict__.items():
                    if name[0] != "_":
                        self.assign_name(name, value)
            else:
                if not hasattr(mod, imp.name):
                    raise ImportError(
                        f"cannot import name '{imp.name}' from '{mod.__name__}' "
                        f"({getattr(mod, '__file__', None) or 'unknown location'})"
                    )
                value = getattr(mod, imp.name)
                self.assign_name(imp.name if imp.asname is None else imp.asname, value)

    async def ast_if(self, arg):
        """Execute if statement."""
        val = None
        if await self.aeval(arg.test):
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val
        else:
            for arg1 in arg.orelse:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val
        return val

    async def ast_for(self, arg):
        """Execute for statement."""
        for loop_var in await self.aeval(arg.iter):
            await self.recurse_assign(arg.target, loop_var)
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    break
            if isinstance(val, EvalBreak):
                break
            if isinstance(val, EvalReturn):
                return val
        else:
            for arg1 in arg.orelse:
                val = await self.aeval(arg1)
                if isinstance(val, EvalReturn):
                    return val
        return None

    async def ast_asyncfor(self, arg):
        """Execute async for statement."""
        return await self.ast_for(arg)

    async def ast_while(self, arg):
        """Execute while statement."""
        while await self.aeval(arg.test):
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    break
            if isinstance(val, EvalBreak):
                break
            if isinstance(val, EvalReturn):
                return val
        else:
            for arg1 in arg.orelse:
                val = await self.aeval(arg1)
                if isinstance(val, EvalReturn):
                    return val
        return None

    async def ast_classdef(self, arg):
        """Evaluate class definition."""
        bases = [(await self.aeval(base)) for base in arg.bases]
        keywords = {kw.arg: await self.aeval(kw.value) for kw in arg.keywords}
        metaclass = keywords.pop("metaclass", type(bases[0]) if bases else type)

        if hasattr(metaclass, "__prepare__"):
            sym_table = metaclass.__prepare__(arg.name, tuple(bases), **keywords)
        else:
            sym_table = {}
        self.sym_table_stack.append(self.sym_table)
        self.sym_table = sym_table
        prev_class_sym_table, self.class_sym_table = self.class_sym_table, sym_table
        class_cell: list[type | None] = [None]
        prev_class_cell, self.class_cell = self.class_cell, class_cell
        prev_class_global_names, self.class_global_names = self.class_global_names, set()
        prev_pending, self.class_pending_annotations = self.class_pending_annotations, []
        try:
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalReturn):
                    raise SyntaxError(f"{val.name()} statement outside function")
                if isinstance(val, EvalStopFlow):
                    raise SyntaxError(f"{val.name()} statement outside loop")
        finally:
            # restore the enclosing scope even if the class body raises and the exception is caught
            self.class_sym_table = prev_class_sym_table
            self.class_cell = prev_class_cell
            self.class_global_names = prev_class_global_names
            pending, self.class_pending_annotations = self.class_pending_annotations, prev_pending
            self.sym_table = self.sym_table_stack.pop()

        decorators = [await self.aeval(dec) for dec in arg.decorator_list]
        init_func = sym_table["__init__"] if "__init__" in sym_table else None
        if isinstance(init_func, staticmethod):
            init_func = init_func.__func__
        if isinstance(init_func, (EvalFunc, EvalFuncVar)):
            #
            # Python can't call our async __init__, so rename it; call_func() calls
            # it when it creates an instance (a compiled __init__ is left to Python)
            #
            sym_table["__init__evalfunc_wrap__"] = sym_table["__init__"]
            del sym_table["__init__"]
        # create the class like any other instance, so a pyscript metaclass __new__ or __init__ is called
        cls = await self.call_func(metaclass, None, arg.name, tuple(bases), sym_table, **keywords)
        if inspect.iscoroutine(cls):
            cls = await cls
        class_cell[0] = cls
        if pending:
            await self.eval_pending_annotations(cls, arg.name, pending)
        for dec_func in reversed(decorators):
            cls = await self.call_func(dec_func, None, cls)
        # as in Python, the name is bound only once the class has been created
        self.assign_name(arg.name, cls)

    async def eval_pending_annotations(self, cls, cls_name, pending):
        """Evaluate class annotations that named the class itself, now that it exists."""
        annotations = cls.__dict__.get("__annotations__")
        if not isinstance(annotations, dict):
            return
        self.sym_table_stack.append(self.sym_table)
        # as in Python, an annotation sees the class's own names first, eg a type alias
        self.sym_table = {cls_name: cls, **cls.__dict__}
        try:
            for name, annotation in pending:
                try:
                    annotations[name] = await self.aeval(annotation)
                except NameError:
                    pass
        finally:
            self.sym_table = self.sym_table_stack.pop()

    async def ast_functiondef(self, arg, async_func=False):
        """Evaluate function definition."""
        other_dec = []
        dec_name = None
        pyscript_compile = None
        for dec in arg.decorator_list:
            if isinstance(dec, ast.Name) and dec.id in COMP_DECORATORS:
                dec_name = dec.id
            elif (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id in COMP_DECORATORS
            ):
                dec_name = dec.func.id
            else:
                other_dec.append(dec)
                continue
            if pyscript_compile:
                raise SyntaxError(
                    f"can only specify single decorator of {', '.join(sorted(COMP_DECORATORS))}"
                )
            pyscript_compile = dec

        if pyscript_compile:
            if isinstance(pyscript_compile, ast.Call):
                if len(pyscript_compile.args) > 0:
                    raise TypeError(f"@{dec_name}() takes 0 positional arguments")
                if len(pyscript_compile.keywords) > 0:
                    raise TypeError(f"@{dec_name}() takes no keyword arguments")
            arg.decorator_list = other_dec
            local_var = None
            if arg.name in self.sym_table and isinstance(self.sym_table[arg.name], EvalLocalVar):
                local_var = self.sym_table[arg.name]
            code = compile(ast.Module(body=[arg], type_ignores=[]), filename=self.filename, mode="exec")
            exec(code, self.global_sym_table, self.sym_table)  # pylint: disable=exec-used

            func = self.sym_table[arg.name]
            if dec_name == "pyscript_executor":
                if not inspect.iscoroutinefunction(func):

                    def executor_wrap_factory(func):
                        async def executor_wrap(*args, **kwargs):
                            return await Function.hass.async_add_executor_job(
                                functools.partial(func, **kwargs), *args
                            )

                        return executor_wrap

                    self.sym_table[arg.name] = executor_wrap_factory(func)
                else:
                    raise TypeError("@pyscript_executor() needs a regular, not async, function")
            if local_var:
                self.sym_table[arg.name] = local_var
                self.sym_table[arg.name].set(func)
            return

        func = EvalFunc(arg, self.code_list, self.code_str, self.global_ctx, async_func)
        if self.sym_table is self.class_sym_table:
            func.class_cell = self.class_cell
        elif self.curr_func:
            # as in Python, a function inside a method shares that method's __class__
            func.class_cell = self.curr_func.class_cell
        await func.eval_defaults(self)
        await func.resolve_nonlocals(self)
        name = func.get_name()
        dec_trig, dec_other, dec_dm = await func.eval_decorators(self)
        self.dec_eval_depth += 1
        for dec_func in dec_other:
            if isinstance(func, EvalFunc):
                # pass decorators an EvalFuncVar, like a Python function object; functools.wraps()
                # then only updates its __dict__, not the fields of the wrapper's EvalFunc
                func = EvalFuncVar(func)
                func.set_ast_ctx(self)
            func_arg = func
            func = await self.call_func(dec_func, None, func_arg)
            if isinstance(func, EvalFuncVar):
                # set the function name back to its original instead of the decorator function we just called
                func.set_name(name)
                if func is not func_arg:
                    dec_trig += func.get_func().decorators
                    dec_dm += func.get_func().dm_decorators
            elif isinstance(func, EvalFunc):
                func.set_name(name)
        self.dec_eval_depth -= 1
        if isinstance(func, EvalFunc):
            func = EvalFuncVar(func)
        if isinstance(func, EvalFuncVar):
            # keep the EvalFuncVar a decorator returned, since the decorator may hold
            # references to it and may have set attributes like __wrapped__ on it
            func_var = func
            func_var.set_ast_ctx(self)
            func = func_var.get_func()
            func.decorators = dec_trig
            func.dm_decorators = dec_dm
            if self.dec_eval_depth == 0:
                func.trigger_stop()
                try:
                    await func.trigger_init(self.global_ctx, name)
                except Exception as e:
                    self.log_exception(e)

                if len(dec_dm) > 0:
                    await self.get_global_ctx().create_decorator_manager(dec_dm, self, func_var)
        else:
            func_var = func

        if self.binds_global(name):
            sym_table = self.global_sym_table
        else:
            sym_table = self.sym_table
        if name in sym_table and isinstance(sym_table[name], EvalLocalVar):
            sym_table[name].set(func_var)
        else:
            sym_table[name] = func_var

    async def ast_lambda(self, arg):
        """Evaluate lambda definition by compiling it to a native Python function."""
        lambda_args = arg.args
        # as in Python, the defaults are evaluated now, in this scope
        defaults = [await self.aeval(default) for default in lambda_args.defaults]
        kw_defaults = {}
        for kwonlyarg, default in zip(lambda_args.kwonlyargs, lambda_args.kw_defaults, strict=True):
            if default is not None:
                kw_defaults[kwonlyarg.arg] = await self.aeval(default)
        #
        # In a function, the lambda can read the function's variables (and the ones it shares
        # with enclosing functions). Compile the lambda inside a native function that has those
        # names as its parameters, so Python's compiler works out which of them the lambda reads,
        # exactly as Python would; they become the lambda's free variables, which we bind to the
        # cells of our EvalLocalVar variables, so the lambda reads their current values.
        #
        enclosing_names = []
        if self.in_local_scope():
            enclosing_names = sorted(
                name
                for name in lambda_body_names(arg) - lambda_param_names(arg)
                if isinstance(self.sym_table.get(name), EvalLocalVar)
            )
        lambda_code = self.cached_lambda_code(arg, enclosing_names)
        closure = tuple(self.sym_table[name].get_cell() for name in lambda_code.co_freevars)
        # as in Python, a lambda without defaults has __defaults__ None
        func = FunctionType(lambda_code, self.global_sym_table, None, tuple(defaults) or None, closure)
        if kw_defaults:
            func.__kwdefaults__ = kw_defaults
        return func

    def lambda_free_names(self, arg):
        """Return the names a lambda reads from the scope it's in, exactly as Python works them out."""
        candidates = sorted(lambda_body_names(arg))
        try:
            return set(self.cached_lambda_code(arg, candidates).co_freevars)
        except SyntaxError:
            # its body isn't native Python, so it fails when it's evaluated; until then, assume any name
            return set(candidates) - lambda_param_names(arg)

    def cached_lambda_code(self, arg, enclosing_names):
        """Return the compiled code of a lambda that can read enclosing_names, compiling it once."""
        # the compiled code depends only on the lambda and the names it might read from here
        cache_key = (tuple(enclosing_names), self.filename)
        code_cache = arg.__dict__.setdefault("pyscript_lambda_code", {})
        lambda_code = code_cache.get(cache_key)
        if lambda_code is None:
            lambda_code = code_cache[cache_key] = self.compile_lambda(arg, enclosing_names)
        return lambda_code

    def compile_lambda(self, arg, enclosing_names):
        """Compile a lambda to native code, inside a function whose parameters are enclosing_names."""
        lambda_args = arg.args
        native_lambda = ast.Lambda(
            args=ast.arguments(
                posonlyargs=lambda_args.posonlyargs,
                args=lambda_args.args,
                vararg=lambda_args.vararg,
                kwonlyargs=lambda_args.kwonlyargs,
                kw_defaults=[None] * len(lambda_args.kwonlyargs),
                kwarg=lambda_args.kwarg,
                defaults=[],
            ),
            body=arg.body,
        )
        scope_func = ast.FunctionDef(
            name="__lambda_scope__",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg=name) for name in enclosing_names],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=[ast.Return(value=native_lambda)],
            decorator_list=[],
        )
        for node in (native_lambda, native_lambda.args, scope_func, scope_func.args, *scope_func.args.args):
            ast.copy_location(node, arg)
        ast.fix_missing_locations(scope_func)
        module_code = compile(
            ast.Module(body=[scope_func], type_ignores=[]), filename=self.filename, mode="exec"
        )
        scope_code = next(const for const in module_code.co_consts if isinstance(const, CodeType))
        lambda_code = next(const for const in scope_code.co_consts if isinstance(const, CodeType))
        lambda_code = strip_qualname_prefix(lambda_code, f"{scope_func.name}.<locals>.")
        return lambda_code

    async def ast_asyncfunctiondef(self, arg):
        """Evaluate async function definition."""
        return await self.ast_functiondef(arg, async_func=True)

    async def ast_try(self, arg):
        """Execute try...except statement."""
        try:
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val
        except Exception as err:
            for handler in arg.handlers:
                match = False
                if handler.type:
                    exc_list = await self.aeval(handler.type)
                    if not isinstance(exc_list, tuple):
                        exc_list = [exc_list]
                    for exc in exc_list:
                        if isinstance(err, exc):
                            match = True
                            break
                else:
                    match = True
                if match:
                    if handler.name is not None:
                        if handler.name in self.sym_table and isinstance(
                            self.sym_table[handler.name], EvalLocalVar
                        ):
                            self.sym_table[handler.name].set(err)
                        else:
                            self.sym_table[handler.name] = err
                    try:
                        for arg1 in handler.body:
                            val = await self.aeval(arg1)
                            if isinstance(val, EvalStopFlow):
                                return val
                    finally:
                        if handler.name is not None:
                            if isinstance(self.sym_table.get(handler.name), EvalLocalVar):
                                # like del, so a lambda or inner function sharing it sees it's unbound
                                self.sym_table[handler.name].set_undefined()
                            else:
                                self.sym_table.pop(handler.name, None)
                    break
            else:
                raise err
        else:
            for arg1 in arg.orelse:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val
        finally:
            for arg1 in arg.finalbody:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val  # pylint: disable=lost-exception,return-in-finally
        return None

    async def ast_raise(self, arg):
        """Execute raise statement."""
        if arg.exc:
            exc = await self.aeval(arg.exc)
            if inspect.isclass(exc) and issubclass(exc, BaseException):
                # create the instance here, so a pyscript __init__ is called
                exc = await self.call_func(exc, None)
            if arg.cause:
                cause = await self.aeval(arg.cause)
                raise exc from cause
            raise exc
        raise  # pylint: disable=misplaced-bare-raise

    async def ast_with(self, arg, async_attr=""):
        """Execute with statement; several items behave like nested with statements."""
        return await self.with_items(arg, arg.items, async_attr)

    async def with_items(self, arg, items, async_attr):
        """Enter the first manager, run the remaining items and the body inside it, then exit it."""
        if not items:
            val = None
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    break
            return val
        item = items[0]
        manager = await self.aeval(item.context_expr)
        enter_attr = f"__{async_attr}enter__"
        exit_attr = f"__{async_attr}exit__"
        enter_func = getattr(type(manager), enter_attr)
        exit_func = getattr(type(manager), exit_attr)
        value = await self.call_func(enter_func, enter_attr, manager)
        try:
            if item.optional_vars:
                await self.recurse_assign(item.optional_vars, value)
            val = await self.with_items(arg, items[1:], async_attr)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            # unlike Python, the exit can't suppress these, so tasks can always be stopped
            await self.call_func(exit_func, exit_attr, manager, *sys.exc_info())
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            if not await self.call_func(exit_func, exit_attr, manager, *sys.exc_info()):
                raise
            return None
        await self.call_func(exit_func, exit_attr, manager, None, None, None)
        return val

    async def ast_asyncwith(self, arg):
        """Execute async with statement."""
        return await self.ast_with(arg, async_attr="a")

    async def ast_pass(self, arg):
        """Execute pass statement."""

    async def ast_expression(self, arg):
        """Execute expression statement."""
        return await self.aeval(arg.body)

    async def ast_expr(self, arg):
        """Execute expression statement."""
        return await self.aeval(arg.value)

    async def ast_break(self, arg):
        """Execute break statement - return special class."""
        return EvalBreak()

    async def ast_continue(self, arg):
        """Execute continue statement - return special class."""
        return EvalContinue()

    async def ast_return(self, arg):
        """Execute return statement - return special class."""
        return EvalReturn(await self.aeval(arg.value) if arg.value else None)

    async def ast_global(self, arg):
        """Execute global statement."""
        if self.sym_table is self.class_sym_table:
            # a class body's global declarations apply to the class body only
            self.class_global_names.update(arg.names)
            return
        if not self.curr_func:
            raise SyntaxError("global statement outside function")
        for var_name in arg.names:
            self.curr_func.global_names.add(var_name)

    async def ast_nonlocal(self, arg):
        """Execute nonlocal statement."""
        if not self.curr_func:
            raise SyntaxError("nonlocal statement outside function")
        for var_name in arg.names:
            self.curr_func.nonlocal_names.add(var_name)

    async def recurse_assign(self, lhs, val):
        """Recursive assignment."""
        if isinstance(lhs, ast.Tuple):
            try:
                vals = [*(iter(val))]
            except Exception:
                raise TypeError("cannot unpack non-iterable object")  # pylint: disable=raise-missing-from
            got_star = 0
            for lhs_elt in lhs.elts:
                if isinstance(lhs_elt, ast.Starred):
                    got_star = 1
                    break
            if len(lhs.elts) > len(vals) + got_star:
                if got_star:
                    err_msg = f"at least {len(lhs.elts) - got_star}"
                else:
                    err_msg = f"{len(lhs.elts)}"
                raise ValueError(f"too few values to unpack (expected {err_msg})")
            if len(lhs.elts) < len(vals) and got_star == 0:
                raise ValueError(f"too many values to unpack (expected {len(lhs.elts)})")
            val_idx = 0
            for lhs_elt in lhs.elts:
                if isinstance(lhs_elt, ast.Starred):
                    star_len = len(vals) - len(lhs.elts) + 1
                    star_name = lhs_elt.value.id
                    await self.recurse_assign(
                        ast.Name(id=star_name, ctx=ast.Store()),
                        vals[val_idx : val_idx + star_len],
                    )
                    val_idx += star_len
                else:
                    await self.recurse_assign(lhs_elt, vals[val_idx])
                    val_idx += 1
        elif isinstance(lhs, ast.Subscript):
            var = await self.aeval(lhs.value)
            var[await self.aeval(lhs.slice)] = val
        else:
            var_name = await self.aeval(lhs)
            if isinstance(var_name, EvalAttrSet):
                await var_name.setattr_async(self, val)
                return
            if not isinstance(var_name, str):
                raise NotImplementedError(f"unknown lhs type {lhs} (got {var_name}) in assign")
            dot_count = var_name.count(".")
            if dot_count == 1:
                State.set(var_name, val)
                return
            if dot_count == 2:
                State.setattr(var_name, val)
                return
            if dot_count > 0:
                raise NameError(
                    f"invalid name '{var_name}' (should be 'domain.entity' or 'domain.entity.attr')"
                )
            self.assign_name(var_name, val)

    def in_local_scope(self):
        """Return whether names are bound in a function's local scope, not at module level or in a class body."""
        return self.sym_table is not self.global_sym_table and self.sym_table is not self.class_sym_table

    def binds_global(self, var_name):
        """Return whether var_name is bound in the global symbol table by a global declaration."""
        if self.sym_table is self.class_sym_table:
            # a class body binds in its own namespace, whatever an enclosing function declares
            return var_name in self.class_global_names
        return bool(self.curr_func and var_name in self.curr_func.global_names)

    def assign_name(self, var_name, val):
        """Bind a plain variable name to val, updating an existing EvalLocalVar in place."""
        if self.binds_global(var_name):
            self.global_sym_table[var_name] = val
            return
        if self.sym_table is self.class_sym_table:
            # a class body binds plainly in the class namespace (see binds_global)
            self.sym_table[var_name] = val
            return
        if var_name in self.sym_table and isinstance(self.sym_table[var_name], EvalLocalVar):
            self.sym_table[var_name].set(val)
        else:
            self.sym_table[var_name] = val

    async def ast_assign(self, arg):
        """Execute assignment statement."""
        rhs = await self.aeval(arg.value)
        for target in arg.targets:
            await self.recurse_assign(target, rhs)

    async def ast_augassign(self, arg):
        """Execute augmented assignment statement (lhs <BinOp>= value)."""
        arg.target.ctx = ast.Load()
        new_val = await self.aeval(ast.BinOp(left=arg.target, op=arg.op, right=arg.value))
        arg.target.ctx = ast.Store()
        await self.recurse_assign(arg.target, new_val)

    async def ast_annassign(self, arg):
        """Execute type hint assignment statement and track __annotations__."""
        if isinstance(arg.target, ast.Name):
            annotations = self.sym_table.setdefault("__annotations__", {})
            if arg.annotation:
                try:
                    annotations[arg.target.id] = await self.aeval(arg.annotation)
                except NameError:
                    # it can name something not defined yet (eg, the class being defined), so
                    # keep its source, as with "from __future__ import annotations"
                    annotations[arg.target.id] = ast.unparse(arg.annotation)
                    if self.sym_table is self.class_sym_table:
                        self.class_pending_annotations.append((arg.target.id, arg.annotation))
        if arg.value is not None:
            rhs = await self.aeval(arg.value)
            await self.recurse_assign(arg.target, rhs)

    async def ast_namedexpr(self, arg):
        """Execute named expression."""
        val = await self.aeval(arg.value)
        await self.recurse_assign(arg.target, val)
        return val

    async def ast_delete(self, arg):
        """Execute del statement."""
        for arg1 in arg.targets:
            if isinstance(arg1, ast.Subscript):
                var = await self.aeval(arg1.value)
                del var[await self.aeval(arg1.slice)]
            elif isinstance(arg1, ast.Name):
                if self.binds_global(arg1.id):
                    if arg1.id not in self.global_sym_table:
                        raise NameError(f"name '{arg1.id}' is not defined")
                    del self.global_sym_table[arg1.id]
                elif arg1.id in self.sym_table:
                    if isinstance(self.sym_table[arg1.id], EvalLocalVar):
                        if self.sym_table[arg1.id].is_defined():
                            self.sym_table[arg1.id].set_undefined()
                        else:
                            raise NameError(f"name '{arg1.id}' is not defined")
                    else:
                        del self.sym_table[arg1.id]
                else:
                    raise NameError(f"name '{arg1.id}' is not defined")
            elif isinstance(arg1, ast.Attribute):
                # as with assignment, a dotted name is a state only if its first part isn't defined
                var_name = await self.ast_attribute_collapse(arg1)
                if var_name is not None:
                    State.delete(var_name)
                else:
                    await EvalAttrSet(await self.aeval(arg1.value), arg1.attr).delattr_async(self)
            else:
                raise NotImplementedError(f"unknown target type {arg1} in del")

    async def ast_assert(self, arg):
        """Execute assert statement."""
        if not await self.aeval(arg.test):
            if arg.msg:
                raise AssertionError(await self.aeval(arg.msg))
            raise AssertionError

    async def ast_attribute_collapse(self, arg, check_undef=True):
        """Combine dotted attributes to allow variable names to have dots."""
        # collapse dotted names, eg:
        #   Attribute(value=Attribute(value=Name(id='i', ctx=Load()), attr='j', ctx=Load()), attr='k', ctx=Store())
        name = arg.attr
        val = arg.value
        while isinstance(val, ast.Attribute):
            name = val.attr + "." + name
            val = val.value
        if isinstance(val, ast.Name):
            name = val.id + "." + name
            # ensure the first portion of name is undefined
            if check_undef and not isinstance(
                await self.ast_name(ast.Name(id=val.id, ctx=ast.Load())), EvalName
            ):
                return None
            return name
        return None

    async def ast_attribute(self, arg):
        """Apply attributes."""
        full_name = await self.ast_attribute_collapse(arg)
        if full_name is not None:
            if isinstance(arg.ctx, ast.Store):
                return full_name
            val = await self.ast_name(ast.Name(id=full_name, ctx=arg.ctx))
            if not isinstance(val, EvalName):
                return val
        val = await self.aeval(arg.value)
        if isinstance(arg.ctx, ast.Store):
            return EvalAttrSet(val, arg.attr)
        if arg.attr == "__init__" and (init_func := self.init_attr(val)) is not None:
            return init_func
        if type(val) is super:  # pylint: disable=unidiomatic-typecheck
            prop = super_property(val, arg.attr)
            # check the getter's real type, since isinstance() would read a native getter's __class__
            if prop is not None and issubclass(type(prop.fget), (EvalFunc, EvalFuncVar)):
                # as in Python, an AttributeError from the getter isn't passed to __getattr__
                return await prop.fget.call(self, SUPER_SELF(val))
        prop = pyscript_property(val, arg.attr, "__getattribute__")
        if prop is not None and is_pyscript_func(prop.fget):
            try:
                return await prop.fget.call(self, val)
            except AttributeError:
                # as in Python, an AttributeError from the getter falls back to __getattr__
                getattr_func = inspect.getattr_static(type(val), "__getattr__", None)
                if getattr_func is None:
                    raise
                if hasattr(getattr_func, "__get__"):
                    getattr_func = getattr_func.__get__(val, type(val))  # pylint: disable=unnecessary-dunder-call
                return await self.call_func(getattr_func, "__getattr__", arg.attr)
        return getattr(val, arg.attr)

    async def ast_name(self, arg):
        """Look up value of identifier on load, or returns name on set."""
        if isinstance(arg.ctx, ast.Load):
            #
            # check other scopes if required by global declarations
            #
            in_class = self.sym_table is self.class_sym_table
            if (in_class and arg.id in self.class_global_names) or (
                self.curr_func and arg.id in self.curr_func.global_names
            ):
                if in_class and arg.id not in self.class_global_names and arg.id in self.sym_table:
                    # a class body finds its own names first, unless it declares them global
                    val = self.sym_table[arg.id]
                    return val.get() if isinstance(val, EvalLocalVar) else val
                if arg.id in self.global_sym_table:
                    return self.global_sym_table[arg.id]
                raise NameError(f"global name '{arg.id}' is not defined")
            #
            # now check in our current symbol table, and then some other places
            #
            if arg.id in self.sym_table:
                if isinstance(self.sym_table[arg.id], EvalLocalVar):
                    if (
                        self.curr_func
                        and arg.id in self.curr_func.local_names
                        and not self.sym_table[arg.id].is_defined()
                    ):
                        raise UnboundLocalError(f"local variable '{arg.id}' referenced before assignment")
                    return self.sym_table[arg.id].get()
                return self.sym_table[arg.id]
            if arg.id in self.local_sym_table:
                return self.local_sym_table[arg.id]
            if arg.id in self.global_sym_table:
                if self.curr_func and arg.id in self.curr_func.local_names:
                    raise UnboundLocalError(f"local variable '{arg.id}' referenced before assignment")
                return self.global_sym_table[arg.id]
            if arg.id in BUILTIN_AST_FUNCS_FACTORY:
                return BUILTIN_AST_FUNCS_FACTORY[arg.id](self)
            if hasattr(builtins, arg.id) and arg.id not in BUILTIN_EXCLUDE and arg.id[0] != "_":
                return getattr(builtins, arg.id)
            if Function.get(arg.id):
                return Function.get(arg.id)
            num_dots = arg.id.count(".")
            #
            # any single-dot name could be a state variable
            # a two-dot name for state.attr needs to exist
            #
            if num_dots == 1 or (num_dots == 2 and State.exist(arg.id)):
                return State.get(arg.id)
            #
            # Couldn't find it, so return just the name wrapped in EvalName to
            # distinguish from a string variable value.  This is to support
            # names with ".", which are joined by ast_attribute
            #
            return EvalName(arg.id)
        return arg.id

    async def ast_binop(self, arg):
        """Evaluate binary operators by calling function based on class."""
        name = "ast_binop_" + arg.op.__class__.__name__.lower()
        return await getattr(self, name, self.ast_not_implemented)(arg.left, arg.right)

    async def ast_binop_add(self, arg0, arg1):
        """Evaluate binary operator: +."""
        return (await self.aeval(arg0)) + (await self.aeval(arg1))

    async def ast_binop_sub(self, arg0, arg1):
        """Evaluate binary operator: -."""
        return (await self.aeval(arg0)) - (await self.aeval(arg1))

    async def ast_binop_mult(self, arg0, arg1):
        """Evaluate binary operator: *."""
        return (await self.aeval(arg0)) * (await self.aeval(arg1))

    async def ast_binop_div(self, arg0, arg1):
        """Evaluate binary operator: /."""
        return (await self.aeval(arg0)) / (await self.aeval(arg1))

    async def ast_binop_mod(self, arg0, arg1):
        """Evaluate binary operator: %."""
        return (await self.aeval(arg0)) % (await self.aeval(arg1))

    async def ast_binop_pow(self, arg0, arg1):
        """Evaluate binary operator: **."""
        return (await self.aeval(arg0)) ** (await self.aeval(arg1))

    async def ast_binop_lshift(self, arg0, arg1):
        """Evaluate binary operator: <<."""
        return (await self.aeval(arg0)) << (await self.aeval(arg1))

    async def ast_binop_rshift(self, arg0, arg1):
        """Evaluate binary operator: >>."""
        return (await self.aeval(arg0)) >> (await self.aeval(arg1))

    async def ast_binop_bitor(self, arg0, arg1):
        """Evaluate binary operator: |."""
        return (await self.aeval(arg0)) | (await self.aeval(arg1))

    async def ast_binop_bitxor(self, arg0, arg1):
        """Evaluate binary operator: ^."""
        return (await self.aeval(arg0)) ^ (await self.aeval(arg1))

    async def ast_binop_bitand(self, arg0, arg1):
        """Evaluate binary operator: &."""
        return (await self.aeval(arg0)) & (await self.aeval(arg1))

    async def ast_binop_floordiv(self, arg0, arg1):
        """Evaluate binary operator: //."""
        return (await self.aeval(arg0)) // (await self.aeval(arg1))

    async def ast_unaryop(self, arg):
        """Evaluate unary operators by calling function based on class."""
        name = "ast_unaryop_" + arg.op.__class__.__name__.lower()
        return await getattr(self, name, self.ast_not_implemented)(arg.operand)

    async def ast_unaryop_not(self, arg0):
        """Evaluate unary operator: not."""
        return not (await self.aeval(arg0))

    async def ast_unaryop_invert(self, arg0):
        """Evaluate unary operator: ~."""
        return ~(await self.aeval(arg0))

    async def ast_unaryop_uadd(self, arg0):
        """Evaluate unary operator: +."""
        return await self.aeval(arg0)

    async def ast_unaryop_usub(self, arg0):
        """Evaluate unary operator: -."""
        return -(await self.aeval(arg0))

    async def ast_compare(self, arg):
        """Evaluate comparison operators by calling function based on class."""
        left = arg.left
        for cmp_op, right in zip(arg.ops, arg.comparators):
            name = "ast_cmpop_" + cmp_op.__class__.__name__.lower()
            val = await getattr(self, name, self.ast_not_implemented)(left, right)
            if not val:
                return False
            left = right
        return True

    async def ast_cmpop_eq(self, arg0, arg1):
        """Evaluate comparison operator: ==."""
        return (await self.aeval(arg0)) == (await self.aeval(arg1))

    async def ast_cmpop_noteq(self, arg0, arg1):
        """Evaluate comparison operator: !=."""
        return (await self.aeval(arg0)) != (await self.aeval(arg1))

    async def ast_cmpop_lt(self, arg0, arg1):
        """Evaluate comparison operator: <."""
        return (await self.aeval(arg0)) < (await self.aeval(arg1))

    async def ast_cmpop_lte(self, arg0, arg1):
        """Evaluate comparison operator: <=."""
        return (await self.aeval(arg0)) <= (await self.aeval(arg1))

    async def ast_cmpop_gt(self, arg0, arg1):
        """Evaluate comparison operator: >."""
        return (await self.aeval(arg0)) > (await self.aeval(arg1))

    async def ast_cmpop_gte(self, arg0, arg1):
        """Evaluate comparison operator: >=."""
        return (await self.aeval(arg0)) >= (await self.aeval(arg1))

    async def ast_cmpop_is(self, arg0, arg1):
        """Evaluate comparison operator: is."""
        return (await self.aeval(arg0)) is (await self.aeval(arg1))

    async def ast_cmpop_isnot(self, arg0, arg1):
        """Evaluate comparison operator: is not."""
        return (await self.aeval(arg0)) is not (await self.aeval(arg1))

    async def ast_cmpop_in(self, arg0, arg1):
        """Evaluate comparison operator: in."""
        return (await self.aeval(arg0)) in (await self.aeval(arg1))

    async def ast_cmpop_notin(self, arg0, arg1):
        """Evaluate comparison operator: not in."""
        return (await self.aeval(arg0)) not in (await self.aeval(arg1))

    async def ast_boolop(self, arg):
        """Evaluate boolean operators and and or."""
        if isinstance(arg.op, ast.And):
            val = True
            for arg1 in arg.values:
                val = await self.aeval(arg1)
                if not val:
                    return val
            return val
        val = False
        for arg1 in arg.values:
            val = await self.aeval(arg1)
            if val:
                return val
        return val

    async def eval_elt_list(self, elts):
        """Evaluate and star list elements."""
        val = []
        for arg in elts:
            if isinstance(arg, ast.Starred):
                val += await self.aeval(arg.value)
            else:
                val.append(await self.aeval(arg))
        return val

    async def ast_list(self, arg):
        """Evaluate list."""
        if isinstance(arg.ctx, ast.Load):
            return await self.eval_elt_list(arg.elts)

    async def loopvar_scope_save(self, generators):
        """Return current scope variables that match looping target vars."""
        #
        # looping variables are in their own implicit nested scope, so save/restore
        # variables in the current scope with the same names
        #
        lvars = set()
        for gen in generators:
            await self.get_names(
                ast.Assign(targets=[gen.target], value=ast.Constant(value=None)), local_names=lvars
            )
        return lvars, {var: self.sym_table[var] for var in lvars if var in self.sym_table}

    def shares_locals(self):
        """Return whether we're in a function that shares its variables with inner functions or lambdas."""
        return self.in_local_scope() and self.curr_func is not None and self.curr_func.has_closure

    def new_loop_vars(self, var_names):
        """Return new local variables for a comprehension's loop vars, which a lambda in it can share."""
        if not self.shares_locals():
            return {}
        # loop variables bound by a global declaration stay global
        return {
            var_name: EvalLocalVar(var_name)
            for var_name in var_names
            if var_name.isidentifier() and not self.binds_global(var_name)
        }

    async def loopvar_scope_enter(self, generators):
        """Save current scope variables that match looping target vars, and give the loop vars their own."""
        var_names, _ = await self.loopvar_scope_save(generators)
        loop_scope = LoopScope(self.sym_table, var_names)
        prev_loop_scopes = self.loop_scopes
        if self.shares_locals():
            #
            # in a function that shares its variables, the loop variables are new locals, so the
            # function's own variables with these names aren't changed; a generator expression
            # made before this comprehension sees the bindings they replace (see genexp_loop)
            #
            self.loop_scopes = (*prev_loop_scopes, loop_scope)
            self.sym_table.update(self.new_loop_vars(var_names))
        return loop_scope, prev_loop_scopes

    async def loopvar_scope_exit(self, loop_scope, prev_loop_scopes):
        """Restore current scope variables that match looping target vars."""
        loop_scope.restore()
        self.loop_scopes = prev_loop_scopes

    async def listcomp_loop(self, generators, elt, loop_iter):
        """Recursive list comprehension."""
        out = []
        gen = generators[0]
        for loop_var in loop_iter:
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if len(generators) == 1:
                    out.append(await self.aeval(elt))
                else:
                    out += await self.listcomp_loop(
                        generators[1:], elt, await self.aeval(generators[1].iter)
                    )
        return out

    async def ast_listcomp(self, arg):
        """Evaluate list comprehension."""
        # as in Python, the outermost iterable is evaluated in the enclosing scope
        loop_iter = await self.aeval(arg.generators[0].iter)
        loop_scope, prev_loop_scopes = await self.loopvar_scope_enter(arg.generators)
        try:
            return await self.listcomp_loop(arg.generators, arg.elt, loop_iter)
        finally:
            await self.loopvar_scope_exit(loop_scope, prev_loop_scopes)

    async def ast_tuple(self, arg):
        """Evaluate Tuple."""
        return tuple(await self.eval_elt_list(arg.elts))

    async def ast_dict(self, arg):
        """Evaluate dict."""
        val = {}
        for key_ast, val_ast in zip(arg.keys, arg.values):
            this_val = await self.aeval(val_ast)
            if key_ast is None:
                val.update(this_val)
            else:
                val[await self.aeval(key_ast)] = this_val
        return val

    async def dictcomp_loop(self, generators, key, value, loop_iter):
        """Recursive dict comprehension."""
        out = {}
        gen = generators[0]
        for loop_var in loop_iter:
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if len(generators) == 1:
                    #
                    # key is evaluated before value starting in 3.8
                    #
                    key_val = await self.aeval(key)
                    out[key_val] = await self.aeval(value)
                else:
                    out.update(
                        await self.dictcomp_loop(
                            generators[1:], key, value, await self.aeval(generators[1].iter)
                        )
                    )
        return out

    async def ast_dictcomp(self, arg):
        """Evaluate dict comprehension."""
        loop_iter = await self.aeval(arg.generators[0].iter)
        loop_scope, prev_loop_scopes = await self.loopvar_scope_enter(arg.generators)
        try:
            return await self.dictcomp_loop(arg.generators, arg.key, arg.value, loop_iter)
        finally:
            await self.loopvar_scope_exit(loop_scope, prev_loop_scopes)

    async def ast_set(self, arg):
        """Evaluate set."""
        ret = set()
        for elt in await self.eval_elt_list(arg.elts):
            ret.add(elt)
        return ret

    async def setcomp_loop(self, generators, elt, loop_iter):
        """Recursive list comprehension."""
        out = set()
        gen = generators[0]
        for loop_var in loop_iter:
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if len(generators) == 1:
                    out.add(await self.aeval(elt))
                else:
                    out.update(
                        await self.setcomp_loop(generators[1:], elt, await self.aeval(generators[1].iter))
                    )
        return out

    async def ast_setcomp(self, arg):
        """Evaluate set comprehension."""
        loop_iter = await self.aeval(arg.generators[0].iter)
        loop_scope, prev_loop_scopes = await self.loopvar_scope_enter(arg.generators)
        try:
            return await self.setcomp_loop(arg.generators, arg.elt, loop_iter)
        finally:
            await self.loopvar_scope_exit(loop_scope, prev_loop_scopes)

    async def genexp_next(self, arg, iters):
        """Evaluate the next element of a generator expression, returning it in a tuple, or () at the end."""
        generators = arg.generators
        while iters:
            level = len(iters) - 1
            try:
                loop_var = next(iters[level])
            except StopIteration:
                iters.pop()
                continue
            gen = generators[level]
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if level + 1 == len(generators):
                    return (await self.aeval(arg.elt),)
                iters.append(iter(await self.aeval(generators[level + 1].iter)))
        return ()

    def genexp_loop(self, arg, iters, loop_vars, gen_vars, gen_scope, scope, loop):
        """Yield the elements of a generator expression, evaluating each one only when it's asked for."""
        #
        # native code (eg, sum(), any() or "".join()) iterates synchronously, so each element is
        # evaluated by running our async code to completion without returning to the event loop,
        # in the scope where the generator expression was created
        #
        gen_loop_scopes = (*scope["loop_scopes"], gen_scope)
        while True:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not loop:
                # pyscript code isn't thread safe, so it can't run in another thread (eg, task.executor)
                raise RuntimeError("a generator expression can only be iterated in the event loop")
            save_scope = {attr: getattr(self, attr) for attr in scope}
            for attr, value in scope.items():
                setattr(self, attr, value)
            self.sym_table_stack = scope["sym_table_stack"].copy()
            sym_table = self.sym_table
            #
            # a comprehension started after we were made (eg, the one iterating over us) has
            # its own loop variables in this scope; put back the bindings it replaced, newest first
            #
            hidden = []
            if save_scope["loop_scopes"] is not scope["loop_scopes"]:
                hidden = [
                    (loop_scope, loop_scope.restore())
                    for loop_scope in reversed(save_scope["loop_scopes"])
                    if loop_scope.sym_table is sym_table and loop_scope not in scope["loop_scopes"]
                ]
            # the loop variables are in their own scope, so hide any with the same names in this one
            outer_vars = {var: sym_table.pop(var) for var in loop_vars if var in sym_table}
            sym_table.update(gen_vars)
            if gen_scope is not None:
                # they replace bindings too, for any generator expression an element uses
                gen_scope.saved = outer_vars
                self.loop_scopes = gen_loop_scopes
            coro = self.genexp_next(arg, iters)
            try:
                coro.send(None)  # pylint: disable=no-member
            except StopIteration as exc:
                elt = exc.value
            else:
                #
                # the element has to wait, which can't be done here, so stop it; whatever
                # it was waiting for (eg, a task or a service call) carries on by itself
                #
                try:
                    coro.close()  # pylint: disable=no-member
                finally:
                    # raise this even if the element's cleanup (eg, a finally block) tries to wait too
                    raise RuntimeError(
                        "a generator expression element can't wait (eg, for task.sleep());"
                        " use a list comprehension instead"
                    )
            finally:
                gen_vars = {var: sym_table.pop(var) for var in loop_vars if var in sym_table}
                sym_table.update(outer_vars)
                if hidden:
                    for loop_scope, bindings in reversed(hidden):
                        # keep what the element assigned (eg, with :=) to the bindings we put back
                        loop_scope.keep_writes()
                        loop_scope.set_bindings(bindings)
                for attr, value in save_scope.items():
                    setattr(self, attr, value)
            if not elt:
                return
            yield elt[0]

    async def ast_generatorexp(self, arg):
        """Evaluate generator expression, returning a generator that evaluates each element lazily."""
        # as in Python, the outermost iterable is evaluated right away
        iters = [iter(await self.aeval(arg.generators[0].iter))]
        loop_vars, _ = await self.loopvar_scope_save(arg.generators)
        gen_vars = self.new_loop_vars(loop_vars)
        gen_scope = LoopScope(self.sym_table, loop_vars) if self.shares_locals() else None
        scope = {attr: getattr(self, attr) for attr in GENEXP_SCOPE_ATTRS}
        scope["sym_table_stack"] = self.sym_table_stack.copy()
        scope["genexp_iter"] = iters[0]  # what Python passes to the generator as its first argument
        gen = self.genexp_loop(arg, iters, loop_vars, gen_vars, gen_scope, scope, asyncio.get_running_loop())
        gen.__name__ = gen.__qualname__ = "<genexpr>"
        return gen

    async def ast_subscript(self, arg):
        """Evaluate subscript."""
        var = await self.aeval(arg.value)
        if isinstance(arg.ctx, ast.Load):
            return var[await self.aeval(arg.slice)]
        return None

    async def ast_index(self, arg):
        """Evaluate index."""
        return await self.aeval(arg.value)

    async def ast_slice(self, arg):
        """Evaluate slice."""
        lower = (await self.aeval(arg.lower)) if arg.lower else None
        upper = (await self.aeval(arg.upper)) if arg.upper else None
        step = (await self.aeval(arg.step)) if arg.step else None
        return slice(lower, upper, step)

    async def ast_call(self, arg):
        """Evaluate function call."""
        func = await self.aeval(arg.func)
        kwargs = {}
        for kw_arg in arg.keywords:
            if kw_arg.arg is None:
                kwargs.update(await self.aeval(kw_arg.value))
            else:
                kwargs[kw_arg.arg] = await self.aeval(kw_arg.value)
        args = await self.eval_elt_list(arg.args)
        #
        # try to deduce function name, although this only works in simple cases
        #
        func_name = None
        if isinstance(arg.func, ast.Name):
            func_name = arg.func.id
        elif isinstance(arg.func, ast.Attribute):
            func_name = arg.func.attr
        if isinstance(func, EvalLocalVar):
            func_name = func.get_name()
            func = func.get()
        return await self.call_func(func, func_name, *args, **kwargs)

    @staticmethod
    def class_init(mro):
        """Return the renamed pyscript __init__ Python would find first in mro, or None if it's native."""
        for klass in mro:
            if "__init__evalfunc_wrap__" in klass.__dict__:
                return klass.__dict__["__init__evalfunc_wrap__"]
            if "__init__" in klass.__dict__:
                return None
        return None

    @staticmethod
    def bind_init(init_func, obj, cls):
        """Bind a renamed __init__ to obj like Python's attribute lookup does; a static one stays unbound."""
        return init_func.__get__(obj, cls)  # pylint: disable=unnecessary-dunder-call

    def init_attr(self, val):
        """Return the pyscript __init__ for val.__init__, or None to use Python's attribute lookup."""
        if isinstance(val, super):
            #
            # super(cls, obj).__init__ searches the mro of obj (or of its type) after cls
            #
            mro = val.__self_class__.__mro__ if val.__self_class__ is not None else ()
            init_func = self.class_init(mro[mro.index(val.__thisclass__) + 1 :] if mro else ())
            if init_func is None:
                return None
            obj = None if val.__self__ is val.__self_class__ else val.__self__
            return self.bind_init(init_func, obj, val.__self_class__)
        if inspect.isclass(val):
            init_func = self.class_init(val.__mro__)
            return None if init_func is None else self.bind_init(init_func, None, val)
        if "__init__" in getattr(val, "__dict__", {}):
            # as in Python, an __init__ set on the instance comes before the class's
            return None
        init_func = self.class_init(type(val).__mro__)
        return None if init_func is None else self.bind_init(init_func, val, type(val))

    def super_args(self):
        """Return the arguments of a zero-argument super(), which Python finds in the calling method."""
        func = self.curr_func
        if self.genexp_iter is not None:
            # in a generator expression's element, Python's first argument is the outermost iterator
            if not func or func.class_cell is None:
                raise RuntimeError("super(): __class__ cell not found")
            if func.class_cell[0] is None:
                raise RuntimeError("super(): empty __class__ cell")
            return func.class_cell[0], self.genexp_iter
        func_args = func.func_def.args.posonlyargs + func.func_def.args.args if func else []
        if not func or not func_args:
            raise RuntimeError("super(): no arguments")
        if func.class_cell is None:
            raise RuntimeError("super(): __class__ cell not found")
        if func.class_cell[0] is None:
            raise RuntimeError("super(): empty __class__ cell")
        first_arg = self.sym_table.get(func_args[0].arg, EvalLocalVar(func_args[0].arg))
        if isinstance(first_arg, EvalLocalVar):
            # a method that defines a function keeps its local variables in EvalLocalVars
            if not first_arg.is_defined():
                raise RuntimeError("super(): arg[0] deleted")
            first_arg = first_arg.get()
        return func.class_cell[0], first_arg

    async def call_func(self, func, func_name, *args, **kwargs):
        """Call a function with the given arguments."""
        if func_name is None:
            try:
                if isinstance(func, (EvalFunc, EvalFuncVar)):
                    func_name = func.get_name()
                else:
                    func_name = func.__name__
            except Exception:
                func_name = "<function>"
        if _LOGGER.isEnabledFor(logging.DEBUG):
            try:
                arg_str = ", ".join(['"' + elt + '"' if isinstance(elt, str) else str(elt) for elt in args])
                kwargs_str = str(kwargs)
            except Exception:
                # eg, an instance whose __init__ hasn't run yet, or whose __str__ is a pyscript method
                arg_str = kwargs_str = "..."
            _LOGGER.debug("%s: calling %s(%s, %s)", self.name, func_name, arg_str, kwargs_str)
        if isinstance(func, (EvalFunc, EvalFuncVar)):
            return await func.call(self, *args, **kwargs)
        if isinstance(func, MethodType) and is_pyscript_func(func.__func__):
            # eg, a pyscript function wrapped by classmethod(), bound to the class
            return await func.__func__.call(self, func.__self__, *args, **kwargs)
        if func is super and not args and not kwargs:
            return super(*self.super_args())
        if inspect.isclass(func):
            init_func = self.class_init(func.__mro__)
            new_func = func.__new__
            if type(func).__call__ is not type.__call__:
                #
                # the metaclass has its own __call__, so let it create the instance,
                # and then call our __init__, which Python doesn't see
                #
                if init_func is not None:
                    inst = func()
                    await self.bind_init(init_func, inst, type(inst)).call(self, *args, **kwargs)
                    return inst
            elif init_func is not None or isinstance(new_func, (EvalFunc, EvalFuncVar)):
                #
                # Python can't call our async __init__ or __new__, so create the instance
                # like type.__call__ does: call __new__, and then __init__ if __new__
                # returned an instance of the class
                #
                if isinstance(new_func, (EvalFunc, EvalFuncVar)):
                    inst = await new_func.call(self, func, *args, **kwargs)
                elif new_func is object.__new__:
                    # object.__new__ rejects extra arguments, since our __init__ is renamed
                    inst = object.__new__(func)
                else:
                    # a native __new__ (eg, of str, tuple or Exception) needs the arguments
                    inst = new_func(func, *args, **kwargs)
                if not isinstance(inst, func):
                    return inst
                init_func = self.class_init(type(inst).__mro__)
                if init_func is None:
                    type(inst).__init__(inst, *args, **kwargs)  # pylint: disable=unnecessary-dunder-call
                    return inst
                ret = await self.bind_init(init_func, inst, type(inst)).call(self, *args, **kwargs)
                if ret is not None:
                    raise TypeError(f"__init__() should return None, not '{type(ret).__name__}'")
                return inst
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        if callable(func):
            if func == time.sleep:  # pylint: disable=comparison-with-callable
                _LOGGER.warning(
                    "%s calls blocking time.sleep(); replaced with asyncio.sleep()", self.filename
                )
                return await asyncio.sleep(*args, **kwargs)
            return func(*args, **kwargs)
        raise TypeError(f"'{func_name}' is not callable (got {func})")

    async def ast_ifexp(self, arg):
        """Evaluate if expression."""
        return await self.aeval(arg.body) if (await self.aeval(arg.test)) else await self.aeval(arg.orelse)

    async def ast_num(self, arg):
        """Evaluate number."""
        return arg.n

    async def ast_str(self, arg):
        """Evaluate string."""
        return arg.s

    async def ast_nameconstant(self, arg):
        """Evaluate name constant."""
        return arg.value

    async def ast_constant(self, arg):
        """Evaluate constant."""
        return arg.value

    async def ast_joinedstr(self, arg):
        """Evaluate joined string."""
        val = ""
        for arg1 in arg.values:
            this_val = await self.aeval(arg1)
            val = val + str(this_val)
        return val

    async def ast_formattedvalue(self, arg):
        """Evaluate formatted value."""
        val = await self.aeval(arg.value)
        if arg.format_spec is not None:
            fmt = await self.aeval(arg.format_spec)
            return f"{val:{fmt}}"
        return f"{val}"

    async def ast_await(self, arg):
        """Evaluate await expr."""
        coro = await self.aeval(arg.value)
        if coro and (asyncio.iscoroutine(coro) or asyncio.isfuture(coro)):
            return await coro
        return coro

    async def get_target_names(self, lhs):
        """Recursively find all the target names mentioned in the AST tree."""
        names = set()
        if isinstance(lhs, ast.Tuple):
            for lhs_elt in lhs.elts:
                if isinstance(lhs_elt, ast.Starred):
                    names.add(lhs_elt.value.id)
                else:
                    names = names.union(await self.get_target_names(lhs_elt))
        elif isinstance(lhs, ast.Attribute):
            var_name = await self.ast_attribute_collapse(lhs, check_undef=False)
            if isinstance(var_name, str):
                names.add(var_name)
        elif isinstance(lhs, ast.Name):
            names.add(lhs.id)
        return names

    async def get_names_set(self, arg, names, nonlocal_names, global_names, local_names):
        """Recursively find all the names mentioned in the AST tree."""

        cls_name = arg.__class__.__name__
        if cls_name == "Attribute":
            full_name = await self.ast_attribute_collapse(arg, check_undef=False)
            if full_name is not None:
                names.add(full_name)
                return
        if cls_name == "Name":
            names.add(arg.id)
            return
        if cls_name == "Nonlocal" and nonlocal_names is not None:
            for var_name in arg.names:
                nonlocal_names.add(var_name)
                names.add(var_name)
            return
        if cls_name == "Global" and global_names is not None:
            for var_name in arg.names:
                global_names.add(var_name)
                names.add(var_name)
            return
        if local_names is not None:
            #
            # find all the local variables by looking for assignments;
            # also, don't recurse into function definitions
            #
            if cls_name == "Assign":
                for target in arg.targets:
                    for name in await self.get_target_names(target):
                        local_names.add(name)
                        names.add(name)
            elif cls_name in {"AnnAssign", "AugAssign", "For", "AsyncFor", "NamedExpr"}:
                for name in await self.get_target_names(arg.target):
                    local_names.add(name)
                    names.add(name)
            elif cls_name in {"With", "AsyncWith"}:
                for item in arg.items:
                    if item.optional_vars:
                        for name in await self.get_target_names(item.optional_vars):
                            local_names.add(name)
                            names.add(name)
            elif cls_name in {"ListComp", "DictComp", "SetComp"}:
                target_vars, _ = await self.loopvar_scope_save(arg.generators)
                for name in target_vars:
                    local_names.add(name)
            elif cls_name in {"Import", "ImportFrom"}:
                # imported names are local too, so nested functions can share their binding
                for alias in arg.names:
                    name = alias.name if alias.asname is None else alias.asname
                    if name != "*" and "." not in name:
                        local_names.add(name)
                        names.add(name)
            elif cls_name == "Try":
                for handler in arg.handlers:
                    if handler.name is not None:
                        local_names.add(handler.name)
                        names.add(handler.name)
            elif cls_name == "Call":
                await self.get_names_set(arg.func, names, nonlocal_names, global_names, local_names)
                for this_arg in arg.args:
                    await self.get_names_set(this_arg, names, nonlocal_names, global_names, local_names)
                for this_arg in arg.keywords or []:
                    await self.get_names_set(this_arg, names, nonlocal_names, global_names, local_names)
                return
            elif cls_name in {"FunctionDef", "ClassDef", "AsyncFunctionDef"}:
                local_names.add(arg.name)
                names.add(arg.name)
                for dec in arg.decorator_list:
                    await self.get_names_set(dec, names, nonlocal_names, global_names, local_names)
                #
                # find unbound names from the body of the function or class
                #
                inner_global, inner_names, inner_local = set(), set(), set()
                for child in arg.body:
                    await self.get_names_set(child, inner_names, None, inner_global, inner_local)
                for name in inner_names:
                    if name not in inner_local and name not in inner_global:
                        names.add(name)
                return
            elif cls_name == "Lambda":
                # the defaults are in this scope; nothing in the body binds a name here
                for default in arg.args.defaults + [kwd for kwd in arg.args.kw_defaults if kwd is not None]:
                    await self.get_names_set(default, names, nonlocal_names, global_names, local_names)
                names.update(self.lambda_free_names(arg))
                return
            elif cls_name == "Delete":
                for arg1 in arg.targets:
                    if isinstance(arg1, ast.Name):
                        local_names.add(arg1.id)
        for child in ast.iter_child_nodes(arg):
            await self.get_names_set(child, names, nonlocal_names, global_names, local_names)

    async def get_names(self, this_ast=None, nonlocal_names=None, global_names=None, local_names=None):
        """Return set of all the names mentioned in our AST tree."""
        names = set()
        this_ast = this_ast or self.ast
        if this_ast:
            await self.get_names_set(this_ast, names, nonlocal_names, global_names, local_names)
        return names

    def parse(self, code_str: str | list[str], filename: str | None = None, mode: str = "exec") -> None:
        """Parse the code_str source code into an AST tree."""
        self.ast = None
        if filename is not None:
            self.filename = filename
        if isinstance(code_str, list):
            self.code_list = code_str
            self.code_str = "\n".join(code_str)
        elif isinstance(code_str, str):
            self.code_str = code_str
            self.code_list = code_str.split("\n")
        else:
            self.code_str = code_str
            self.code_list = []
        self.ast = ast.parse(self.code_str, filename=self.filename, mode=mode)

    def log_exception(self, exc: Exception) -> None:
        """Log eval exception."""
        try:
            fmt = EvalExceptionFormatter(exc)
            msg = "\n" + "".join(fmt.format())
            self.get_logger().error(msg)

            if _LOGGER.isEnabledFor(logging.DEBUG):
                msg = "\n" + "".join(traceback.format_tb(exc.__traceback__))
                _LOGGER.error(msg)
        except Exception:
            _LOGGER.exception("Error while formatting ast exception")

    def set_local_sym_table(self, sym_table):
        """Set the local symbol table."""
        self.local_sym_table = sym_table

    def set_global_ctx(self, global_ctx):
        """Set the global context."""
        self.global_ctx = global_ctx
        if self.sym_table == self.global_sym_table:
            self.global_sym_table = global_ctx.get_global_sym_table()
            self.sym_table = self.global_sym_table
        else:
            self.global_sym_table = global_ctx.get_global_sym_table()
        if len(self.sym_table_stack) > 0:
            self.sym_table_stack[0] = self.global_sym_table

    def get_global_ctx(self):
        """Return the global context."""
        return self.global_ctx

    def get_global_ctx_name(self):
        """Return the global context name."""
        return self.global_ctx.get_name()

    def set_logger_name(self, name):
        """Set the context's logger name."""
        if self.logger:
            for handler in self.logger_handlers:
                self.logger.removeHandler(handler)
        self.logger_name = name
        self.logger = logging.getLogger(LOGGER_PATH + "." + name)
        for handler in self.logger_handlers:
            self.logger.addHandler(handler)

    def get_logger_name(self):
        """Get the context's logger name."""
        return self.logger_name

    def get_logger(self):
        """Get the context's logger."""
        return self.logger

    def add_logger_handler(self, handler):
        """Add logger handler to this context."""
        self.logger.addHandler(handler)
        self.logger_handlers.add(handler)

    def remove_logger_handler(self, handler):
        """Remove logger handler to this context."""
        self.logger.removeHandler(handler)
        self.logger_handlers.discard(handler)

    def completions(self, root):
        """Return potential variable, function or attribute matches."""
        words = set()
        num_period = root.count(".")
        if num_period >= 1:
            last_period = root.rfind(".")
            name = root[0:last_period]
            attr_root = root[last_period + 1 :]
            if name in self.global_sym_table:
                var = self.global_sym_table[name]
                try:
                    for attr in var.__dict__:
                        if attr.lower().startswith(attr_root) and (attr_root != "" or attr[0:1] != "_"):
                            words.add(f"{name}.{attr}")
                except Exception:  # noqa: S110
                    pass
        for keyw in set(keyword.kwlist) - {"yield"}:
            if keyw.lower().startswith(root):
                words.add(keyw)
        sym_table = BUILTIN_AST_FUNCS_FACTORY.copy()
        for name, value in builtins.__dict__.items():
            if name[0] != "_" and name not in BUILTIN_EXCLUDE:
                sym_table[name] = value
        sym_table.update(self.global_sym_table.items())
        for name, value in sym_table.items():
            if name.lower().startswith(root):
                words.add(name)
        return words

    async def eval(self, new_state_vars: dict[str, Any] | None = None, merge_local: bool = False) -> None:
        """Execute parsed code, with the optional state variables added to the scope."""
        if new_state_vars:
            if not merge_local:
                self.local_sym_table = {}
            self.local_sym_table.update(new_state_vars)
        if self.ast:
            val = await self.aeval(self.ast)
            if isinstance(val, EvalStopFlow):
                return None
            return val
        return None

    def dump(self, this_ast=None):
        """Dump the AST tree for debugging."""
        return ast.dump(this_ast if this_ast else self.ast)


class EvalExceptionFormatter:
    """Format exceptions using pyscript-aware traceback frames."""

    def __init__(self, exc: BaseException) -> None:
        """Initialize exception formatter state."""
        self.exc = exc

        self.current_func: str | None = None
        self.current_code_list: list[str] | None = None
        self.current_filename: str | None = None
        self.last_eval_frame: traceback.FrameSummary | None = None
        self.lineno: int = 1
        self.col_offset: int = 0
        self.end_col_offset: int = 0

        self.stack = traceback.StackSummary()
        self._build_stack()
        self.chained_msg: str | None = None
        self.chained_exc: EvalExceptionFormatter | None = None
        if exc.__cause__ is not None:
            self.chained_msg = traceback._cause_message
            self.chained_exc = EvalExceptionFormatter(exc.__cause__)
        elif exc.__context__ is not None and not exc.__suppress_context__:
            self.chained_msg = traceback._context_message
            self.chained_exc = EvalExceptionFormatter(exc.__context__)

    def format(self) -> list[str]:
        """Return formatted traceback lines for this exception."""
        result = []

        if self.chained_msg and self.chained_exc:
            result.extend(self.chained_exc.format())
            result.append(self.chained_msg)

        result.extend(self.stack.format())
        if isinstance(self.exc, SyntaxError):
            format_exc = [f"{type(self.exc).__name__}: {self.exc.msg}\n"]
        else:
            format_exc = traceback.format_exception_only(self.exc)
        result.extend(format_exc)

        return result

    def _build_stack(self) -> None:
        """Build stack summary from traceback frames."""
        current_tb = self.exc.__traceback__

        while current_tb:
            frame = current_tb.tb_frame
            code = frame.f_code

            if code.co_filename == __file__:
                if code.co_qualname == EvalFunc.call.__qualname__:
                    eval_func = frame.f_locals.get("self")
                    if isinstance(eval_func, EvalFunc):
                        self.current_func = eval_func.get_name()
                        self.current_code_list = eval_func.code_list
                        self.current_filename = eval_func.global_ctx.get_file_path()
                elif code.co_qualname == AstEval.call_func.__qualname__ and self.current_func is None:
                    self.current_func = frame.f_locals.get("func_name", None)
                elif code.co_qualname == AstEval.parse.__qualname__ and isinstance(self.exc, SyntaxError):
                    ctx = frame.f_locals.get("self")
                    self.current_code_list = ctx.code_list
                    self.current_filename = ctx.global_ctx.get_file_path() or ctx.filename
                    self.lineno = self.exc.lineno
                    self.col_offset = self.exc.offset - 1
                    self.end_col_offset = self.exc.end_offset - 1
                    self.ast_frame(ctx)
                    # cancel frames from ast.py
                    return
                elif code.co_qualname in [AstEval.aeval.__qualname__, AstEval.recurse_assign.__qualname__]:
                    ctx = frame.f_locals.get("self")
                    if not self.current_filename:
                        self.current_filename = ctx.global_ctx.get_file_path() or ctx.filename
                    if not self.current_code_list:
                        self.current_code_list = ctx.code_list

                    for val in frame.f_locals.values():
                        if isinstance(val, (ast.expr, ast.stmt)) and hasattr(val, "lineno"):
                            self.lineno = getattr(val, "lineno", self.lineno)
                            self.col_offset = getattr(val, "col_offset", self.col_offset)
                            self.end_col_offset = getattr(val, "end_col_offset", self.col_offset)
                            self.ast_frame(ctx)
                            break
            else:
                self.real_frame(current_tb)

            current_tb = current_tb.tb_next

    def ast_frame(self, ctx: AstEval) -> None:
        """Add or replace a frame that points to AST source."""
        source = ctx.get_global_ctx().source or ""
        code_list = self.current_code_list or source.splitlines()
        if code_list and self.lineno <= len(code_list):
            codeline = code_list[self.lineno - 1]
        else:
            _LOGGER.error("Source code is unavailable for %s.", self.current_filename)
            codeline = None

        func = self.current_func
        if not func and self.current_filename != ctx.name:
            func = ctx.name

        new_frame = traceback.FrameSummary(
            filename=self.current_filename,
            lineno=self.lineno,
            name=func,
            colno=self.col_offset,
            line=codeline,
            lookup_line=False,
            end_lineno=self.lineno,
            end_colno=self.end_col_offset,
        )

        last_frame = self.stack[-1] if self.stack else None
        if last_frame and new_frame.filename == last_frame.filename and new_frame.name == last_frame.name:
            # Replace with more detailed (deeper) data
            self.stack[-1] = new_frame
        else:
            self.stack.append(new_frame)
        self.last_eval_frame = new_frame

    def real_frame(self, tb: TracebackType) -> None:
        """Add a regular Python traceback frame."""
        lineno = tb.tb_lineno
        end_lineno = colno = end_colno = None
        frame = tb.tb_frame
        code = frame.f_code

        if tb.tb_lasti >= 0:
            target = tb.tb_lasti // 2
            for idx, pos in enumerate(code.co_positions()):
                if idx == target:
                    if pos[0] is not None:
                        lineno = pos[0]
                    end_lineno, colno, end_colno = pos[1:]
                    break
        self.stack.append(
            traceback.FrameSummary(
                filename=code.co_filename,
                lineno=lineno,
                name=code.co_name,
                end_lineno=end_lineno,
                colno=colno,
                end_colno=end_colno,
            )
        )
