"""Global context handling."""

import logging

from .const import LOGGER_PATH
from .function import Function
from .trigger import TrigInfo

_LOGGER = logging.getLogger(LOGGER_PATH + ".global_ctx")


class GlobalContext:
    """Define class for global variables and trigger context."""

    def __init__(self, name, hass, global_sym_table=None):
        """Initialize GlobalContext."""
        self.name = name
        self.hass = hass
        self.global_sym_table = global_sym_table if global_sym_table else {}
        self.triggers = set()
        self.triggers_delay_start = set()
        self.logger = logging.getLogger(LOGGER_PATH + "." + name)
        self.auto_start = False

    def trigger_register(self, func):
        """Register a trigger function; return True if start now."""
        self.triggers.add(func)
        if self.auto_start:
            return True
        self.triggers_delay_start.add(func)
        return False

    def trigger_unregister(self, func):
        """Unregister a trigger function."""
        self.triggers.discard(func)
        self.triggers_delay_start.discard(func)

    def set_auto_start(self, auto_start):
        """Set the auto-start flag."""
        self.auto_start = auto_start

    def start(self):
        """Start any unstarted triggers."""
        for func in self.triggers_delay_start:
            func.trigger_start()
        self.triggers_delay_start = set()

    def stop(self):
        """Stop all triggers."""
        for func in self.triggers:
            func.trigger_stop()
        self.triggers = set()
        self.triggers_delay_start = set()

    def get_name(self):
        """Return the global context name."""
        return self.name

    def get_global_sym_table(self):
        """Return the global symbol table."""
        return self.global_sym_table

    def get_trig_info(self, name, trig_args):
        """Return a new trigger info instance with the given args."""
        return TrigInfo(name, trig_args, self)


class GlobalContextMgr:
    """Define class for all global contexts."""

    #
    # map of context names to contexts
    #
    contexts = {}

    #
    # sequence number for sessions
    #
    name_seq = 0

    def __init__(self):
        """Report an error if GlobalContextMgr in instantiated."""
        _LOGGER.error("GlobalContextMgr class is not meant to be instantiated")

    @classmethod
    def init(cls):
        """Initialize GlobalContextMgr."""

        def get_global_ctx_factory(ast_ctx):
            """Generate a pyscript.get_global_ctx() function with given ast_ctx."""

            async def get_global_ctx():
                return ast_ctx.get_global_ctx_name()

            return get_global_ctx

        def list_global_ctx_factory(ast_ctx):
            """Generate a pyscript.list_global_ctx() function with given ast_ctx."""

            async def list_global_ctx():
                ctx_names = set(cls.contexts.keys())
                curr_ctx_name = ast_ctx.get_global_ctx_name()
                ctx_names.discard(curr_ctx_name)
                return [curr_ctx_name] + sorted(sorted(ctx_names))

            return list_global_ctx

        def set_global_ctx_factory(ast_ctx):
            """Generate a pyscript.set_global_ctx() function with given ast_ctx."""

            async def set_global_ctx(name):
                global_ctx = cls.get(name)
                if global_ctx is None:
                    raise NameError(f"global context '{name}' does not exist")
                ast_ctx.set_global_ctx(global_ctx)
                ast_ctx.set_logger_name(global_ctx.name)

            return set_global_ctx

        ast_funcs = {
            "pyscript.get_global_ctx": get_global_ctx_factory,
            "pyscript.list_global_ctx": list_global_ctx_factory,
            "pyscript.set_global_ctx": set_global_ctx_factory,
        }

        Function.register_ast(ast_funcs)

    @classmethod
    def get(cls, name):
        """Return the GlobalContext given a name."""
        return cls.contexts.get(name, None)

    @classmethod
    def set(cls, name, global_ctx):
        """Save the GlobalContext by name."""
        cls.contexts[name] = global_ctx

    @classmethod
    def items(cls):
        """Return all the global context items."""
        return sorted(cls.contexts.items())

    @classmethod
    async def delete(cls, name):
        """Delete the given GlobalContext."""
        if name in cls.contexts:
            global_ctx = cls.contexts[name]
            global_ctx.stop()
            del cls.contexts[name]

    @classmethod
    def new_name(cls, root):
        """Find a unique new name by appending a sequence number to root."""
        while True:
            name = f"{root}{cls.name_seq}"
            cls.name_seq += 1
            if name not in cls.contexts:
                return name
