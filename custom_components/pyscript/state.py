"""Handles state variable access and change notification."""

import logging

from .const import LOGGER_PATH
from .handler import Handler

_LOGGER = logging.getLogger(LOGGER_PATH + ".state")


class State:
    """Class for state functions."""

    def __init__():
        """Warn on State instantiation."""
        _LOGGER.error("State class is not meant to be instantiated")

    def init(hass):
        """Initialize State."""

        State.hass = hass
        #
        # notify message queues by variable
        #
        State.notify = {}

        #
        # Last value of state variable notifications.  We maintain this
        # so that trigger evaluation can use the last notified value,
        # rather than fetching the current value, which is subject to
        # race conditions multiple state variables are set.
        #
        State.notify_var_last = {}

    def notify_add(var_names, queue):
        """Register to notify state variables changes to be sent to queue."""

        for var_name in var_names if isinstance(var_names, list) else [var_names]:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if state_var_name not in State.notify:
                State.notify[state_var_name] = {}
            State.notify[state_var_name][queue] = var_names

    def notify_del(var_names, queue):
        """Unregister notify of state variables changes for given queue."""

        for var_name in var_names if isinstance(var_names, list) else [var_names]:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if (
                state_var_name not in State.notify
                or queue not in State.notify[state_var_name]
            ):
                return
            del State.notify[state_var_name][queue]

    async def update(new_vars, func_args):
        """Deliver all notifications for state variable changes."""

        notify = {}
        for var_name, var_val in new_vars.items():
            if var_name in State.notify:
                State.notify_var_last[var_name] = var_val
                notify.update(State.notify[var_name])

        if notify:
            _LOGGER.debug("state.update(%s, %s)", new_vars, func_args)
            for queue, var_names in notify.items():
                await queue.put(["state", [State.notify_var_get(var_names), func_args]])

    def notify_var_get(var_names):
        """Return the most recent value of a state variable change."""
        new_vars = {}
        for var_name in var_names if var_names is not None else []:
            if var_name in State.notify_var_last:
                new_vars[var_name] = State.notify_var_last[var_name]
        return new_vars

    def set(var_name, value, attributes=None, **kwargs):
        """Set a state variable and optional attributes in hass."""
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")
        if attributes is None:
            state_value = State.hass.states.get(var_name)
            if state_value:
                attributes = state_value.attributes
            else:
                attributes = {}
        if kwargs:
            attributes = attributes.copy()
            attributes.update(kwargs)
        _LOGGER.debug("setting %s = %s, attr = %s", var_name, value, attributes)
        State.hass.states.async_set(var_name, value, attributes)

    def exist(var_name):
        """Check if a state variable value or attribute exists in hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            return False
        value = State.hass.states.get(f"{parts[0]}.{parts[1]}")
        return value and (len(parts) == 2 or value.attributes.get(parts[2]) is not None)

    def get(var_name):
        """Get a state variable value or attribute from hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")
        value = State.hass.states.get(f"{parts[0]}.{parts[1]}")
        if not value:
            return None
        if len(parts) == 2:
            return value.state
        return value.attributes.get(parts[2])

    def get_attr(var_name):
        """Return a dict of attributes for a state variable."""
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")
        value = State.hass.states.get(var_name)
        if not value:
            return None
        return value.attributes.copy()

    def completions(root):
        """Return possible completions of state variables."""
        words = set()
        num_period = root.count(".")
        if num_period == 2:
            #
            # complete state attributes
            #
            last_period = root.rfind(".")
            name = root[0:last_period]
            value = State.hass.states.get(name)
            if value:
                attr_root = root[last_period + 1 :]
                for attr_name in value.attributes.keys():
                    if attr_name.lower().startswith(attr_root):
                        words.add(f"{name}.{attr_name}")
        elif num_period < 2:
            #
            # complete among all state names
            #
            for name in State.hass.states.async_all():
                if name.entity_id.lower().startswith(root):
                    words.add(name.entity_id)
        return words

    async def names(domain=None):
        """Implement names, which returns all entity_ids."""
        return State.hass.states.async_entity_ids(domain)

    def register_functions():
        """Register state functions."""
        functions = {
            "state.get": State.get,
            "state.set": State.set,
            "state.names": State.names,
            "state.get_attr": State.get_attr,
        }
        Handler.register(functions)
