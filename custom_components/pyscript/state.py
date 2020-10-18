"""Handles state variable access and change notification."""

import logging

from .const import LOGGER_PATH
from .function import Function

_LOGGER = logging.getLogger(LOGGER_PATH + ".state")


class State:
    """Class for state functions."""

    #
    # Global hass instance
    #
    hass = None

    #
    # notify message queues by variable
    #
    notify = {}

    #
    # Last value of state variable notifications.  We maintain this
    # so that trigger evaluation can use the last notified value,
    # rather than fetching the current value, which is subject to
    # race conditions when multiple state variables are set.
    #
    notify_var_last = {}

    #
    # pyscript yaml configuration
    #
    pyscript_config = {}

    def __init__(self):
        """Warn on State instantiation."""
        _LOGGER.error("State class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize State."""
        cls.hass = hass

    @classmethod
    def notify_add(cls, var_names, queue):
        """Register to notify state variables changes to be sent to queue."""

        for var_name in var_names if isinstance(var_names, set) else {var_names}:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if state_var_name not in cls.notify:
                cls.notify[state_var_name] = {}
            cls.notify[state_var_name][queue] = var_names

    @classmethod
    def notify_del(cls, var_names, queue):
        """Unregister notify of state variables changes for given queue."""

        for var_name in var_names if isinstance(var_names, set) else {var_names}:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if state_var_name not in cls.notify or queue not in cls.notify[state_var_name]:
                return
            del cls.notify[state_var_name][queue]

    @classmethod
    async def update(cls, new_vars, func_args):
        """Deliver all notifications for state variable changes."""

        notify = {}
        for var_name, var_val in new_vars.items():
            if var_name in cls.notify:
                cls.notify_var_last[var_name] = var_val
                notify.update(cls.notify[var_name])

        if notify:
            _LOGGER.debug("state.update(%s, %s)", new_vars, func_args)
            for queue, var_names in notify.items():
                await queue.put(["state", [cls.notify_var_get(var_names, new_vars), func_args]])

    @classmethod
    def notify_var_get(cls, var_names, new_vars):
        """Return the most recent value of a state variable change."""
        notify_vars = {}
        for var_name in var_names if var_names is not None else []:
            if var_name in cls.notify_var_last:
                notify_vars[var_name] = cls.notify_var_last[var_name]
            elif var_name in new_vars:
                notify_vars[var_name] = new_vars[var_name]
            elif 1 <= var_name.count(".") <= 2 and not cls.exist(var_name):
                notify_vars[var_name] = None
        return notify_vars

    @classmethod
    def set(cls, var_name, value=None, new_attributes=None, **kwargs):
        """Set a state variable and optional attributes in hass."""
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")

        state_value = None
        if value is None or new_attributes is None:
            state_value = cls.hass.states.get(var_name)

        if value is None and state_value:
            value = state_value.state

        if new_attributes is None:
            if state_value:
                new_attributes = state_value.attributes
            else:
                new_attributes = {}
        if kwargs:
            new_attributes = new_attributes.copy()
            new_attributes.update(kwargs)
        _LOGGER.debug("setting %s = %s, attr = %s", var_name, value, new_attributes)
        cls.notify_var_last[var_name] = str(value)
        cls.hass.states.async_set(var_name, value, new_attributes)

    @classmethod
    def set_attr(cls, var_attr_name, value):
        """Set a state variable's attribute in hass."""
        parts = var_attr_name.split(".")
        if len(parts) != 3:
            raise NameError(f"invalid name {var_attr_name} (should be 'domain.entity.attr')")

        state_var_name = f"{parts[0]}.{parts[1]}"
        attr_name = parts[2]

        cls.set(state_var_name, **{attr_name: value})

    @classmethod
    def exist(cls, var_name):
        """Check if a state variable value or attribute exists in hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            return False
        value = cls.hass.states.get(f"{parts[0]}.{parts[1]}")
        return value and (len(parts) == 2 or parts[2] in value.attributes)

    @classmethod
    def get(cls, var_name):
        """Get a state variable value or attribute from hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            raise NameError(f"invalid name '{var_name}' (should be 'domain.entity')")
        value = cls.hass.states.get(f"{parts[0]}.{parts[1]}")
        if not value:
            raise NameError(f"name '{parts[0]}.{parts[1]}' is not defined")
        if len(parts) == 2:
            return value.state
        if parts[2] not in value.attributes:
            raise AttributeError(f"state '{parts[0]}.{parts[1]}' has no attribute '{parts[2]}'")
        return value.attributes.get(parts[2])

    @classmethod
    def get_attr(cls, var_name):
        """Return a dict of attributes for a state variable."""
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")
        value = cls.hass.states.get(var_name)
        if not value:
            return None
        return value.attributes.copy()

    @classmethod
    def completions(cls, root):
        """Return possible completions of state variables."""
        words = set()
        num_period = root.count(".")
        if num_period == 2:
            #
            # complete state attributes
            #
            last_period = root.rfind(".")
            name = root[0:last_period]
            value = cls.hass.states.get(name)
            if value:
                attr_root = root[last_period + 1 :]
                for attr_name in value.attributes.keys():
                    if attr_name.lower().startswith(attr_root):
                        words.add(f"{name}.{attr_name}")
        elif num_period < 2:
            #
            # complete among all state names
            #
            for name in cls.hass.states.async_all():
                if name.entity_id.lower().startswith(root):
                    words.add(name.entity_id)
        return words

    @classmethod
    async def names(cls, domain=None):
        """Implement names, which returns all entity_ids."""
        return cls.hass.states.async_entity_ids(domain)

    @classmethod
    def register_functions(cls):
        """Register state functions and config variable."""
        functions = {
            "state.get": cls.get,
            "state.set": cls.set,
            "state.set_attr": cls.set_attr,
            "state.names": cls.names,
            "state.get_attr": cls.get_attr,
            "pyscript.config": cls.pyscript_config,
        }
        Function.register(functions)

    @classmethod
    def set_pyscript_config(cls, config):
        """Set pyscript yaml config."""
        #
        # have to update inplace, since dist is already used as value
        #
        cls.pyscript_config.clear()
        for name, value in config.items():
            cls.pyscript_config[name] = value
