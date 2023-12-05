"""Handles state variable access and change notification."""

import asyncio
import logging

from homeassistant.core import Context
from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
from homeassistant.helpers.service import async_get_all_descriptions

from .const import LOGGER_PATH
from .entity import PyscriptEntity
from .function import Function

_LOGGER = logging.getLogger(LOGGER_PATH + ".state")

STATE_VIRTUAL_ATTRS = {"entity_id", "last_changed", "last_updated"}


class StateVal(str):
    """Class for representing the value and attributes of a state variable."""

    def __new__(cls, state):
        """Create a new instance given a state variable."""
        new_var = super().__new__(cls, state.state)
        new_var.__dict__ = state.attributes.copy()
        new_var.entity_id = state.entity_id
        new_var.last_updated = state.last_updated
        new_var.last_changed = state.last_changed
        return new_var


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
    # race conditions when multiple state variables are set quickly.
    #
    notify_var_last = {}

    #
    # pyscript yaml configuration
    #
    pyscript_config = {}

    #
    # pyscript vars which have already been registered as persisted
    #
    persisted_vars = {}

    #
    # other parameters of all services that have "entity_id" as a parameter
    #
    service2args = {}

    def __init__(self):
        """Warn on State instantiation."""
        _LOGGER.error("State class is not meant to be instantiated")

    @classmethod
    def init(cls, hass):
        """Initialize State."""
        cls.hass = hass

    @classmethod
    async def get_service_params(cls):
        """Get parameters for all services."""
        cls.service2args = {}
        all_services = await async_get_all_descriptions(cls.hass)
        for domain in all_services:
            cls.service2args[domain] = {}
            for service, desc in all_services[domain].items():
                if "entity_id" not in desc["fields"] and "target" not in desc:
                    continue
                cls.service2args[domain][service] = set(desc["fields"].keys())
                cls.service2args[domain][service].discard("entity_id")

    @classmethod
    async def notify_add(cls, var_names, queue):
        """Register to notify state variables changes to be sent to queue."""

        added = False
        for var_name in var_names if isinstance(var_names, set) else {var_names}:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if state_var_name not in cls.notify:
                cls.notify[state_var_name] = {}
            cls.notify[state_var_name][queue] = var_names
            added = True
        return added

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
                await queue.put(["state", [cls.notify_var_get(var_names, new_vars), func_args.copy()]])

    @classmethod
    def notify_var_get(cls, var_names, new_vars):
        """Add values of var_names to new_vars, or default to None."""
        notify_vars = new_vars.copy()
        for var_name in var_names if var_names is not None else []:
            if var_name in notify_vars:
                continue
            parts = var_name.split(".")
            if var_name in cls.notify_var_last:
                notify_vars[var_name] = cls.notify_var_last[var_name]
            elif len(parts) == 3 and f"{parts[0]}.{parts[1]}" in cls.notify_var_last:
                notify_vars[var_name] = getattr(
                    cls.notify_var_last[f"{parts[0]}.{parts[1]}"], parts[2], None
                )
            elif len(parts) == 4 and parts[2] == "old" and f"{parts[0]}.{parts[1]}.old" in notify_vars:
                notify_vars[var_name] = getattr(notify_vars[f"{parts[0]}.{parts[1]}.old"], parts[3], None)
            elif 1 <= var_name.count(".") <= 3 and not cls.exist(var_name):
                notify_vars[var_name] = None
        return notify_vars

    @classmethod
    def set(cls, var_name, value=None, new_attributes=None, **kwargs):
        """Set a state variable and optional attributes in hass."""
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")

        if isinstance(value, StateVal):
            if new_attributes is None:
                #
                # value is a StateVal, so extract the attributes and value
                #
                new_attributes = value.__dict__.copy()
                for discard in STATE_VIRTUAL_ATTRS:
                    new_attributes.pop(discard, None)
            value = str(value)

        state_value = None
        if value is None or new_attributes is None:
            state_value = cls.hass.states.get(var_name)

        if value is None and state_value:
            value = state_value.state

        if new_attributes is None:
            if state_value:
                new_attributes = state_value.attributes.copy()
            else:
                new_attributes = {}

        curr_task = asyncio.current_task()
        if "context" in kwargs and isinstance(kwargs["context"], Context):
            context = kwargs["context"]
            del kwargs["context"]
        else:
            context = Function.task2context.get(curr_task, None)

        if kwargs:
            new_attributes = new_attributes.copy()
            new_attributes.update(kwargs)

        _LOGGER.debug("setting %s = %s, attr = %s", var_name, value, new_attributes)
        cls.hass.states.async_set(var_name, value, new_attributes, context=context)
        if var_name in cls.notify_var_last or var_name in cls.notify:
            #
            # immediately update a variable we are monitoring since it could take a while
            # for the state changed event to propagate
            #
            cls.notify_var_last[var_name] = StateVal(cls.hass.states.get(var_name))

        if var_name in cls.persisted_vars:
            cls.persisted_vars[var_name].set_state(value)
            cls.persisted_vars[var_name].set_attributes(new_attributes)

    @classmethod
    def setattr(cls, var_attr_name, value):
        """Set a state variable's attribute in hass."""
        parts = var_attr_name.split(".")
        if len(parts) != 3:
            raise NameError(f"invalid name {var_attr_name} (should be 'domain.entity.attr')")
        if not cls.exist(f"{parts[0]}.{parts[1]}"):
            raise NameError(f"state {parts[0]}.{parts[1]} doesn't exist")
        cls.set(f"{parts[0]}.{parts[1]}", **{parts[2]: value})

    @classmethod
    async def register_persist(cls, var_name):
        """Register pyscript state variable to be persisted with RestoreState."""
        if var_name.startswith("pyscript.") and var_name not in cls.persisted_vars:
            # this is a hack accessing hass internals; should re-implement using RestoreEntity
            restore_data = cls.hass.data[DATA_RESTORE_STATE]
            this_entity = PyscriptEntity()
            this_entity.entity_id = var_name
            cls.persisted_vars[var_name] = this_entity
            try:
                restore_data.async_restore_entity_added(this_entity)
            except TypeError:
                restore_data.async_restore_entity_added(var_name)

    @classmethod
    async def persist(cls, var_name, default_value=None, default_attributes=None):
        """Persist a pyscript domain state variable, and update with optional defaults."""
        if var_name.count(".") != 1 or not var_name.startswith("pyscript."):
            raise NameError(f"invalid name {var_name} (should be 'pyscript.entity')")

        await cls.register_persist(var_name)
        exists = cls.exist(var_name)

        if not exists and default_value is not None:
            cls.set(var_name, default_value, default_attributes)
        elif exists and default_attributes is not None:
            # Patch the attributes with new values if necessary
            current = cls.hass.states.get(var_name)
            new_attributes = {k: v for (k, v) in default_attributes.items() if k not in current.attributes}
            cls.set(var_name, current.state, **new_attributes)

    @classmethod
    def exist(cls, var_name):
        """Check if a state variable value or attribute exists in hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            return False
        value = cls.hass.states.get(f"{parts[0]}.{parts[1]}")
        if value is None:
            return False
        if (
            len(parts) == 2
            or (parts[0] in cls.service2args and parts[2] in cls.service2args[parts[0]])
            or parts[2] in value.attributes
            or parts[2] in STATE_VIRTUAL_ATTRS
        ):
            return True
        return False

    @classmethod
    def get(cls, var_name):
        """Get a state variable value or attribute from hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            raise NameError(f"invalid name '{var_name}' (should be 'domain.entity' or 'domain.entity.attr')")
        state = cls.hass.states.get(f"{parts[0]}.{parts[1]}")
        if not state:
            raise NameError(f"name '{parts[0]}.{parts[1]}' is not defined")
        #
        # simplest case is just the state value
        #
        state = StateVal(state)
        if len(parts) == 2:
            return state
        #
        # see if this is a service that has an entity_id parameter
        #
        if parts[0] in cls.service2args and parts[2] in cls.service2args[parts[0]]:
            params = cls.service2args[parts[0]][parts[2]]

            def service_call_factory(domain, service, entity_id, params):
                async def service_call(*args, **kwargs):
                    curr_task = asyncio.current_task()
                    hass_args = {}
                    for keyword, typ, default in [
                        ("context", [Context], Function.task2context.get(curr_task, None)),
                        ("blocking", [bool], None),
                        ("return_response", [bool], None),
                        ("limit", [float, int], None),
                    ]:
                        if keyword in kwargs and type(kwargs[keyword]) in typ:
                            hass_args[keyword] = kwargs.pop(keyword)
                        elif default:
                            hass_args[keyword] = default

                    kwargs["entity_id"] = entity_id
                    if len(args) == 1 and len(params) == 1:
                        #
                        # with just a single parameter and positional argument, create the keyword setting
                        #
                        [param_name] = params
                        kwargs[param_name] = args[0]
                    elif len(args) != 0:
                        raise TypeError(f"service {domain}.{service} takes no positional arguments")

                    # return await Function.hass_services_async_call(domain, service, kwargs, **hass_args)
                    return await cls.hass.services.async_call(domain, service, kwargs, **hass_args)

                return service_call

            return service_call_factory(parts[0], parts[2], f"{parts[0]}.{parts[1]}", params)
        #
        # finally see if it is an attribute
        #
        try:
            return getattr(state, parts[2])
        except AttributeError:
            raise AttributeError(  # pylint: disable=raise-missing-from
                f"state '{parts[0]}.{parts[1]}' has no attribute '{parts[2]}'"
            )

    @classmethod
    def delete(cls, var_name, context=None):
        """Delete a state variable or attribute from hass."""
        parts = var_name.split(".")
        if not context:
            context = Function.task2context.get(asyncio.current_task(), None)
        context_arg = {"context": context} if context else {}
        if len(parts) == 2:
            if var_name in cls.notify_var_last or var_name in cls.notify:
                #
                # immediately update a variable we are monitoring since it could take a while
                # for the state changed event to propagate
                #
                cls.notify_var_last[var_name] = None
            if not cls.hass.states.async_remove(var_name, **context_arg):
                raise NameError(f"name '{var_name}' not defined")
            return
        if len(parts) == 3:
            var_name = f"{parts[0]}.{parts[1]}"
            value = cls.hass.states.get(var_name)
            if value is None:
                raise NameError(f"state {var_name} doesn't exist")
            new_attr = value.attributes.copy()
            if parts[2] not in new_attr:
                raise AttributeError(f"state '{var_name}' has no attribute '{parts[2]}'")
            del new_attr[parts[2]]
            cls.set(f"{var_name}", value.state, new_attributes=new_attr, **context_arg)
            return
        raise NameError(f"invalid name '{var_name}' (should be 'domain.entity' or 'domain.entity.attr')")

    @classmethod
    def getattr(cls, var_name):
        """Return a dict of attributes for a state variable."""
        if isinstance(var_name, StateVal):
            attrs = var_name.__dict__.copy()
            for discard in STATE_VIRTUAL_ATTRS:
                attrs.pop(discard, None)
            return attrs
        if var_name.count(".") != 1:
            raise NameError(f"invalid name {var_name} (should be 'domain.entity')")
        value = cls.hass.states.get(var_name)
        if not value:
            return None
        return value.attributes.copy()

    @classmethod
    def get_attr(cls, var_name):
        """Return a dict of attributes for a state variable - deprecated."""
        _LOGGER.warning("state.get_attr() is deprecated: use state.getattr() instead")
        return cls.getattr(var_name)

    @classmethod
    def completions(cls, root):
        """Return possible completions of state variables."""
        words = set()
        parts = root.split(".")
        num_period = len(parts) - 1
        if num_period == 2:
            #
            # complete state attributes
            #
            last_period = root.rfind(".")
            name = root[0:last_period]
            value = cls.hass.states.get(name)
            if value:
                attr_root = root[last_period + 1 :]
                attrs = set(value.attributes.keys()).union(STATE_VIRTUAL_ATTRS)
                if parts[0] in cls.service2args:
                    attrs.update(set(cls.service2args[parts[0]].keys()))
                for attr_name in attrs:
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
            "state.setattr": cls.setattr,
            "state.names": cls.names,
            "state.getattr": cls.getattr,
            "state.get_attr": cls.get_attr,  # deprecated form; to be removed
            "state.persist": cls.persist,
            "state.delete": cls.delete,
            "pyscript.config": cls.pyscript_config,
        }
        Function.register(functions)

    @classmethod
    def set_pyscript_config(cls, config):
        """Set pyscript yaml config."""
        #
        # have to update inplace, since dest is already used as value
        #
        cls.pyscript_config.clear()
        for name, value in config.items():
            cls.pyscript_config[name] = value
