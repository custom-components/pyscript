Reference
=========

Configuration
-------------

Pyscript can be configured using the UI, or via yaml. To use the UI, go to the
Configuration -> Integrations page and selection "+" to add ``Pyscript Python scripting``.
After that, you can change the settings anytime by selecting Options under Pyscript
in the Configuration page.

Alternatively, for yaml configuration, add ``pyscript:`` to ``<config>/configuration.yaml``.
You can't mix these two methods - your initial choice determines how you should update
these settings later.  If you want to switch configuration methods you will need to
uninstall and reinstall pyscript.

Pyscript has two optional configuration parameters that allow any python package to be
imported and exposes the ``hass`` variable as a global (both options default to ``false``).
In `<config>/configuration.yaml``:

.. code:: yaml

   pyscript:
     allow_all_imports: true
     hass_is_global: true

The settings and behavior of your code can be controlled by additional user-defined yaml
configuration settings.  If you configured pyscript using the UI flow, you can still
add additional configuration settings via yaml.  Since they are free-form (no fixed
schema) there is no UI configuration available for these additional settings.

All the pyscript configuration settings are available via the variable ``pyscript.config``
(see `this section <#accessing-yaml-configuration>`__). The recommended structure is
to have entries for each application you write stored under an ``apps`` entry.
For example, applications ``my_app1`` and ``my_app2`` would be configured as:

.. code:: yaml

   pyscript:
     allow_all_imports: true
     apps:
        my_app1:
           # any settings for my_app1 go here
        my_app2:
           # any settings for my_app2 go here

As explained below, the use of ``apps`` with entries for each application by name below,
is used to determine which application scripts are autoloaded. That's the only configuration
structure that pyscript checks - any other parameters can be added and used as you like.

At startup, pyscript loads the following files. It also unloads and reloads these files when
the ``pyscript.reload`` service is called, which also reloads the ``yaml`` configuration.

``<config>/pyscript/*.py``
  all files with a ``.py`` suffix are autoloaded

``<config>/pyscript/apps/<app_name>.py``
  all files in the ``apps`` subdirectory with a ``.py`` suffix are autoloaded, provided ``app_name``
  exists in the pyscript ``yaml`` configuration under ``apps`` (that allows each app to be disabled by
  simply removing its configuration and reloading).

``<config>/pyscript/apps/<app_name>/__init__.py``
  every ``__init__.py`` file in a subdirectory in the ``apps`` subdirectory is autoloaded,
  provided ``app_name`` exists in the pyscript ``yaml`` configuration under ``apps``.
  This form is most convenient for sharing pyscript code, since all the files for one
  application are stored in its own directory.

Like regular Python, functions within one source file can call each other, and can share global
variables (if necessary), but just within that one file. Each file has its own separate global
context. Each Jupyter session also has its own separate global context, so functions, triggers,
variables and services defined in each interactive session are isolated from the script files and
other Jupyter sessions. Pyscript provides some utility functions to switch global contexts, which
allows an interactive Jupyter session to interact directly with functions and global variables
created by a script file, or even another Jupyter session.

The optional ``<config>/pyscript/modules`` subdirectory can contain modules (files with a ``.py``
extension) or packages (directories that contain at least a ``__init__.py`` file) that can be
imported by any other pyscript files, applications or modules. Any modules or packages
imported from ``<config>/pyscript/modules`` are unloaded if you call the ``pyscript.reload``
service. They are not autoloaded. Importing modules and packages from ``<config>/pyscript/modules``
are not restricted if ``allow_all_imports`` is ``False``. Typically common functions or
features would be implemented in a module or package, and then imported and used by scripts
in ``<config>/pyscript`` or applications in ``<config>/pyscript/apps``.

Even if you can’t directly call one function from another script file, HASS state variables are
global and services can be called from any script file.

Reloading the ``.py`` files is accomplished by calling the ``pyscript.reload`` service, which is the
one built-in service (so you can’t create your own service with that name). All function
definitions, services and triggers are re-created on ``reload``, except for any active Jupyter
sessions. Any currently running functions (ie, functions that have been triggered and are actively
executing Python code or waiting inside ``task.sleep()`` or ``task.wait_until()``) are not stopped
by ``reload`` - they continue to run until they finish (return). You can terminate these running
functions too on ``reload`` if you wish by calling ``task.unique()`` in the script file preamble
(ie, outside any function definition), so it is executed on load and reload, which will terminate
any running functions that have previously called ``task.unique()`` with the same argument.


State Variables
---------------

State variables can be accessed in any Python code simply by name. State variables (also called
``entity_id``) are of the form ``DOMAIN.name``, where ``DOMAIN`` is typically the name of the
component that sets that variable. You can set a state variable by assigning to it.

State variables only have string values, so you will need to convert them to ``int`` or ``float`` if
you need their numeric value.

State variables have attributes that can be accessed by adding the name of the attribute, as in
``DOMAIN.name.attr``. The attribute names and their meaning depend on the component that sets them,
so you will need to look at the State tab in the Developer Tools to see the available attributes.

Starting in version 0.21, when you set a state variable, the existing attributes are not affected
(they were previously removed).

In cases where you need to compute the name of the state variable dynamically, or you need to set or
get the state attributes, you can use the built-in functions ``state.get()``, ``state.get_attr()``
and ``state.set()``; see below.

The function ``state.names(domain=None)`` returns a list of all state variable names (ie,
``entity_id``\ s) of a domain. If ``domain`` is not specified, it returns all HASS state
variable (entity) names.

Also, service names (which are called as functions) take priority over state variable names, so if a
component has a state variable name that collides with one of its services, you’ll need to use
``state.get(name)`` to access that state variable.

Accessing state variables that don't exist will throw a ``NameError`` exception, and accessing
an attribute that doesn't exist will throw a ``AttributeError`` exception.

Calling services
----------------

Any service can be called by using the service name as a function, with keyword parameters to
specify the service parameters. You’ll need to look up the service in the Service tab of Developer
Tools to find the exact name and parameters. For example, inside any function you can call:

.. code:: python

       myservice.flash_light(light_name="front", light_color="red")

which calls the ``myservice.flash_light`` service with the indicated parameters. Obviously those
parameter values could be any Python expression, and this call could be inside a loop, an if
statement or any other Python code.

The function ``service.call(domain, name, **kwargs)`` can also be used to call a service when you
need to compute the domain or service name dynamically. For example, the above service could also be
called by:

.. code:: python

       service.call("myservice", "flash_light", light_name="front", light_color="red")

Firing events
-------------

Any event can be triggered by calling ``event.fire(event_type, **kwargs)``. It takes the
``event_type`` as a first argument, and any keyword parameters as the event parameters. The
``event_type`` could be a user-defined string, or it could be one of the built-in events. You can
access the names of those built-in events by importing from ``homeassistant.const``, eg:

.. code:: python

   from homeassistant.const import EVENT_CALL_SERVICE

Function Decorators
-------------------

There are three decorators for defining state, time and event triggers and two decorators for
defining whether any trigger actually causes the function to run (i.e., is active), based on
state-based expressions or one or more time-windows. The decorators should appear immediately before
the function they refer to. A single function can have any or all of the decorator types specified,
but at most one of each type.

A Python function with decorators is still a normal Python function that can be called by any other
Python function. The decorators have no effect in the case where you call it directly from another
function.

@state_trigger
^^^^^^^^^^^^^^

.. code:: python

    @state_trigger(str_expr, ...)

``@state_trigger`` takes one or more string arguments that contain any expression based on one or
more state variables, and evaluates to ``True`` or ``False`` (or non-zero or zero). Whenever the
state variables mentioned in the expression change, the expression is evaluated and the trigger
occurs if it evaluates to ``True`` (or non-zero). For each state variable, eg: ``domain.name``,
the prior value is also available to the expression as ``domain.name.old`` in case you want to
condition the trigger on the prior value too.

Multiple arguments are logically "or"ed together, so the trigger occurs if any of the expressions
evaluate to ``True``. Any argument can alternatively be a list or set of strings, and they are
treated the same as multiple arguments by "or"ing them together.

All state variables in HASS have string values. So you’ll have to do comparisons against string
values or cast the variable to an integer or float. These two examples are essentially equivalent
(note the use of single quotes inside the outer double quotes):

.. code:: python

   @state_trigger("domain.light_level == '255' or domain.light2_level == '0'")

.. code:: python

   @state_trigger("int(domain.light_level) == 255 or int(domain.light2_level) == 0")

although the second will throw an exception if the variable string doesn’t represent a valid integer.
If you want numerical inequalities you should use the second form, since string lexicographic
ordering is not the same as numeric ordering.

You can also use state variable attributes in the trigger expression, with an idenfitier of the
form ``DOMAIN.name.attr``. Attributes maintain their original type, so there is no need to cast
then to another type.

You can specify a state trigger on any change with a string that is just the state variable name:

.. code:: python

   @state_trigger("domain.light_level")

The trigger can include arguments with any mixture of string expressions (that are evaluated
when any of the underlying state variables change) and string state variable names (that trigger
whenever that variable changes).

Note that if a state variable is set to the same value, HASS doesn’t generate a state change event,
so the ``@state_trigger`` condition will not be checked. It is only evaluated each time a state
variable changes to a new value.

When the trigger occurs and the function is executed (meaning any active checks passed too), keyword
arguments are passed to the function so it can tell which state variable caused it to succeed and
run, in cases where the trigger condition involves multiple variables. These are:

.. code:: python

   kwargs = {
       "trigger_type": "state",
       "var_name": var_name,
       "value": new_value,
       "old_value": old_value
   }

If your function needs to know any of these values, you can list the keyword arguments you need,
with defaults:

.. code:: python

   @state_trigger("domain.light_level == '255' or domain.light2_level == '0'")
   def light_turned_on(trigger_type=None, var_name=None, value=None):
       pass

Using ``trigger_type`` is helpful if you have multiple trigger decorators. The function can now tell
which type of trigger, and which of the two variables changed to cause the trigger. You can also use
the keyword catch-all declaration instead:

.. code:: python

   @state_trigger("domain.light_level == '255' or domain.light2_level == '0'")
   def light_turned_on(**kwargs)
       log.info(f"got arguments {kwargs}")

and all those values will simply get passed in into kwargs as a ``dict``. That’s the most useful
form to use if you have multiple decorators, since each one passes different variables into the
function (although all of them set ``trigger_type``).

Inside ``str_expr``, undefined state variables, undefined state attributes, and undefined
``.old`` variables evaluate to ``None``, rather than throwing an exception. The ``.old`` variable will
be ``None`` the first time the state variable is set (since it has no prior value), and when the
``str_expr`` is being evaluated because a different state variable changed (only the state variable
change that caused ``str_expr`` to be evaluated gets its prior value in ``.old``; any other ``.old``
variables will be ``None`` for that evaluation).

@time_trigger
^^^^^^^^^^^^^

.. code:: python

    @time_trigger(time_spec, ...)

``@time_trigger`` takes one or more string specifications that specify time-based triggers. When
multiple time triggers are specified, each are evaluated, and the earliest one is the next trigger.
Then the process repeats.

Several of the time specifications use a ``datetime`` format, which is ISO: ``yyyy/mm/dd hh:mm:ss``,
with the following features:

- There is no time-zone (local is assumed).
- Seconds can include a decimal (fractional) portion if you need finer resolution.
- The date is optional, and the year can be omitted with just ``mm/dd``.
- The date can also be replaced by a day of the week (either full like ``sunday``
  or 3-letters like ``sun``, based on the locale).
- The meaning of partial or missing dates depends on the trigger, as explained below.
- The time can instead be ``sunrise``, ``sunset``, ``noon`` or ``midnight``.
- The ``datetime`` can be followed by an optional offset
  of the form ``[+-]number{seconds|minutes|hours|days|weeks}`` and abbreviations ``{s|m|h|d|w}`` or
  ``{sec|min|hr|day|week}`` can be used. That allows things like ``sunrise + 30m`` to mean 30
  minutes after sunrise, or ``sunday sunset - 1h`` to mean an hour before sunset on Sundays. The
  ``number`` can be floating point. (Note, there is no i18n support for those offset abbreviations -
  they are in English.)

In ``@time_trigger``, each string specification ``time_spec`` can take one of four forms:

- ``"startup"`` triggers on HASS start and reload.
- ``"once(datetime)"`` triggers once on the date and time. If the year is
  omitted, it triggers once per year on the date and time (eg, birthday). If the date is just a day
  of week, it triggers once on that day of the week. If the date is omitted, it triggers once each
  day at the indicated time.
- ``"period(datetime_start, interval, datetime_end)"`` or
  ``"period(datetime_start, interval)"`` triggers every interval starting at the starting datetime
  and finishing at the optional ending datetime. When there is no ending datetime, the periodic
  trigger runs forever. The interval has the form ``number{sec|min|hours|days|weeks}`` (the same as
  datetime offset without the leading sign), and single-letter abbreviations can be used.
- ``"cron(min hr dom mon dow)"`` triggers
  according to Linux-style crontab. Each of the five entries are separated by spaces and correspond
  to minutes, hours, day-of-month, month, day-of-week (0 = sunday):

  ============ ==============
  field        allowed values
  ============ ==============
  minute       0-59
  hour         0-23
  day of month 1-31
  month        1-12
  day of week  0-6 (0 is Sun)
  ============ ==============

  Each field can be a ``*`` (which means “all”), a single number, a range or comma-separated list of
  numbers or ranges (no spaces). Ranges are inclusive. For example, if you specify hours as
  ``6,10-13`` that means hours of 6,10,11,12,13. The trigger happens on the next minute, hour, day
  that matches the specification. See any Linux documentation for examples and more details (note:
  names for days of week and months are not supported; only their integer values are).

When the ``@time_trigger`` occurs and the function is called, the keyword argument ``trigger_type``
is set to ``"time"``, and ``trigger_time`` is the exact ``datetime`` of the time specification that
caused the trigger (it will be slightly before the current time), or ``None`` in the case of a
``startup`` trigger.

A final special form of ``@time_trigger`` has no arguments, which causes the function to run once
automatically on startup or reload, which is the same as providing a single ``"startup"`` time
specification:

.. code:: python

   @time_trigger
   def run_on_startup_or_reload():
       """This function runs automatically once on startup or reload"""
       pass

The function is not re-started after it returns, unless a reload occurs. Startup occurs when the
``EVENT_HOMEASSISTANT_STARTED`` event is fired, which is after everything else is initialized and
ready, so this function can call any services etc.

@event_trigger
^^^^^^^^^^^^^^

.. code:: python

    @event_trigger(event_type, str_expr=None)

``@event_trigger`` triggers on the given ``event_type``. An optional ``str_expr`` can be used to
match the event data, and the trigger will only occur if that expression evaluates to ``True`` or
non-zero. This expression has available all the event parameters sent with the event, together with
these two variables:

- ``trigger_type`` is set to “event”
- ``event_type`` is the string event type, which will be the same as the
  first argument to ``@event_trigger``

Note unlike state variables, the event data values are not forced to be strings, so typically that
data has its native type.

When the ``@event_trigger`` occurs, those same variables are passed as keyword arguments to the
function in case it needs them.

The ``event_type`` could be a user-defined string, or it could be one of the built-in events. You
can access the names of those events by importing from ``homeassistant.const``, eg:

.. code:: python

   from homeassistant.const import EVENT_CALL_SERVICE

To figure out what parameters are sent with an event and what objects (eg: ``list``, ``dict``) are
used to represent them, you can look at the HASS source code, or initially use the ``**kwargs``
argument to capture all the parameters and log them. For example, you might want to trigger on
certain service calls (not ones directed to pyscript), but you are unsure which one and what
parameters it has. So initially you trigger on all service calls just to see them:

.. code:: python

   from homeassistant.const import EVENT_CALL_SERVICE

   @event_trigger(EVENT_CALL_SERVICE)
   def monitor_service_calls(**kwargs):
       log.info(f"got EVENT_CALL_SERVICE with kwargs={kwargs}")

After running that, you see that you are interested in the service call ``lights.turn_on``, and you
see that the ``EVENT_CALL_SERVICE`` event has parameters ``domain`` set to ``lights`` and
``service`` set to ``turn_on``, and the service parameters are passed as a ``dict`` in
``service_data``. So then you can narrow down the event trigger to that particular service call:

.. code:: python

   from homeassistant.const import EVENT_CALL_SERVICE

   @event_trigger(EVENT_CALL_SERVICE, "domain == 'lights' and service == 'turn_on'")
   def monitor_light_turn_on_service(service_data=None):
       log.info(f"lights.turn_on service called with service_data={service_data}")

This `wiki page <https://github.com/custom-components/pyscript/wiki/Event-based-triggers>`__ gives
more examples of built-in and user events and how to create triggers for them.

@task_unique
^^^^^^^^^^^^

.. code:: python

    @task_unique(task_name, kill_me=False)

This decorator is equivalent to calling ``task.unique()`` at the start of the function when that
function is triggered. Like all the decorators, if the function is called directly from another
Python function, this decorator has no effect. See `this section <#task-unique>`__ for more
details.

@state_active
^^^^^^^^^^^^^

.. code:: python

    @state_active(str_expr)

When any trigger occurs (whether time, state or event), the ``@state_active`` expression is
evaluated. If it evaluates to ``False`` (or zero), the trigger is ignored and the trigger function
is not called.

If the trigger was caused by ``@state_trigger``, the prior value of the state variable that
caused the trigger is available to ``str_expr`` with a ``.old`` suffix.

Inside the ``str_expr``, undefined state variables, undefined state attributes, and undefined
``.old`` variables evaluate to ``None``, rather than throwing an exception. Any ``.old`` variable
will be ``None`` if the trigger is not a state trigger, if a different state variable change
caused the state trigger, or if the state variable that caused the trigger was set for the
first time (so there is no prior value).

@time_active
^^^^^^^^^^^^

.. code:: python

    @time_active(time_spec, ...)

``@time_active`` takes one or more strings that specify time-based ranges. When any trigger occurs
(whether time, state or event), each time range specification is checked. If the current time
doesn’t fall within any range specified, the trigger is ignored and the trigger function is not
called.

Each string specification ``time_spec`` can take two forms:

- ``"range(datetime_start, datetime_end)"`` is satisfied if the current
  time is in the indicated range, including the end points. As in ``@time_trigger``, the year or
  date can be omitted to specify daily ranges. If the end is prior to the start, the range is
  satisfied if the current time is either greater than or equal to the start or less than or equal
  to the end. That allows a range like: ``@time_active("range(sunset - 20min, sunrise + 15min)")``
  to mean at least 20 minutes before sunset, or at least 15 minutes after sunrise (note: at
  latitudes close to the polar circles, there can be cases where the sunset time is after midnight,
  so it is before the sunrise time, so this might not work correctly; at even greater latitudes
  sunset and sunrise will not be defined at all since there might not be daily sunrises or sunsets).
- ``"cron(min hr dom mon dow)"`` is satisfied if the current time matches
  the range specified by the ``cron`` parameters. For example, if ``hr`` is ``6-10`` that means
  hours between 6 and 10 inclusive. If additionally ``min`` is ``*`` (i.e., any), then that would
  mean a time interval from 6:00 to immediately prior to 11:00.

Each argument specification can optionally start with ``not``, which inverts the meaning of that
range or cron specification. If you specify multiple arguments without ‘not’, they are logically
or’ed together, meaning the active check is true if any of the (positive) time ranges are met. If
you have several ``not`` arguments, they are logically and’ed together, so the active check will be
true if the current time doesn’t match any of the “not” (negative) specifications. ``@time_active``
allows multiple arguments with and without ``not``. The condition will be met if the current time
matches any of the positive arguments, and none of the negative arguments.

@service
^^^^^^^^

The ``@service`` decorator causes the function to be registered as a service so it can be called
externally. The ``@state_active`` and ``@time_active`` decorators don’t affect the service - those
only apply to time, state and event triggers specified by other decorators.

The function is called with keyword parameters set to the service call parameters, plus
``trigger_type`` is set to ``"service"``.

The ``doc_string`` (the string immediately after the function declaration) is used as the service
description that appears is in the Services tab of the Developer Tools page. The function argument
names are used as the service parameter names, but there is no description.

Alternatively, if the ``doc_string`` starts with ``yaml``, the rest of the string is used as a
``yaml`` service description. Here’s the first example above, with a more detailed ``doc_string``:

.. code:: python

   @service
   def hello_world(action=None, id=None):
       """yaml
   description: hello_world service example using pyscript.
   fields:
     action:
        description: turn_on turns on the light, fire fires an event
        example: turn_on
     id:
        description: id of light, or name of event to fire
        example: kitchen.light
   """
       log.info(f"hello world: got action {action}")
       if action == "turn_on" and id is not None:
           light.turn_on(entity_id=id, brightness=255)
       elif action == "fire" and id is not None:
           event.fire(id)

Functions
---------

Most of these have been mentioned already, but here is the complete list of additional functions
made available by ``pyscript``.

Note that even though the function names contain a period, the left portion is not a class (e.g.,
``state`` is not a class, and in fact isn’t even defined). These are simply functions whose name
includes a period. This is one aspect where the interpreter behaves slightly differently from real
Python.

However, if you set a variable like ``state``, ``log`` or ``task`` to some value, then the functions
defined with that prefix will no longer be available, since the portion after the period will now be
interpreted as a method or class function acting on that variable. That's the same behavior as
Python - for example if you set ``bytes`` to some value, then the ``bytes.fromhex()`` class method
is no longer available in the current scope.

State variables
^^^^^^^^^^^^^^^

State variables can be used and set just by using them as normal Python variables. However, there
could be cases where you want to dynamically generate the variable name (eg, in a function or loop
where the state variable name is computed dynamically). These functions allow you to get and set a
variable using its string name. The set function also allows you to optionally set the attributes,
which you can’t do if you are directly assigning to the variable:

``state.get(name)``
  Returns the value of the state variable given its string ``name``. A ``NameError`` exception
  is thrown if the name doesn't exist. If ``name`` is a string of the form ``DOMAIN.entity.attr``
  then the attribute ``attr`` of the state variable ``DOMAIN.entity`` is returned; an
  ``AttributeError`` exception is thrown if that attribute doesn't exist.
``state.get_attr(name)``
  Returns a ``dict`` of attribute values for the state variable, or ``None``
  if it doesn’t exist
``state.names(domain=None)``
  Returns a list of all state variable names (ie, ``entity_id``\ s) of a
  domain. If ``domain`` is not specified, it returns all HASS state variable (``entity_id``) names.
``state.set(name, value, new_attributes=None, **kwargs)``
  Sets the state variable to the given value, with the optional attributes. The optional 3rd
  argument, ``new_attributes``, should be a ``dict`` and it will overwrite all the existing
  attributes if specified. If instead attributes are specified using keyword arguments, then other
  attributes will not be affected. If no optional arguments are provided, just the state variable
  value is set and the attributes are not changed. To clear the attributes, set
  ``new_attributes={}``.

Note that in HASS, all state variable values are coerced into strings. For example, if a state
variable has a numeric value, you might want to convert it to a numeric type (eg, using ``int()`` or
``float()``). Attributes keep their native type.

Persistent State
^^^^^^^^^^^^^^^^

This method is provided to indicate that a particular entity_id should be persisted. This is only effective for entitys in the `pyscript` domain.

``state.persist(entity_id, default_value=None)``
  Indicates that the entity named in `entity_id` should be persisted. Optionally, a default value can be provided.



Service Calls
^^^^^^^^^^^^^

``service.call(domain, name, **kwargs)``
  calls the service ``domain.name`` with the given keyword arguments as parameters.
``service.has_service(domain, name)``
  returns whether the service ``domain.name`` exists.

Event Firing
^^^^^^^^^^^^

``event.fire(event_type, **kwargs)``
  sends an event with the given ``event_type`` string and the keyword parameters as the event data.

Logging
^^^^^^^

Five logging functions are provided, with increasing levels of severity:

``log.debug(str)``
  log a message at debug level
``log.info(str)``
  log a message at info level
``log.warning(str)``
  log a message at warning level
``log.error(str)``
  log a message at error level
``print(str)``
  same as ``log.debug(str)``; currently ``print`` doesn’t support other arguments.

The ``Logger`` component can be used to specify the logging level. Log messages below the configured
level will not appear in the log. Each log message function uses a log name of the form:

.. code:: yaml

   custom_components.pyscript.file.FILENAME.FUNCNAME

where ``FUNCNAME`` is the name of the top-level Python function (e.g., the one called by a trigger
or service), defined in the script file ``FILENAME.py``. See the XXXX

That allows you to set the log level for each Python top-level function separately if necessary.
That setting also applies to any other Python functions that the top-level Python function calls.
For example, these settings:

.. code:: yaml

   logger:
     default: info
     logs:
       custom_components.pyscript.file: info
       custom_components.pyscript.file.my_scripts.my_function: debug

will log all messages at ``info`` or higher (ie: ``log.info()``, ``log.warning()`` and
``log.error()``), and inside ``my_function`` defined in the script file ``my_scripts.py`` (and any
other functions it calls) will log all messages at ``debug`` or higher.

Note that in Jupyter, all the ``log`` functions will display output in your session, independent of
the ``logger`` configuration settings.

Task sleep
^^^^^^^^^^

``task.sleep(seconds)``
  sleeps for the indicated number of seconds, which can be floating point. Do not import ``time``
  and use ``time.sleep()`` - that will block lots of other activity.

Task unique
^^^^^^^^^^^

``task.unique(task_name, kill_me=False)``
  kills any currently running triggered function that previously called ``task.unique`` with the
  same ``task_name``. The name can be any string. If ``kill_me=True`` then the current task is
  killed if another task that is running previously called ``task.unique`` with the same
  ``task_name``.

Note that ``task.unique`` is specific to the current global context, so names used in one
global context will not affect another.

``task.unique`` can also be called outside a function, for example in the preamble of a script file
or interactively using Jupyter. That causes any currently running functions (ie, functions that have
already been triggered and are running Python code) that previously called ``task.unique`` with the
same name to be terminated. Since any currently running functions are not terminated on reload, this
is the mechanism you can use should you wish to terminate specific functions on reload. If used
outside a function or interactively with Jupyter, calling ``task.unique`` with ``kill_me=True``
causes ``task.unique`` to do nothing.

The ``task.unique`` functionality is also provided via a decorator ``@task_unique``. If your
function immediately and always calls ``task.unique``, you could choose instead to use the
function decorator form.

Task waiting
^^^^^^^^^^^^

``task.wait_until()``
  allows functions to wait for events, using identical syntax to the decorators. This can be
  helpful if at some point during execution of some logic you want to wait for some additional
  triggers.

It takes the following keyword arguments (all are optional):

- ``state_trigger=None`` can be set to a string just like ``@state_trigger``, or it can be
  a list of strings that are logically "or"ed together.
- ``time_trigger=None`` can be set to a string or list of strings with
  datetime specifications, just like ``@time_trigger``.
- ``event_trigger=None`` can be set to a string or list of two strings, just like
  ``@event_trigger``. The first string is the name of the event, and the second string
  (when the setting is a two-element list) is an expression based on the event parameters.
- ``timeout=None`` an overall timeout in seconds, which can be floating point.
- ``state_check_now=True`` if set, ``task.wait_until()`` checks any ``state_trigger``
  immediately to see if it is already ``True``, and will return immediately if so. If
  ``state_check_now=False``, ``task.wait_until()`` waits until a state variable change occurs,
  before checking the expression. Using ``True`` is safer to help avoid race conditions, although
  ``False`` makes ``task.wait_until()`` behave like ``@state_trigger``, which doesn’t check at
  startup. However, if you use the default of ``True``, and your function will call
  ``task.wait_until()`` again, it’s recommended you set that state variable to some other value
  immediately after ``task.wait_until()`` returns. Otherwise the next call will also return
  immediately.

When a trigger occurs, the return value is a ``dict`` containing the same keyword values that are
passed into the function when the corresponding decorator trigger occurs. There will always be a key
``trigger_type`` that will be set to:

- ``"state"``, ``"time"`` or ``"event"`` when each of those triggers occur.
- ``"timeout"`` if there is a timeout after ``timeout`` seconds (the ``dict`` has no other values)
- ``"none"`` if you specify only ``time_trigger`` and no ``timeout``, and there is no future next
  time that satisfies the trigger condition (e.g., a ``range`` or ``once`` is now in the past).
  Otherwise, ``task.wait_until()`` would never return.

In the special case that ``state_check_now=True`` and ``task.wait_until()`` returns immediately, the
other return variables that capture the variable name and value that just caused the trigger are not
included in the ``dict`` - it will just contain ``trigger_type="state"``.

Here’s an example. Whenever a door is opened, we want to do something if the door closes within 30
seconds. If a timeout of more than 30 seconds elapses (ie, the door is still open), we want to do
some other action. We use a decorator trigger when the door is opened, and we use
``task.wait_until`` to wait for either the door to close, or a timeout of 30 seconds to elapse. The
return value tells which of the two events happened:

.. code:: python

   @state_trigger("security.rear_door == 'open'")
   def rear_door_open_too_long():
       """send alert if door is open for more than 30 seconds"""
       trig_info = task.wait_until(
                       state_trigger="security.rear_door == 'closed'",
                       timeout=30
                   )
       if trig_info["trigger_type"] == "timeout":
           # 30 seconds elapsed without the door closing; do some actions
           pass
       else:
           # the door closed within 30 seconds; do some other actions
           pass

``task.wait_until()`` is logically equivalent to using the corresponding decorators, with some
important differences. Consider these two alternatives, which each run some code whenever there is
an event ``test_event3`` with parameters ``args == 20`` and ``arg2 == 30``:

.. code:: python

   @event_trigger("test_event3", "arg1 == 20 and arg2 == 30")
   def process_test_event3(**trig_info):
       # do some things, including waiting a while
       task.sleep(5)
       # do some more things

versus:

.. code:: python

   @time_trigger    # empty @time_trigger means run the function on startup
   def wait_for_then_process_test_event3():
       while 1:
           trig_info = task.wait_until(
                           event_trigger=["test_event3", "arg1 == 20 and arg2 == 30"]
                       )
           # do some things, including waiting a while
           task.sleep(5)
           # do some more things

Logically they are the similar, but the important differences are:

- ``task.wait_until()`` only looks for the trigger conditions when it is called, and it stops
  monitoring them as soon as it returns. That means the trigger (especially an event trigger) could
  occur before or after ``task.wait_until()`` is called, and you will miss the event. In contrast,
  the decorator triggers monitor the trigger conditions continuously, so they will not miss state
  changes or events once they are initialized. The reason for the ``state_check_now`` argument, and
  its default value of ``True`` is to help avoid this race condition for state triggers. Time
  triggers should generally be safe.

- The decorators run each trigger function as a new independent task, and don’t wait for it to
  finish. So a function will be run for every matching event. In contrast, if your code runs for a
  while before calling ``task.wait_until()`` again (e.g., ``task.sleep()`` or any code), or even if
  there is no other code in the ``while`` loop, some events or state changes of interest will be
  potentially missed.

Summary: use trigger decorators whenever you can. Be especially cautious using ``task.wait_until()``
to wait for events; you must make sure your logic is robust to missing events that happen before or
after ``task.wait_until()`` runs.

Task executor
^^^^^^^^^^^^^

If you call any Python functions that do I/O or otherwise block, they need to be run outside the
main event loop using ``task.executor``:

``task.executor(func, *args, **kwargs)``
  Run the given function in a separate thread. The first argument is the function to be called,
  followed by each of the positional or keyword arguments that function expects. The ``func``
  argument can only be a regular Python function, not a function defined in pyscript.

See `this section <#avoiding-event-loop-i-o>`__ for more information.

Global Context
^^^^^^^^^^^^^^

Each pyscript file that is loaded, and each Jupyter session, runs inside its own global context,
which means its global variables and functions are isolated from each other (unless they are a
module or package that is explicitly imported). In normal use you don’t need to worry about global
contexts. But for interactive debugging and development, you might want your Jupyter session to
access variables and functions defined in a script file.

Here is the naming convention for each file's global context:

  ======================================= ===========================
  pyscript file path                      global context name
  ======================================= ===========================
  ``pyscript/FILE.py``                    ``file.FILE``
  ``pyscript/modules/MODULE.py``          ``modules.MODULE``
  ``pyscript/modules/MODULE/__init__.py`` ``modules.MODULE.__init__``
  ``pyscript/modules/MODULE/FILE.py``     ``modules.MODULE.FILE``
  ``pyscript/apps/APP.py``                ``apps.APP``
  ``pyscript/apps/APP/__init__.py``       ``apps.APP.__init__``
  ``pyscript/apps/APP/FILE.py``           ``apps.APP.FILE``
  ======================================= ===========================

The logging path uses the global context name, so you can customize logging verbosity for each
global context, to the granularity of specific functions eg:

.. code:: yaml

   logger:
     default: info
     logs:
       custom_components.pyscript.file: info
       custom_components.pyscript.file.my_scripts.my_function: debug
       custom_components.pyscript.apps.my_app: debug
       custom_components.pyscript.apps.my_app.my_function: debug

Each Jupyter global context name is ``jupyter_NNN`` where ``NNN`` is a unique integer starting at 0.

On reload, all global contexts whose names starts with ``file.``, ``modules.`` or ``apps.`` are
removed. As each file is reloaded, the corresponding global context is created.

Three functions are provided for getting, setting and listing the global contexts. That allows
you to interactively change the global context during a Jupyter session. You could also use these
functions in your script files, but that is strongly discouraged because it violates the name
space isolation among the script files. Here are the functions:

``pyscript.get_global_ctx()``
  returns the current global context name.
``pyscript.list_global_ctx()``
  lists all the global contexts, with the current global context listed first.
``pyscript.set_global_ctx(new_ctx_name)``
  sets the current global context to the given name.

When you exit a Jupyter session, its global context is deleted, which means any triggers, functions,
services and variables you created are deleted (HASS state variables survive). If you switch to a
script file’s context, then any triggers, functions, services or variables you interactively create
there will persist after you exit the Jupyter session. However, if you don’t update the
corresponding script file, then upon the next pyscript reload or HASS restart, those interactive
changes will be lost, since reloading a script file recreates a new global context.

Advanced Topics
---------------

Workflow
^^^^^^^^

Without Jupyter, the pyscript workflow involves editing scripts in the ``<config>/pyscript`` folder,
and calling the ``pyscript.reload`` service to reload the code. You will need to look at the log
file for error messages (eg, syntax errors), or log output from your code.

A much better alternative is to use Jupyter notebook to interactively deveop and test functions,
triggers and services.

Jupyter auto-completion (with `<TAB>`) is supported in Jupyter notebook, console and lab. It should
work after you have typed at least the first character. After you hit `<TAB>` you should see a list
of potential completions from which you can select. It's a great way to easily see available state
variables, functions or services.

In a Jupyter session, one or more functions can be defined in each code cell. Every time that cell
is executed (eg, `<Shift>Return`), those functions are redefined, and any existing trigger
decorators with the same function name are canceled and replaced by the new definition. You might
have other function and trigger definitions in another cell - they won't be affected (assuming those
function names are different), and they will only be replaced when you re-execute that other cell.

When the Jupyter session is terminated, its global context is deleted, which means any trigger
rules, functions, services and variables you created are deleted. The pyscript Jupyter kernel is
intended as an interactive sandbox. As you finalize specific functions, triggers and automation
logic, you should copy them to a pyscript script file, and then use the `pyscript.reload` service to
load them. That ensures they will be loaded and run each time you re-start HASS.

If a function you define has been triggered and is currently executing Python code, then re-running
the cell in which the function is defined, or exiting the Jupyter session, will not stop or cancel
the already running function. This is the same behavior as `reload`. In pyscript, each triggered
function (ie, a trigger has occurred, the trigger conditions are met, and the function is actually
executing Python code) runs as an independent task until it finishes. So if you are testing triggers
of a long-running function (eg, one that uses `task.sleep()` or `task.wait_until()`) you could end
up with many running instances. It's strongly recommended that you use `task.unique()` to make sure
old running function tasks are terminated when a new one is triggered. Then you can manually call
`task.unique()` to terminate that last running function before exiting the Jupyter session.

If you switch global contexts to a script file's context, and create some new variables, triggers,
functions or services there, then those objects will survive the termination of your Jupyter
session. However, if you `reload` the scripts, then those newly-created objects will be removed.
To make any additions or changes permanent (meaning they will be re-created on each `reload` or each
time your restart HASS) then you should copy the changes or additions to one of your pyscript script
files.

Importing
^^^^^^^^^

Pyscript supports importing two types of packages or modules:

- Pyscript code can be put into modules or packages and stored in the ``<config>/pyscript/modules`` folder.
  Any pyscript code can import and use these modules or packages. These modules are not autoloaded
  on startup; they are only loaded when another script imports them. When you call the pyscript
  reload service, all imported modules are unloaded. Imports of pyscript modules and packages
  are not affected by the ``allow_all_imports`` setting - if a file is in the ``<config>/pyscript/modules``
  folder then it can be imported.
  
  Package-style layout is also supported where a PACKAGE is defined in
  ``<config>/pyscript/modules/PACKAGE/__init__.py``, and that file can, in turn,
  do relative imports of other files in that same directory. This form is most convenient for
  sharing useful pyscript libraries, since all the files for one package are stored in its own
  directory.

- Installed Python packages can be imported. By default, pyscript only allows a short list of Python
  packages to be imported, for both security reasons and to reduce the risk that package functions
  that block doing I/O are called.

The rest of this section discusses the second style - importing installed Python modules and packages.

If you set the ``allow_all_imports`` configuration parameter, any available Python package can be
imported. You should be cautious about setting this if you are going to install community pyscript
code without inspecting it, since it could, for example, ``import os`` and call ``os.remove()``.
However, if you are developing your own code then there is no issue with enabling all imports.

Pyscript code is run using an asynchronous interpreter, which allows it to run in the HASS main
event loop. That allows many of the "magic" features to be implemented without the user having to
worry about the details. However, the performance will be much slower that regular Python code,
which is typically compiled. Any Python packages you import will run at native, compiled speed.

So if you plan to run large chunks of code in pyscript without needing any of the pypscript-specific
features, you might consider putting them in a package and importing it instead. That way it will
run at native compiled speed.

One way to do that is in one of your pyscript script files, add this code:

.. code:: python

    import sys

    if "config/pyscript_module" not in sys.path:
        sys.path.append("config/pyscript_modules")

This adds a new folder ``config/pyscript_modules`` to Python's module search path. You can then add
modules (files ending in ``.py``) to that folder, which will contain native python that is compiled
when imported (note that none of the pyscript-specific features are available).

Trigger Closures
^^^^^^^^^^^^^^^^

Pyscript supports trigger functions that are defined as closures, ie: functions defined inside
another function. This allows you to easily create many similar trigger functions that might
differ only in a couple of parameters (eg, a common function in different rooms or for each
media setup). The trigger will be stopped when the function is no longer referenced in
any scope. Typically the closure function is returned, and the return value is assigned
to a variable. If that variable is re-assigned or deleted, the trigger function will be
destroyed.

Here's an example:

.. code:: python

        def state_trigger_factory(sensor_name, trig_value):

            @state_trigger(f"input_boolean.{sensor_name} == '{trig_value}'")
            def func_trig(value=None):
                log.info(f"func_trig: {sensor_name} is {value}")

            return func_trig

        f1 = state_trigger_factory("test1", "on")
        f2 = state_trigger_factory("test2", "on")
        f3 = state_trigger_factory("test3", "on")

This creates three trigger functions that fire when the given sensor ``input_boolean.testN`` is
``on``. If you re-assign or delete ``f1`` then that trigger will be destroyed, and the other two
will not be affected. If you repeatedly re-run this block of code in Jupyter the right thing will
happen - each time it runs the old triggers are destroyed when the variables are re-assigned.

Any data type could be used to maintain a reference to the trigger function. For example
a list could be manually built:

.. code:: python

    input_boolean_test_triggers = [
        state_trigger_factory("test1", "on"),
        state_trigger_factory("test2", "on"),
        state_trigger_factory("test3", "on")
    ]

or dynamically in a loop:

.. code:: python

    input_boolean_test_triggers = []
    for i in range(1, 4):
        input_boolean_test_triggers.append(state_trigger_factory(f"test{i}", "on"))

If you are writing a factory function and you prefer the caller not to bother with
maintaining variables with the closure functions, you could move the appending into
the function and use a global variable (a class could also be used):

.. code:: python

        input_boolean_test_triggers = []

        def state_trigger_factory(sensor_name, trig_value):

            @state_trigger(f"input_boolean.{sensor_name} == '{trig_value}'")
            def func_trig(value=None):
                log.info(f"func_trig: {sensor_name} is {value}")

            input_boolean_test_triggers.append(func_trig)

        state_trigger_factory("test1", "on")
        state_trigger_factory("test2", "on")
        state_trigger_factory("test3", "on")

Notice there is no return value from the factory function.

A ``dict`` could be used instead of a list, with a key that combines the unique parameters
of the trigger. That way a new trigger with the same parameters will replace an old one
when the ``dict`` entry is set, if that's the behavior you want.

Accessing YAML configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Pyscript binds all of its ``yaml`` configuration to the variable ``pyscript.config``. That
allows you to add configuration settings that can be processed by your pyscript code.

One motivation is to allow pyscript apps to be developed and shared that can instantiate triggers
and logic based on ``yaml`` configuration. That allows other users to use and configure your
pyscript code without needing to edit or even understand it - they just need to add the
corresponding ``yaml`` configuration.

A recommended convention is to put the settings for a pyscript application called ``auto_lights``
below an entry ``apps``. That entry could contain a list of settings (eg, for handling multiple
rooms or locations).

Here's an example ``yaml`` configuration with settings for two applications, ``auto_lights``
and ``motion_light``:

.. code:: yaml

   pyscript:
     allow_all_imports: true
     apps:
       auto_lights:
         - room: living
           level: 60
           some_list:
            - 1
            - 20
         - room: dining
           level: 80
           some_list:
            - 1
            - 20
       motion_light:
         - sensor: rear_left
           light: rear_flood
         - sensor: side_yard
           light: side_flood
         - sensor: front_patio
           light: front_porch

The corresponding ``pyscript.config`` variable value will be:

.. code:: python

   {
       "allow_all_imports": True,
       "apps": {
           "auto_lights": [
               {"room": "living", "level": 60, "some_list": [1, 20]},
               {"room": "dining", "level": 80, "some_list": [1, 20]},
           ],
           "motion_light": [
               {"sensor": "rear_left", "light": "rear_flood"},
               {"sensor": "side_yard", "light": "side_flood"},
               {"sensor": "front_patio", "light": "front_porch"},
           ],
       },
   }

Your application code for ``auto_lights`` would be in either

- ``<config>/pyscript/apps/auto_lights.py``
- ``<config>/pyscript/apps/auto_lights/__init__.py``

It can simply iterate over ``pyscript.config["apps"]["auto_lights"]`` settings up the necessary
triggers and application logic, eg:

.. code:: python

   def setup_triggers(room=None, level=None, some_list=None):
       #
       # define some trigger functions etc
       #
       pass

   for inst in pyscript.config["apps"]["auto_lights"]:
       setup_triggers(**inst)

Validating the configuration can be done either manually or with the ``voluptuous`` package.

Access to Hass
^^^^^^^^^^^^^^

If the ``hass_is_global`` configuration setting is set (default is off), then the variable ``hass``
is available as a global variable in all pyscript contexts. That provides significant flexiblity
in accessing HASS internals for cases where pyscript doesn't provide some binding or access.

Ideally you should only use ``hass`` for read-only access. However, you do need a good understanding
of ``hass`` internals and objects if you try to call functions or update anything. With great power
comes great responsibility!

For example, you can access configuration settings like ``hass.config.latitude`` or ``hass.config.time_zone``.

You can use ``hass`` to compute sunrise and sunset times using the same method HASS does, eg:

.. code:: python

   import homeassistant.helpers.sun as sun
   import datetime

   location = sun.get_astral_location(hass)
   sunrise = location.sunrise(datetime.datetime.today()).replace(tzinfo=None)
   sunset = location.sunset(datetime.datetime.today()).replace(tzinfo=None)
   print(f"today sunrise = {sunrise}, sunset = {sunset}")

Here's another method that uses the installed version of ``astral`` directly, rather than the HASS
helper function.  It's a bit more crytpic since it's a very old version of ``astral``, but you can
see how the HASS configuration values are used:

.. code:: python

   import astral
   import datetime

   here = astral.Location(
       (
           "",
           "",
           hass.config.latitude,
           hass.config.longitude,
           str(hass.config.time_zone),
           hass.config.elevation,
       )
   )
   sunrise = here.sunrise(datetime.datetime.today()).replace(tzinfo=None)
   sunset = here.sunset(datetime.datetime.today()).replace(tzinfo=None)
   print(f"today sunrise = {sunrise}, sunset = {sunset}")

If there are particular HASS internals that you think many pyscript users would find useful,
consider making a feature request or PR so it becomes a built-in feature in pyscript, rather
than requiring users to always have to delve into ``hass``.

Avoiding Event Loop I/O
^^^^^^^^^^^^^^^^^^^^^^^

All pyscript code runs in the HASS main event loop. That means if you execute code that blocks, for
example doing I/O like reading or writing files or fetching a URL, then the main loop in HASS will
be blocked, which will delay all other tasks.

All the built-in functionality in pyscript is written using asynchronous code, which runs seamlessly
together with all the other tasks in the main event loop. However, if you import Python packages and
call functions that block (eg, file or networrk I/O) then you need to run those functions outside
the main event loop. That can be accomplished wrapping those function calls with the
``task.executor`` function, which runs the function in a separate thread:

``task.executor(func, *args, **kwargs)``
  Run the given function in a separate thread. The first argument is the function to be called,
  followed by each of the positional or keyword arguments that function expects. The ``func``
  argument can only be a regular Python function, not a function defined in pyscript.

If you forget to use ``task.executor``, you might get this warning from HASS:

::

    WARNING (MainThread) [homeassistant.util.async_] Detected I/O inside the event loop. This is
    causing stability issues. Please report issue to the custom component author for pyscript doing
    I/O at custom_components/pyscript/eval.py, line 1583: return func(*args, **kwargs)

Here's an example fetching a URL. Inside pyscript, this is the wrong way since it does I/O without
using a separate thread:

.. code:: python

    import requests

    url = "https://raw.githubusercontent.com/custom-components/pyscript/master/README.md"
    resp = requests.get(url)

The correct way is:

.. code:: python

    import requests

    url = "https://raw.githubusercontent.com/custom-components/pyscript/master/README.md"
    resp = task.executor(requests.get, url)

An even better solution to fetch a URL is to use a Python package that uses asyncio, in which case
there is no need for ``task.executor``. In this case, ``aiohttp`` can be used (the ``await`` keyword
is optional in pyscript):

.. code:: python

    import aiohttp

    url = "https://raw.githubusercontent.com/custom-components/pyscript/master/README.md"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            print(resp.status)
            print(resp.text())

Persistent State
^^^^^^^^^^^^^^^^

Pyscript has the ability to persist state in the `pyscript.` domain. This means that setting an entity like `pyscript.test` will cause it to be restored to its previous state when Home Assistant is restarted.

This can be done in any of the usual ways to set the state of an `entity_id`:

.. code:: python

   set.state('pyscript.test', 'on')

   pyscript.test = 'on'

Attributes can be included:

.. code:: python

   set.state('pyscript.test', 'on', friendly_name="Test", device_class="motion")

   pyscript.test = 'on'
   pyscript.test.friendly_name = 'Test'
   pyscript.test.device_class = 'motion'

In order to ensure that the state of a particular entity persists, you need to request persistence explicitly. This must be done in a code location that will be certain to run at startup. Generally, this means outside of trigger functions.


.. code:: python

   state.persist('pyscript.last_light_on')

   @state_trigger('binary_sensor.motion == "on"')
   def turn_on_lights():
     light.turn_on('light.overhead')
     pyscript.last_light_on = "light.overhead"

With this in place, `state.persist()` will be called every time this script is parsed, ensuring this particular state will persist. 


   

  
  