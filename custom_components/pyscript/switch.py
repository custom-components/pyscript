"""Pyscript Switch Entity"""
from .entity_manager import EntityManager, PyscriptEntity
from .eval import EvalFunc

PLATFORM = "switch"


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Initialize Pyscript Switch Platform"""
    EntityManager.register_platform(PLATFORM, async_add_entities, PyscriptSwitch)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Initialize Pyscript Switch Config"""
    return await async_setup_platform(hass, config_entry.data, async_add_entities, discovery_info=None)


class PyscriptSwitch(PyscriptEntity):
    """A Pysript Switch Entity"""
    platform = PLATFORM

    def __init__(self, *args, **kwargs):
        """Initialize Pyscript Switch"""
        super().__init__(*args, **kwargs)
        self._turn_on_handler = None
        self._turn_off_handler = None

    async def async_turn_on(self, **kwargs):
        """Handle turn_on request."""
        if self._turn_on_handler is None:
            return

        if callable(self._turn_on_handler):
            await self._turn_on_handler(self, **kwargs)
        elif isinstance(self._turn_on_handler, EvalFunc):
            await self._turn_on_handler.call(self.ast_ctx, self, **kwargs)
        else:
            raise RuntimeError(f"Unable to Call turn_on_handler of type {type(self._turn_on_handler)}")

    async def async_turn_off(self, **kwargs):
        """Handle turn_off request."""
        if self._turn_off_handler is None:
            return

        if callable(self._turn_off_handler):
            await self._turn_off_handler(self, **kwargs)
        elif isinstance(self._turn_off_handler, EvalFunc):
            await self._turn_off_handler.call(self.ast_ctx, self, **kwargs)
        else:
            raise RuntimeError(f"Unable to Call turn_off_handler of type {type(self._turn_off_handler)}")

    def set_state(self, state):
        """Handle state validation"""
        state = state.lower()

        if state not in ("on", "off"):
            raise ValueError('Switch state must be "on" or "off"')

        super().set_state(state)

    # TO BE USED IN PYSCRIPT
    ######################################

    def on_turn_on(self, func):
        """Setup handler for turn_on functionality"""
        self._turn_on_handler = func

    def on_turn_off(self, func):
        """Setup handler for turn_off functionality"""
        self._turn_off_handler = func