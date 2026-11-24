"""Implements the task functions that need the evaluator."""

import functools
import inspect

from .eval import AstEval, EvalFunc, EvalFuncVar
from .function import Function


class TaskFunctions:
    """Define task functions that need the evaluator."""

    @classmethod
    def register_functions(cls):
        """Register the task functions."""
        Function.register_ast({"task.create": cls.user_task_create_factory})
        Function.register(
            {
                "task.add_done_callback": cls.user_task_add_done_callback,
                "task.executor": cls.user_task_executor,
            }
        )

    @classmethod
    def user_task_create_factory(cls, ast_ctx):
        """Return wrapper to call to astFunction with the ast context."""

        async def user_task_create(func, /, *args, **kwargs):
            """Implement task.create()."""

            async def func_call(func, func_name, new_ast_ctx, /, *args, **kwargs):
                """Call user function inside task.create()."""
                try:
                    return await new_ast_ctx.call_func(func, func_name, *args, **kwargs)
                except Exception as e:
                    new_ast_ctx.log_exception(e)
                    return None

            try:
                if isinstance(func, (EvalFunc, EvalFuncVar)):
                    func_name = func.get_name()
                else:
                    func_name = func.__name__
            except Exception:
                func_name = "<function>"

            new_ast_ctx = AstEval(f"{ast_ctx.get_global_ctx_name()}.{func_name}", ast_ctx.get_global_ctx())
            Function.install_ast_funcs(new_ast_ctx)
            task = Function.create_task(
                func_call(func, func_name, new_ast_ctx, *args, **kwargs), ast_ctx=new_ast_ctx
            )
            Function.task_done_callback_ctx(task, new_ast_ctx)
            return task

        return user_task_create

    @classmethod
    async def user_task_add_done_callback(cls, task, callback, /, *args, **kwargs):
        """Implement task.add_done_callback()."""
        ast_ctx = None
        if type(callback) is EvalFuncVar:
            ast_ctx = callback.get_ast_ctx()
        Function.task_add_done_callback(task, ast_ctx, callback, *args, **kwargs)

    @classmethod
    async def user_task_executor(cls, func, /, *args, **kwargs):
        """Implement task.executor()."""
        if inspect.iscoroutinefunction(func) or not callable(func):
            raise TypeError(f"function {func} is not callable by task.executor")
        if isinstance(func, EvalFuncVar):
            raise TypeError(
                "pyscript functions can't be called from task.executor - must be a regular python function"
            )
        return await Function.hass.async_add_executor_job(functools.partial(func, **kwargs), *args)
