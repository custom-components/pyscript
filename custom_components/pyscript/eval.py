"""Python interpreter for pyscript."""

import ast
import asyncio
import builtins
from collections import OrderedDict
import importlib
import inspect
import io
import keyword
import logging
import sys

import yaml

from homeassistant.const import SERVICE_RELOAD
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


def ast_eval_exec_factory(ast_ctx, mode):
    """Generate a function that executes eval() or exec() with given ast_ctx."""

    async def eval_func(arg_str, eval_globals=None, eval_locals=None):
        eval_ast = AstEval(ast_ctx.name, ast_ctx.global_ctx)
        eval_ast.parse(arg_str, f"{mode}()", mode=mode)
        if eval_ast.exception_obj:
            raise eval_ast.exception_obj
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
            eval_ast.sym_table = ast_ctx.sym_table
        eval_ast.curr_func = ast_ctx.curr_func
        try:
            eval_result = await eval_ast.aeval(eval_ast.ast)
        except Exception as err:
            ast_ctx.exception_obj = err
            ast_ctx.exception = f"Exception in {ast_ctx.filename} line {ast_ctx.lineno} column {ast_ctx.col_offset}: {eval_ast.exception}"
            ast_ctx.exception_long = (
                ast_ctx.format_exc(err, ast_ctx.lineno, ast_ctx.col_offset, short=True)
                + "\n"
                + eval_ast.exception_long
            )
            raise
        ast_ctx.curr_func = eval_ast.curr_func
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
        return ast_ctx.sym_table

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


class EvalLocalVar:
    """Wrapper for local variable symtable entry."""

    def __init__(self, name, **kwargs):
        """Initialize value of local symbol."""
        self.name = name
        self.defined = False
        if "value" in kwargs:
            self.value = kwargs["value"]
            self.defined = True

    def get(self):
        """Get value of local symbol."""
        if not self.defined:
            raise NameError(f"name '{self.name}' is not defined")
        return self.value

    def get_name(self):
        """Get name of local symbol."""
        return self.name

    def set(self, value):
        """Set value of local symbol."""
        self.value = value
        self.defined = True

    def is_defined(self):
        """Return whether value is defined."""
        return self.defined

    def set_undefined(self):
        """Set local symbol to undefined."""
        self.defined = False

    def __getattr__(self, attr):
        """Get attribute of local variable."""
        if not self.defined:
            raise NameError(f"name '{self.name}' is not defined")
        return getattr(self.value, attr)

    def __repr__(self):
        """Generate string with address and value."""
        return f"EvalLocalVar @{hex(id(self))} = {self.value if self.defined else 'undefined'}"


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

    def setattr(self, value):
        """Set the attribute value."""
        setattr(self.obj, self.attr, value)

    def getattr(self):
        """Get the attribute value."""
        return getattr(self.obj, self.attr)


class EvalFunc:
    """Class for a callable pyscript function."""

    def __init__(self, func_def, code_list, code_str, global_ctx):
        """Initialize a function calling context."""
        self.func_def = func_def
        self.name = func_def.name
        self.global_ctx = global_ctx
        self.global_ctx_name = global_ctx.get_name()
        self.logger = logging.getLogger(LOGGER_PATH + "." + self.global_ctx_name)
        self.defaults = []
        self.kw_defaults = []
        self.decorators = []
        self.global_names = set()
        self.nonlocal_names = set()
        self.local_sym_table = {}
        self.doc_string = ast.get_docstring(func_def)
        self.num_posn_arg = len(self.func_def.args.args) - len(self.defaults)
        self.code_list = code_list
        self.code_str = code_str
        self.exception = None
        self.exception_obj = None
        self.exception_long = None
        self.trigger = None
        self.trigger_service = False
        self.has_closure = False

    def get_name(self):
        """Return the function name."""
        return self.name

    async def eval_defaults(self, ast_ctx):
        """Evaluate the default function arguments."""
        self.defaults = []
        for val in self.func_def.args.defaults:
            self.defaults.append(await ast_ctx.aeval(val))
        self.num_posn_arg = len(self.func_def.args.args) - len(self.defaults)
        self.kw_defaults = []
        for val in self.func_def.args.kw_defaults:
            self.kw_defaults.append({"ok": bool(val), "val": None if not val else await ast_ctx.aeval(val)})

    async def trigger_init(self):
        """Initialize decorator triggers for this function."""
        trig_args = {}
        got_reqd_dec = False
        trig_decorators_reqd = {
            "time_trigger",
            "state_trigger",
            "event_trigger",
            "mqtt_trigger",
        }
        trig_decorators = {
            "time_trigger",
            "state_trigger",
            "event_trigger",
            "mqtt_trigger",
            "state_active",
            "time_active",
            "task_unique",
        }
        decorator_used = set()
        for dec in self.decorators:
            dec_name, dec_args, dec_kwargs = dec[0], dec[1], dec[2]
            if dec_name in decorator_used:
                self.logger.error(
                    "%s defined in %s: decorator %s repeated; ignoring decorator",
                    self.name,
                    self.global_ctx_name,
                    dec_name,
                )
                continue
            decorator_used.add(dec_name)
            if dec_name in trig_decorators_reqd:
                got_reqd_dec = True
            if dec_name in trig_decorators:
                if dec_name not in trig_args:
                    trig_args[dec_name] = {}
                    trig_args[dec_name]["args"] = []
                if dec_args is not None:
                    trig_args[dec_name]["args"] += dec_args
                if dec_kwargs is not None:
                    trig_args[dec_name]["kwargs"] = dec_kwargs
            elif dec_name == "service":
                if dec_args is not None:
                    self.logger.error(
                        "%s defined in %s: decorator @service takes no arguments; ignoring decorator",
                        self.name,
                        self.global_ctx_name,
                    )
                    continue
                if self.name in (SERVICE_RELOAD, SERVICE_JUPYTER_KERNEL_START):
                    self.logger.error(
                        "function '%s' in %s with @service conflicts with builtin service; ignoring (please rename function)",
                        self.name,
                        self.global_ctx_name,
                    )
                    return
                desc = self.doc_string
                if desc is None or desc == "":
                    desc = f"pyscript function {self.name}()"
                desc = desc.lstrip(" \n\r")
                if desc.startswith("yaml"):
                    try:
                        desc = desc[4:].lstrip(" \n\r")
                        file_desc = io.StringIO(desc)
                        service_desc = yaml.load(file_desc, Loader=yaml.BaseLoader) or OrderedDict()
                        file_desc.close()
                    except Exception as exc:
                        self.logger.error(
                            "Unable to decode yaml doc_string for %s(): %s", self.name, str(exc),
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
                        ast_ctx = AstEval(f"{self.global_ctx_name}.{func_name}", self.global_ctx)
                        Function.install_ast_funcs(ast_ctx)
                        func_args = {
                            "trigger_type": "service",
                            "context": call.context,
                        }
                        func_args.update(call.data)

                        async def do_service_call(func, ast_ctx, data):
                            await func.call(ast_ctx, **data)
                            if ast_ctx.get_exception_obj():
                                ast_ctx.get_logger().error(ast_ctx.get_exception_long())

                        Function.create_task(do_service_call(func, ast_ctx, func_args))

                    return pyscript_service_handler

                Function.hass.services.async_register(
                    DOMAIN, self.name, pyscript_service_factory(self.name, self),
                )
                async_set_service_schema(Function.hass, DOMAIN, self.name, service_desc)
                self.trigger_service = True
            else:
                self.logger.warning(
                    "%s defined in %s: unknown decorator @%s: ignored",
                    self.name,
                    self.global_ctx_name,
                    dec_name,
                )

        for dec_name in trig_decorators:
            if dec_name in trig_args and len(trig_args[dec_name]["args"]) == 0:
                trig_args[dec_name]["args"] = None

        #
        # check that we have the right number of arguments, and that they are
        # strings
        #
        arg_check = {
            "event_trigger": {"arg_cnt": {1, 2}},
            "mqtt_trigger": {"arg_cnt": {1, 2}},
            "state_active": {"arg_cnt": {1}},
            "state_trigger": {"arg_cnt": {"*"}, "type": {list, set}},
            "task_unique": {"arg_cnt": {1}},
            "time_active": {"arg_cnt": {0, "*"}},
            "time_trigger": {"arg_cnt": {0, "*"}},
        }
        for dec_name, arg_info in arg_check.items():
            arg_cnt = arg_info["arg_cnt"]
            if dec_name not in trig_args:
                continue
            if trig_args[dec_name]["args"] is None:
                if 0 not in arg_cnt:
                    self.logger.error(
                        "%s defined in %s: decorator @%s needs at least one argument; ignoring decorator",
                        self.name,
                        self.global_ctx_name,
                        dec_name,
                    )
                    del trig_args[dec_name]
                continue
            if "*" not in arg_cnt and len(trig_args[dec_name]["args"]) not in arg_cnt:
                self.logger.error(
                    "%s defined in %s: decorator @%s got %d argument%s, expected %s; ignoring decorator",
                    self.name,
                    self.global_ctx_name,
                    dec_name,
                    len(trig_args[dec_name]["args"]),
                    "s" if len(trig_args[dec_name]["args"]) > 1 else "",
                    " or ".join([str(cnt) for cnt in sorted(arg_cnt)]),
                )
                del trig_args[dec_name]
                continue
            for arg_num, arg in enumerate(trig_args[dec_name]["args"]):
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
                self.logger.error(
                    "%s defined in %s: decorator @%s argument %d should be a %s; ignoring decorator",
                    self.name,
                    self.global_ctx_name,
                    dec_name,
                    arg_num + 1,
                    mesg,
                )
                del trig_args[dec_name]
                continue
            if arg_cnt == {1}:
                trig_args[dec_name]["args"] = trig_args[dec_name]["args"][0]

        kwarg_check = {
            "task_unique": {"kill_me"},
            "time_active": {"hold_off"},
            "state_trigger": {"state_hold", "state_check_now", "state_hold_false"},
        }
        for dec_name in trig_args:
            if dec_name not in kwarg_check and "kwargs" in trig_args[dec_name]:
                self.logger.error(
                    "%s defined in %s: decorator @%s doesn't take keyword arguments; ignored",
                    self.name,
                    self.global_ctx_name,
                    dec_name,
                )
            if dec_name in kwarg_check and "kwargs" in trig_args[dec_name]:
                used_kw = set(trig_args[dec_name]["kwargs"].keys())
                if not used_kw.issubset(kwarg_check[dec_name]):
                    self.logger.error(
                        "%s defined in %s: decorator @%s valid keyword arguments are: %s; others ignored",
                        self.name,
                        self.global_ctx_name,
                        dec_name,
                        ", ".join(sorted(kwarg_check[dec_name])),
                    )

        if not got_reqd_dec and len(trig_args) > 0:
            self.logger.error(
                "%s defined in %s: needs at least one trigger decorator (ie: %s)",
                self.name,
                self.global_ctx_name,
                ", ".join(sorted(trig_decorators_reqd)),
            )
            return

        if len(trig_args) == 0:
            if self.trigger_service:
                self.global_ctx.trigger_register(self)
            return

        trig_args["action"] = self
        trig_args["global_sym_table"] = self.global_ctx.global_sym_table

        self.trigger = self.global_ctx.get_trig_info(f"{self.global_ctx_name}.{self.name}", trig_args)
        if self.global_ctx.trigger_register(self):
            self.trigger_start()

    def trigger_start(self):
        """Start any triggers for this function."""
        if self.trigger:
            self.trigger.start()

    def trigger_stop(self):
        """Stop any triggers for this function."""
        if self.trigger:
            trigger = self.trigger
            self.trigger = None
            trigger.stop()
        if self.trigger_service:
            self.trigger_service = False
            Function.hass.services.async_remove(DOMAIN, self.name)

    async def eval_decorators(self, ast_ctx):
        """Evaluate the function decorators arguments."""
        self.decorators = []
        code_str, code_list = ast_ctx.code_str, ast_ctx.code_list
        ast_ctx.code_str, ast_ctx.code_list = self.code_str, self.code_list
        for dec in self.func_def.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                args = []
                kwargs = {}
                for arg in dec.args:
                    args.append(await ast_ctx.aeval(arg))
                for keyw in dec.keywords:
                    kwargs[keyw.arg] = await ast_ctx.aeval(keyw.value)
                if len(kwargs) == 0:
                    kwargs = None
                self.decorators.append([dec.func.id, args, kwargs])
            elif isinstance(dec, ast.Name):
                self.decorators.append([dec.id, None, None])
            else:
                _LOGGER.error("function %s has unexpected decorator type %s", self.name, dec)
        ast_ctx.code_str, ast_ctx.code_list = code_str, code_list

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
        for i in range(len(self.func_def.args.kwonlyargs)):
            args.append(self.func_def.args.kwonlyargs[i].arg)
        nonlocal_names = set()
        global_names = set()
        var_names = set(args)
        local_names = set(args)
        for stmt in self.func_def.body:
            self.has_closure = self.has_closure or isinstance(stmt, ast.FunctionDef)
            var_names = var_names.union(
                await ast_ctx.get_names(
                    stmt, nonlocal_names=nonlocal_names, global_names=global_names, local_names=local_names,
                )
            )
        for var_name in var_names:
            got_dot = var_name.find(".")
            if got_dot >= 0:
                var_name = var_name[0:got_dot]

            if var_name in global_names:
                continue

            if var_name in local_names and var_name not in nonlocal_names:
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
        for arg in self.func_def.args.args:
            args.append(arg.arg)
        return args

    async def try_aeval(self, ast_ctx, arg):
        """Call self.aeval and capture exceptions."""
        try:
            return await ast_ctx.aeval(arg)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if ast_ctx.exception_long is None:
                ast_ctx.exception_long = ast_ctx.format_exc(err, arg.lineno, arg.col_offset)

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the function with the given context and arguments."""
        sym_table = {}
        if args is None:
            args = []
        kwargs = kwargs.copy() if kwargs else {}
        for i in range(len(self.func_def.args.args)):
            var_name = self.func_def.args.args[i].arg
            val = None
            if i < len(args):
                val = args[i]
                if var_name in kwargs:
                    raise TypeError(f"{self.name}() got multiple values for argument '{var_name}'")
            elif var_name in kwargs:
                val = kwargs[var_name]
                del kwargs[var_name]
            elif self.num_posn_arg <= i < len(self.defaults) + self.num_posn_arg:
                val = self.defaults[i - self.num_posn_arg]
            else:
                raise TypeError(
                    f"{self.name}() missing {self.num_posn_arg - i} required positional arguments"
                )
            sym_table[var_name] = val
        for i in range(len(self.func_def.args.kwonlyargs)):
            var_name = self.func_def.args.kwonlyargs[i].arg
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
        if self.func_def.args.vararg:
            if len(args) > len(self.func_def.args.args):
                sym_table[self.func_def.args.vararg.arg] = tuple(args[len(self.func_def.args.args) :])
            else:
                sym_table[self.func_def.args.vararg.arg] = ()
        elif len(args) > len(self.func_def.args.args):
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
        self.exception = None
        self.exception_obj = None
        self.exception_long = None
        prev_func = ast_ctx.curr_func
        ast_ctx.curr_func = self
        for arg1 in self.func_def.body:
            val = await self.try_aeval(ast_ctx, arg1)
            if isinstance(val, EvalReturn):
                val = val.value
                break
            # return None at end if there isn't a return
            val = None
            if ast_ctx.get_exception_obj():
                break
        ast_ctx.curr_func = prev_func
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
        return val


class EvalFuncVar:
    """Class for a callable pyscript function."""

    def __init__(self, func):
        """Initialize instance with given EvalFunc function."""
        self.func = func

    def get_func(self):
        """Return the EvalFunc function."""
        return self.func

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the EvalFunc function."""
        return await self.func.call(ast_ctx, *args, **kwargs)

    def __del__(self):
        """On deletion, stop any triggers for this function."""
        self.func.trigger_stop()


class EvalFuncVarClassInst(EvalFuncVar):
    """Class for a callable pyscript class instance function."""

    def __init__(self, func, class_inst):
        """Initialize instance with given EvalFunc function."""
        super().__init__(func)
        self.class_inst = class_inst

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the EvalFunc function."""
        return await self.func.call(ast_ctx, self.class_inst, *args, **kwargs)


class EvalFuncVarAstCtx:
    """Class for a callable pyscript function with ast context."""

    def __init__(self, ast_ctx, eval_func_var):
        """Initialize instance with given EvalFunc function."""
        self.eval_func_var = eval_func_var
        self.ast_ctx = ast_ctx

    async def call(self, ast_ctx, *args, **kwargs):
        """Call the EvalFunc function."""
        return await self.eval_func_var.call(ast_ctx, *args, **kwargs)

    async def __call__(self, *args, **kwargs):
        """Call the EvalFunc function using our saved ast ctx."""
        return await self.eval_func_var.call(self.ast_ctx, *args, **kwargs)


class AstEval:
    """Python interpreter AST object evaluator."""

    def __init__(self, name, global_ctx, logger_name=None):
        """Initialize an interpreter execution context."""
        self.name = name
        self.str = None
        self.ast = None
        self.global_ctx = global_ctx
        self.global_sym_table = global_ctx.get_global_sym_table() if global_ctx else {}
        self.sym_table_stack = []
        self.sym_table = self.global_sym_table
        self.local_sym_table = {}
        self.curr_func = None
        self.filename = name
        self.code_str = None
        self.code_list = None
        self.exception = None
        self.exception_obj = None
        self.exception_long = None
        self.exception_curr = None
        self.lineno = 1
        self.col_offset = 0
        self.logger_handlers = set()
        self.logger = None
        self.set_logger_name(logger_name if logger_name is not None else self.name)
        self.config_entry = Function.hass.data.get(DOMAIN, {}).get(CONFIG_ENTRY, {})

    async def ast_not_implemented(self, arg, *args):
        """Raise NotImplementedError exception for unimplemented AST types."""
        name = "ast_" + arg.__class__.__name__.lower()
        raise NotImplementedError(f"{self.name}: not implemented ast " + name)

    async def aeval(self, arg, undefined_check=True):
        """Vector to specific function based on ast class type."""
        name = "ast_" + arg.__class__.__name__.lower()
        try:
            if hasattr(arg, "lineno"):
                self.lineno = arg.lineno
                self.col_offset = arg.col_offset
            val = await getattr(self, name, self.ast_not_implemented)(arg)
            if undefined_check and isinstance(val, EvalName):
                raise NameError(f"name '{val.name}' is not defined")
            if isinstance(val, EvalFuncVar):
                return EvalFuncVarAstCtx(self, val)
            return val
        except Exception as err:
            if not self.exception_obj:
                func_name = self.curr_func.get_name() + "(), " if self.curr_func else ""
                self.exception_obj = err
                self.exception = f"Exception in {func_name}{self.filename} line {self.lineno} column {self.col_offset}: {err}"
                self.exception_long = self.format_exc(err, self.lineno, self.col_offset)
            raise

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
            mod, error_ctx = await self.global_ctx.module_import(imp.name, 0)
            if error_ctx:
                self.exception_obj = error_ctx.exception_obj
                self.exception = error_ctx.exception
                self.exception_long = error_ctx.exception_long
                raise self.exception_obj
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
            self.sym_table[imp.name if imp.asname is None else imp.asname] = mod

    async def ast_importfrom(self, arg):
        """Execute from X import Y."""
        if arg.module is None:
            # handle: "from . import xyz"
            for imp in arg.names:
                mod, error_ctx = await self.global_ctx.module_import(imp.name, arg.level)
                if error_ctx:
                    self.exception_obj = error_ctx.exception_obj
                    self.exception = error_ctx.exception
                    self.exception_long = error_ctx.exception_long
                    raise self.exception_obj
                if not mod:
                    raise ModuleNotFoundError(f"module '{imp.name}' not found")
                self.sym_table[imp.name if imp.asname is None else imp.asname] = mod
            return
        mod, error_ctx = await self.global_ctx.module_import(arg.module, arg.level)
        if error_ctx:
            self.exception_obj = error_ctx.exception_obj
            self.exception = error_ctx.exception
            self.exception_long = error_ctx.exception_long
            raise self.exception_obj
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
                        self.sym_table[name] = value
            else:
                self.sym_table[imp.name if imp.asname is None else imp.asname] = getattr(mod, imp.name)

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
        self.sym_table[arg.name] = EvalLocalVar(arg.name)
        sym_table = {}
        self.sym_table_stack.append(self.sym_table)
        self.sym_table = sym_table
        for arg1 in arg.body:
            val = await self.aeval(arg1)
            if isinstance(val, EvalReturn):
                raise SyntaxError(f"{val.name()} statement outside function")
            if isinstance(val, EvalStopFlow):
                raise SyntaxError(f"{val.name()} statement outside loop")
        self.sym_table = self.sym_table_stack.pop()

        sym_table["__init__evalfunc_wrap__"] = None
        if "__init__" in sym_table:
            sym_table["__init__evalfunc_wrap__"] = sym_table["__init__"]
            del sym_table["__init__"]
        self.sym_table[arg.name].set(type(arg.name, tuple(bases), sym_table))

    async def ast_functiondef(self, arg):
        """Evaluate function definition."""
        func = EvalFunc(arg, self.code_list, self.code_str, self.global_ctx)
        await func.eval_defaults(self)
        await func.eval_decorators(self)
        await func.resolve_nonlocals(self)
        await func.trigger_init()
        name = func.get_name()
        func_var = EvalFuncVar(func)
        if name in self.sym_table and isinstance(self.sym_table[name], EvalLocalVar):
            self.sym_table[name].set(func_var)
        else:
            self.sym_table[name] = func_var

    async def ast_lambda(self, arg):
        """Evaluate lambda definition."""
        funcdef = ast.FunctionDef(
            args=arg.args, body=[ast.Return(value=arg.body)], name="lambda", decorator_list=None,
        )
        func = EvalFunc(funcdef, self.code_list, self.code_str, self.global_ctx)
        await func.eval_defaults(self)
        return EvalFuncVar(func)

    async def ast_asyncfunctiondef(self, arg):
        """Evaluate async function definition."""
        return await self.ast_functiondef(arg)

    async def ast_try(self, arg):
        """Execute try...except statement."""
        try:
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    return val
                if self.exception_obj is not None:
                    raise self.exception_obj
        except Exception as err:
            curr_exc = self.exception_curr
            self.exception_curr = err
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
                    save_obj = self.exception_obj
                    save_exc_long = self.exception_long
                    save_exc = self.exception
                    self.exception_obj = None
                    self.exception = None
                    self.exception_long = None
                    if handler.name is not None:
                        if handler.name in self.sym_table and isinstance(
                            self.sym_table[handler.name], EvalLocalVar
                        ):
                            self.sym_table[handler.name].set(err)
                        else:
                            self.sym_table[handler.name] = err
                    for arg1 in handler.body:
                        try:
                            val = await self.aeval(arg1)
                            if isinstance(val, EvalStopFlow):
                                if handler.name is not None:
                                    del self.sym_table[handler.name]
                                self.exception_curr = curr_exc
                                return val
                        except Exception:
                            if self.exception_obj is not None:
                                if handler.name is not None:
                                    del self.sym_table[handler.name]
                                self.exception_curr = curr_exc
                                if self.exception_obj == save_obj:
                                    self.exception_long = save_exc_long
                                    self.exception = save_exc
                                else:
                                    self.exception_long = (
                                        save_exc_long
                                        + "\n\nDuring handling of the above exception, another exception occurred:\n\n"
                                        + self.exception_long
                                    )
                                raise self.exception_obj  # pylint: disable=raise-missing-from
                    if handler.name is not None:
                        del self.sym_table[handler.name]
                    break
            else:
                self.exception_curr = curr_exc
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
                    return val  # pylint: disable=lost-exception
        return None

    async def ast_raise(self, arg):
        """Execute raise statement."""
        if not arg.exc:
            if not self.exception_curr:
                raise RuntimeError("No active exception to reraise")
            exc = self.exception_curr
        else:
            exc = await self.aeval(arg.exc)
        if self.exception_curr:
            exc.__cause__ = self.exception_curr
        if arg.cause:
            cause = await self.aeval(arg.cause)
            raise exc from cause
        raise exc

    async def ast_with(self, arg, async_attr=""):
        """Execute with statement."""
        hit_except = False
        ctx_list = []
        val = None
        enter_attr = f"__{async_attr}enter__"
        exit_attr = f"__{async_attr}exit__"
        try:
            for item in arg.items:
                manager = await self.aeval(item.context_expr)
                ctx_list.append(
                    {
                        "manager": manager,
                        "enter": getattr(type(manager), enter_attr),
                        "exit": getattr(type(manager), exit_attr),
                        "target": item.optional_vars,
                    }
                )
            for ctx in ctx_list:
                if ctx["target"]:
                    value = await self.call_func(ctx["enter"], enter_attr, ctx["manager"])
                    await self.recurse_assign(ctx["target"], value)
            for arg1 in arg.body:
                val = await self.aeval(arg1)
                if isinstance(val, EvalStopFlow):
                    break
        except Exception:
            hit_except = True
            exit_ok = True
            for ctx in reversed(ctx_list):
                ret = await self.call_func(ctx["exit"], exit_attr, ctx["manager"], *sys.exc_info())
                exit_ok = exit_ok and ret
            if not exit_ok:
                raise
        finally:
            if not hit_except:
                for ctx in reversed(ctx_list):
                    await self.call_func(ctx["exit"], exit_attr, ctx["manager"], None, None, None)
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
                vals = [*(val.__iter__())]
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
                        ast.Name(id=star_name, ctx=ast.Store()), vals[val_idx : val_idx + star_len],
                    )
                    val_idx += star_len
                else:
                    await self.recurse_assign(lhs_elt, vals[val_idx])
                    val_idx += 1
        elif isinstance(lhs, ast.Subscript):
            var = await self.aeval(lhs.value)
            if isinstance(lhs.slice, ast.Index):
                ind = await self.aeval(lhs.slice.value)
                var[ind] = val
            else:
                lower = await self.aeval(lhs.slice.lower) if lhs.slice.lower else None
                upper = await self.aeval(lhs.slice.upper) if lhs.slice.upper else None
                step = await self.aeval(lhs.slice.step) if lhs.slice.step else None
                var[slice(lower, upper, step)] = val
        else:
            var_name = await self.aeval(lhs)
            if isinstance(var_name, EvalAttrSet):
                var_name.setattr(val)
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
            if self.curr_func and var_name in self.curr_func.global_names:
                self.global_sym_table[var_name] = val
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
                if isinstance(arg1.slice, ast.Index):
                    ind = await self.aeval(arg1.slice.value)
                    for elt in ind if isinstance(ind, list) else [ind]:
                        del var[elt]
                elif isinstance(arg1.slice, ast.Slice):
                    lower, upper, step = None, None, None
                    if arg1.slice.lower:
                        lower = await self.aeval(arg1.slice.lower)
                    if arg1.slice.upper:
                        upper = await self.aeval(arg1.slice.upper)
                    if arg1.slice.step:
                        step = await self.aeval(arg1.slice.step)
                    del var[slice(lower, upper, step)]
                else:
                    raise NotImplementedError(f"{self.name}: not implemented slice type {arg1.slice} in del")
            elif isinstance(arg1, ast.Name):
                if self.curr_func and arg1.id in self.curr_func.global_names:
                    if arg1.id in self.global_sym_table:
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
                var_name = await self.ast_attribute_collapse(arg1, check_undef=False)
                if not isinstance(var_name, str):
                    raise NameError("state name should be 'domain.entity' or 'domain.entity.attr'")
                State.delete(var_name)
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
        return getattr(val, arg.attr)

    async def ast_name(self, arg):
        """Look up value of identifier on load, or returns name on set."""
        if isinstance(arg.ctx, ast.Load):
            #
            # check other scopes if required by global declarations
            #
            if self.curr_func and arg.id in self.curr_func.global_names:
                if arg.id in self.global_sym_table:
                    return self.global_sym_table[arg.id]
                raise NameError(f"global name '{arg.id}' is not defined")
            #
            # now check in our current symbol table, and then some other places
            #
            if arg.id in self.sym_table:
                if isinstance(self.sym_table[arg.id], EvalLocalVar):
                    return self.sym_table[arg.id].get()
                return self.sym_table[arg.id]
            if arg.id in self.local_sym_table:
                return self.local_sym_table[arg.id]
            if arg.id in self.global_sym_table:
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
                return await State.get(arg.id)
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
            val = 1
            for arg1 in arg.values:
                this_val = await self.aeval(arg1)
                if this_val == 0:
                    return 0
                val = this_val
            return val
        for arg1 in arg.values:
            val = await self.aeval(arg1)
            if val != 0:
                return val
        return 0

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

    async def loopvar_scope_restore(self, var_names, save_vars):
        """Restore current scope variables that match looping target vars."""
        for var_name in var_names:
            if var_name in save_vars:
                self.sym_table[var_name] = save_vars[var_name]
            else:
                del self.sym_table[var_name]

    async def listcomp_loop(self, generators, elt):
        """Recursive list comprehension."""
        out = []
        gen = generators[0]
        for loop_var in await self.aeval(gen.iter):
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if len(generators) == 1:
                    out.append(await self.aeval(elt))
                else:
                    out += await self.listcomp_loop(generators[1:], elt)
        return out

    async def ast_listcomp(self, arg):
        """Evaluate list comprehension."""
        target_vars, save_values = await self.loopvar_scope_save(arg.generators)
        result = await self.listcomp_loop(arg.generators, arg.elt)
        await self.loopvar_scope_restore(target_vars, save_values)
        return result

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

    async def dictcomp_loop(self, generators, key, value):
        """Recursive dict comprehension."""
        out = {}
        gen = generators[0]
        for loop_var in await self.aeval(gen.iter):
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
                    out.update(await self.dictcomp_loop(generators[1:], key, value))
        return out

    async def ast_dictcomp(self, arg):
        """Evaluate dict comprehension."""
        target_vars, save_values = await self.loopvar_scope_save(arg.generators)
        result = await self.dictcomp_loop(arg.generators, arg.key, arg.value)
        await self.loopvar_scope_restore(target_vars, save_values)
        return result

    async def ast_set(self, arg):
        """Evaluate set."""
        ret = set()
        for elt in await self.eval_elt_list(arg.elts):
            ret.add(elt)
        return ret

    async def setcomp_loop(self, generators, elt):
        """Recursive list comprehension."""
        out = set()
        gen = generators[0]
        for loop_var in await self.aeval(gen.iter):
            await self.recurse_assign(gen.target, loop_var)
            for cond in gen.ifs:
                if not await self.aeval(cond):
                    break
            else:
                if len(generators) == 1:
                    out.add(await self.aeval(elt))
                else:
                    out.update(await self.setcomp_loop(generators[1:], elt))
        return out

    async def ast_setcomp(self, arg):
        """Evaluate set comprehension."""
        target_vars, save_values = await self.loopvar_scope_save(arg.generators)
        result = await self.setcomp_loop(arg.generators, arg.elt)
        await self.loopvar_scope_restore(target_vars, save_values)
        return result

    async def ast_subscript(self, arg):
        """Evaluate subscript."""
        var = await self.aeval(arg.value)
        if isinstance(arg.ctx, ast.Load):
            if isinstance(arg.slice, ast.Index):
                return var[await self.aeval(arg.slice)]
            if isinstance(arg.slice, ast.Slice):
                lower = (await self.aeval(arg.slice.lower)) if arg.slice.lower else None
                upper = (await self.aeval(arg.slice.upper)) if arg.slice.upper else None
                step = (await self.aeval(arg.slice.step)) if arg.slice.step else None
                return var[slice(lower, upper, step)]
        else:
            return None

    async def ast_index(self, arg):
        """Evaluate index."""
        return await self.aeval(arg.value)

    async def ast_slice(self, arg):
        """Evaluate slice."""
        return await self.aeval(arg.value)

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
        arg_str = ", ".join(['"' + elt + '"' if isinstance(elt, str) else str(elt) for elt in args])
        #
        # try to deduce function name, although this only works in simple cases
        #
        if isinstance(arg.func, ast.Name):
            func_name = arg.func.id
        elif isinstance(arg.func, ast.Attribute):
            func_name = arg.func.attr
        else:
            func_name = "<function>"
        if isinstance(func, EvalLocalVar):
            func_name = func.get_name()
            func = func.get()
        _LOGGER.debug("%s: calling %s(%s, %s)", self.name, func_name, arg_str, kwargs)
        return await self.call_func(func, func_name, *args, **kwargs)

    async def call_func(self, func, func_name, *args, **kwargs):
        """Call a function with the given arguments."""
        if isinstance(func, (EvalFuncVar, EvalFuncVarAstCtx)):
            return await func.call(self, *args, **kwargs)
        if inspect.isclass(func) and hasattr(func, "__init__evalfunc_wrap__"):
            inst = func()
            for name in inst.__dir__():
                value = getattr(inst, name)
                if type(value) is not EvalFuncVar:
                    continue
                setattr(inst, name, EvalFuncVarClassInst(value.get_func(), inst))
            if getattr(func, "__init__evalfunc_wrap__") is not None:
                #
                # since our __init__ function is async, call the renamed one
                #
                await inst.__init__evalfunc_wrap__.call(self, *args, **kwargs)
            return inst
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        if callable(func):
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
        return await self.aeval(arg.value)

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

    async def get_names_set(self, arg, names, nonlocal_names=None, global_names=None, local_names=None):
        """Recursively find all the names mentioned in the AST tree."""

        cls_name = arg.__class__.__name__
        if cls_name == "Attribute":
            full_name = await self.ast_attribute_collapse(arg)
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
            elif cls_name in {"AugAssign", "For", "AsyncFor", "NamedExpr"}:
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
            elif cls_name == "Try":
                for handler in arg.handlers:
                    if handler.name is not None:
                        local_names.add(handler.name)
                        names.add(handler.name)
            elif cls_name == "Call":
                await self.get_names_set(
                    arg.func,
                    names,
                    nonlocal_names=nonlocal_names,
                    global_names=global_names,
                    local_names=local_names,
                )
                for this_arg in arg.args:
                    await self.get_names_set(
                        this_arg,
                        names,
                        nonlocal_names=nonlocal_names,
                        global_names=global_names,
                        local_names=local_names,
                    )
                return
            elif cls_name in {"FunctionDef", "ClassDef", "AsyncFunctionDef"}:
                local_names.add(arg.name)
                names.add(arg.name)
                return
            elif cls_name == "Delete":
                for arg1 in arg.targets:
                    if isinstance(arg1, ast.Name):
                        local_names.add(arg1.id)
        for child in ast.iter_child_nodes(arg):
            await self.get_names_set(
                child,
                names,
                nonlocal_names=nonlocal_names,
                global_names=global_names,
                local_names=local_names,
            )

    async def get_names(self, this_ast=None, nonlocal_names=None, global_names=None, local_names=None):
        """Return set of all the names mentioned in our AST tree."""
        names = set()
        this_ast = this_ast or self.ast
        if this_ast:
            await self.get_names_set(
                this_ast,
                names,
                nonlocal_names=nonlocal_names,
                global_names=global_names,
                local_names=local_names,
            )
        return names

    def parse(self, code_str, filename=None, mode="exec"):
        """Parse the code_str source code into an AST tree."""
        self.exception = None
        self.exception_obj = None
        self.exception_long = None
        self.ast = None
        if filename is not None:
            self.filename = filename
        try:
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
            return True
        except SyntaxError as err:
            self.exception_obj = err
            self.lineno = err.lineno
            self.col_offset = err.offset - 1
            self.exception = f"syntax error {err}"
            if err.filename == self.filename:
                self.exception_long = self.format_exc(err, self.lineno, self.col_offset)
            else:
                self.exception_long = self.format_exc(err, 1, self.col_offset, code_list=[err.text])
            return False
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.exception_obj = err
            self.lineno = 1
            self.col_offset = 0
            self.exception = f"parsing error {err}"
            self.exception_long = self.format_exc(err)
            return False

    def format_exc(self, exc, lineno=None, col_offset=None, short=False, code_list=None):
        """Format an multi-line exception message using lineno if available."""
        if code_list is None:
            code_list = self.code_list
        if lineno is not None:
            if short:
                mesg = f"In <{self.filename}> line {lineno}:\n"
                mesg += "    " + code_list[lineno - 1]
            else:
                mesg = f"Exception in <{self.filename}> line {lineno}:\n"
                mesg += "    " + code_list[lineno - 1] + "\n"
                if col_offset is not None:
                    mesg += "    " + " " * col_offset + "^\n"
                mesg += f"{type(exc).__name__}: {exc}"
        else:
            mesg = f"Exception in <{self.filename}>:\n"
            mesg += f"{type(exc).__name__}: {exc}"
        return mesg

    def get_exception(self):
        """Return the last exception str."""
        return self.exception

    def get_exception_obj(self):
        """Return the last exception object."""
        return self.exception_obj

    def get_exception_long(self):
        """Return the last exception in a longer str form."""
        return self.exception_long

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
                except Exception:
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

    async def eval(self, new_state_vars=None):
        """Execute parsed code, with the optional state variables added to the scope."""
        self.exception = None
        self.exception_obj = None
        self.exception_long = None
        if new_state_vars:
            self.local_sym_table.update(new_state_vars)
        if self.ast:
            try:
                val = await self.aeval(self.ast)
                if isinstance(val, EvalStopFlow):
                    return None
                return val
            except asyncio.CancelledError:
                raise
            except Exception as err:
                if self.exception_long is None:
                    self.exception_long = self.format_exc(err, self.lineno, self.col_offset)
        return None

    def dump(self, this_ast=None):
        """Dump the AST tree for debugging."""
        return ast.dump(this_ast if this_ast else self.ast)
