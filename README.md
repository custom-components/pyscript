# Pyscript: Python Scripting for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/custom-components/pyscript.svg?style=for-the-badge)](https://github.com/custom-components/pyscript/releases)
[![License](https://img.shields.io/github/license/custom-components/pyscript.svg?style=for-the-badge)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![Project Maintenance](https://img.shields.io/badge/maintainer-%40craigbarratt-blue.svg?style=for-the-badge)](https://github.com/craigbarratt)

This HACS custom integration allows you to write Python functions and scripts that can implement a
wide range of automation, logic and triggers. State variables are bound to Python variables and
services are callable as Python functions, so it's easy and concise to implement logic.

Functions you write can be configured to be called as a service or run upon time, state-change or
event triggers. Functions can also call any service, fire events and set state variables.
Functions can sleep or wait for additional changes in state variables or events, without slowing or
affecting other operations. You can think of these functions as small programs that run in
parallel, independently of each other, and they could be active for extended periods of time.

State, event and time triggers are specified by Python function decorators (the "@" lines
immediately before each function definition). A state trigger can be any Python expression using
state variables - the trigger is evaluated only when a state variable it references changes, and the
trigger occurs when the expression is true or non-zero. A time trigger could be a single event (eg:
date and time), a repetitive event (eg: at a particular time each day or weekday, daily relative
to sunrise or sunset or any regular time period within an optional range) or using cron syntax
(where events occur periodically based on a concise specification of ranges of minutes, hours, days
of week, days of month and months). An event trigger specifies the event type, and an optional
Python trigger test based on the event data that runs the Python function if true.

Pyscript implements a Python interpreter using the ast parser output, in a fully async manner. That
allows several of the "magic" features to be implemented in a seamless Pythonic manner, such as
binding of variables to states and functions to services. Pyscript supports imports, although the
valid import list is restricted for security reasons. Pyscript does not (yet) support some language
features like declaring new objects, `try/except`, generators and some syntax like `with` and
`yield`. Pyscript provides a handful of additional built-in functions that connect to HASS
features, like logging, accessing state variables as strings (if you need to compute their names
dynamically), sleeping and waiting for triggers.

Pyscript also provides a kernel that interfaces with the Jupyter front-ends (eg, notebook, console
and lab). That allows you to develop and test pyscript code interactively. Plus you can interact
with much of HASS by looking at state variables, calling services etc, in a similar way to [HASS
CLI](https://github.com/home-assistant-ecosystem/home-assistant-cli), althought the CLI provides
status on many other parts of HASS.

For more information about the Jupyter kernel, see the [README](https://github.com/custom-components/pyscript/tree/master/jupyter)
in the jupyter directory.
There is also a [Jupyter notebook tutorial](https://github.com/custom-components/pyscript/blob/master/jupyter/pyscript_tutorial.ipynb),
which can be downlaoded and run interactively in Jupyter notebook connected to your live HASS with pyscript.

Pyscript provides functionality that complements the existing automations, templates and triggers.
Pyscript is most similar to [AppDaemon](https://appdaemon.readthedocs.io/en/latest/), and
some similarities and differences are discussed in this
[Wiki page](https://github.com/custom-components/pyscript/wiki/Comparing-Pyscript-to-AppDaemon).
Pyscript with Jupyter makes it extremely easy to learn, use and debug. Pyscripts presents
a simplified and more integrated binding for Python scripting than
[Python Scripts](https://www.home-assistant.io/integrations/python_script), which requires
a lot more expertise and scaffolding using direct access to Home Assistant internals.

## Installation

### Option 1: HACS

Under HACS -> Integrations, select "+", search for `pyscript` and install it.

### Option 2: Manual

From the [latest release](https://github.com/custom-components/pyscript/releases) download the zip file `hass-custom-pyscript.zip`
```bash
cd YOUR_HASS_CONFIG_DIRECTORY    # same place as configuration.yaml
mkdir -p custom_components/pyscript
cd custom_components/pyscript
unzip hass-custom-pyscript.zip
```

Alternatively you can install the current GitHub master version by cloning and copying:
```bash
mkdir SOME_LOCAL_WORKSPACE
cd SOME_LOCAL_WORKSPACE
git clone https://github.com/custom-components/pyscript.git
mkdir -p YOUR_HASS_CONFIG_DIRECTORY/custom_components
cp -pr pyscript/custom_components/pyscript YOUR_HASS_CONFIG_DIRECTORY/custom_components
```

### Install Jupyter Kernel

Installing the pyscript Jupyter kernel is optional.  The steps to install and use it are in
this [README](https://github.com/custom-components/pyscript/tree/master/jupyter).

## Configuration

* Add `pyscript:` to `<config>/configuration.yaml`; pyscript doesn't have any configuration settings
* Create the folder `<config>/pyscript`
* Add files with a suffix of `.py` in the folder `<config>/pyscript`.
* Restart HASS.
* Whenever you change a script file, make a `reload` service call to `pyscript`.
* Watch the HASS log for `pyscript` errors and logger output from your scripts.

## Writing your first script

Create a file `example.py` in the `<config>/pyscript` folder (you can use any file name, so long as it ends in `.py`)
that contains:
```python
@service
def hello_world(action=None, id=None):
    """hello_world example using pyscript."""
    log.info(f"hello world: got action {action} id {id}")
    if action == "turn_on" and id is not None:
        light.turn_on(entity_id=id, brightness=255)
    elif action == "fire" and id is not None:
        event.fire(id, param1=12, pararm2=80)
```

After starting Home Assistant, use the Service tab in the Developer Tools page to call
the service `pyscript.hello_world` with parameters
```yaml
action: hello
id: world
```

The function decorator `@service` means `pyscript.hello_world` is registered as a service. The
expected service parameters are keyword arguments to the function. This function prints a log
message showing the `action` and `id` that the service was called with. Then, if the action is
`"turn_on"` and the `id` is specified, the `light.turn_on` service is called. Otherwise, if the
action is `"fire"` then an event type with that `id` is fired with the given parameters. You can
experiment by calling the service with different parameters. (Of course, it doesn't make much sense
to have a function that either does nothing, calls another service, or fires an event, but, hey,
this is just an example.)

<div class='note'>

You'll need to look at the log messages to see the output. The log message won't be visible
unless the [Logger](/integrations/logger/) is enabled at least for level `info`, for example:
```yaml
logger:
  default: info
  logs:
    homeassistant.components.pyscript: info
```
</div>

## An example using triggers

Here's another example:

```python
@state_trigger("security.rear_motion == '1' or security.side_motion == '1'")
@time_active("range(sunset - 20min, sunrise + 15min)")
def motion_light_rear():
    """Turn on rear light for 5 minutes when there is motion and it's dark"""
    log.info(f"triggered; turning on the light")
    light.turn_on(entity_id="light.outside_rear", brightness=255)
    task.sleep(300)
    light.turn_off(entity_id="light.outside_rear")
```
This introduces two new function decorators

- `@state_trigger` describes the condition(s) that trigger the function (the other two trigger types
are `@time_trigger` and `@event_trigger`, which we'll describe below). This condition is evaluated
each time the variables it refers to change, and if it evaluates to `True` or non-zero then the
trigger occurs.

- `@time_active` describes a time range that is checked whenever a potential trigger occurs. The
Python function is only executed if the `@time_active` criteria is met. In this example the time
range is from 20 minutes before sunset to 15 minutes after sunrise, ie: from dusk to dawn. Whenever
the trigger is `True` and the active conditions are met, the function is executed as a new task. The
trigger logic doesn't wait for the function to finish; it goes right back to checking for the next
condition. The function turns on the rear outside light, waits for 5 minutes, and then turns it
off.

However, this example has a problem. During those 5 minutes, any additional motion event will cause
another instance of the function to be executed. You might have dozens of them running, which is
perfectly ok for `pyscript`, but probably not the behavior you want, since as each earlier one
finishes the light will be turned off, which could be much less than 5 minutes after the most recent
motion event.

There is a special function provided to ensure just one function uniquely handles a task, if that's
the behavior you prefer. Here's the improved example:

```python
@state_trigger("security.rear_motion == '1' or security.side_motion == '1'")
@time_active("range(sunset - 20min, sunrise + 20min)")
def motion_light_rear():
    """Turn on rear light for 5 minutes when there is motion and it's dark"""
    task.unique("motion_light_rear")
    log.info(f"triggered; turning on the light")
    light.turn_on(entity_id="light.outside_rear", brightness=255)
    task.sleep(300)
    light.turn_off(entity_id="light.outside_rear")
```
The `task.unique` function will terminate any task that previously called `task.unique("motion_light_rear")`,
and our instance will survive. (The function takes a 2nd argument that causes the opposite to
happen: the older task survives and we are terminated - so long!)

As before, this example will turn on the light for 5 minutes, but when there is a new motion event,
the old function (which is part way through waiting for 5 minutes) is terminated, and we start
another 5 minute timer. The effect is the light will stay on for 5 minutes after the last motion
event, and stays on until there are no motion events for at least 5 minutes. If instead the second
argument to `task.unique` is set, that means the new task is terminated instead. The result is
that the light will go on for 5 minutes following a motion event, and any new motion events during
that time will be ignored, since each new triggered function will be terminated. Depending on your
application, either behavior might be preferred.

There are some other improvements we could make. We could check if the light is already on so we
don't have to turn it on again, by checking the relevant state variable:
```python
@state_trigger("security.rear_motion == '1' or security.side_motion == '1'")
@time_active("range(sunset - 20min, sunrise + 20min)")
def motion_light_rear():
    """Turn on rear light for 5 minutes when there is motion and it's dark"""
    task.unique("motion_light_rear")
    log.info(f"triggered; turning on the light")
    if light.outside_rear != "on":
        light.turn_on(entity_id="light.outside_rear", brightness=255)
    task.sleep(300)
    light.turn_off(entity_id="light.outside_rear")
```
You could also create another function that calls `task.unique("motion_light_rear")` if the light is
manually turned on (by doing a `@state_trigger` on the relevant state variable), so that the motion
logic is stopped when there is a manual event that you want to override the motion logic.

We've introduced some of the main features. Now for some more formal descriptions of the decorators
and the handful of extra built-in functions available.

## Pyscript configuration

`Pyscript` doesn't take any configuration variables. It looks in the `<config>/pyscript` folder
for Python files which are any files ending in `.py`. The `<config>/pyscript` folder can contain
as many `.py` script files as you like. Each `.py` file can contain as many functions as you want.
The file names themselves can be anything, so long as they have a `.py` extension. You might like
to group related functions in one file.

Like regular Python, functions within each script file can call each other, and can share global
variables (if necessary), but just within that one file. Each file has its own separate global
context. Each Jupyter session also has its own separate global context, so functions, triggers,
variables and services defined in each interactive session are isolated from the script files and
other Jupyter sessions. Pyscript provides some utility functions to switch global contexts, which
allows an interactive Jupyter session to interact directly with functions and global variables
created by a script file, or even another Jupyter session.

Even if you can't directly call one function from another script file, HASS state variables are
global and services can be called from any script file.

Reloading the `.py` files is accomplished by calling the `pyscript.reload` service, which is the one
built-in service (so you can't create your own service with that name). All function definitions,
services and triggers are re-created on `reload`. Any currently running functions (ie, functions
that have been triggered and are actively executing Python code or waiting inside `task.sleep()`
or `task.wait_until()`) are not stopped by `reload` - they continue to run until they finish
(return). You can terminate these running functions too on `reload` if you prefer by using
`task.unique()` in both the script file preamble and inside those functions.

## Accessing state variables

State variables can be accessed in any Python code simply by name. State variables (also called
`entity_id`) are of the form `DOMAIN.name`, where `DOMAIN` is typically the name of the component
that sets that variable. You can set a state variable by assigning to it.

State variables only have string values, so you will need to convert them to `int` or `float` if
you need their numeric value.

State variables have attributes that can be accessed by adding the name of the attribute, as in
`DOMAIN.name.attr`. The attribute names and their meaning depend on the component that sets
them, so you will need to look at the State tab in the Developer Tools to see the available
attributes.

In cases where you need to compute the name of the state variable dynamically, or you need to
set the state attributes, you can use the built-in functions `state.get(name)` and
`state.set(name, value, attr=None)`; see below.

Also, service names (which are called as functions) take priority over state variable names,
so if a component has a state variable name that collides with one of its services, you'll
need to use `state.get(name)` to access that state variable.

Finally, state variables that don't exist evaluate to `None`, rather than throwing an exception.
That makes it easy to test if a state variable has a valid value.

## Calling services

Any service can be called by using the service name as a function, with keyword parameters
to specify the service parameters. You'll need to look up the service in the Service
tab of Developer Tools to find the exact name and parameters. For example, inside
any function you can call:
```python
    myservice.flash_light(light_name="front", light_color="red")
```
which calls the `myservice.flash_light` service with the indicated parameters. Obviously
those parameter values could be any Python expression, and this call could be inside a
loop, an if statement or any other Python code.

The function `service.call(domain, name, **kwargs)` can also be used to call a service
when you need to compute the domain or service name dynamically. For example, the
above service could also be called by:
```python
    service.call("myservice", "flash_light", light_name="front", light_color="red")
```

## Firing events

Any event can be triggered by calling `event.fire(event_type, **kwargs)`. It takes the `event_type`
as a first argument, and any keyword parameters as the event parameters. The `event_type` could be
a user-defined string, or it could be one of the built-in events. You can access the names of those
built-in events by importing from `homeassistant.const`, eg:
```python
from homeassistant.const import EVENT_CALL_SERVICE
````
## Function decorators

There are three decorators for defining state, time and event triggers and two decorators for
defining whether any trigger actually causes the function to run (i.e., is active), based on
state-based expressions or one or more time-windows. The decorators should appear immediately
before the function they refer to. A single function can have any or all of the decorator types
specified, but at most one of each type.

A Python function with decorators is still a normal Python function that can be called by any other
Python function. The decorators have no effect in the case where you call it directly from
another function.

#### `@state_trigger(str_expr)`

`@state_trigger` takes a single string `str_expr` that is an expression based on one or more state
variables, and evaluates to `True` or `False` (or non-zero or zero). Whenever the state variables
mentioned in the expression change, the expression is evaluated and the trigger occurs if it
evaluates to `True` (or non-zero). For each state variable, eg: `domain.name`, the prior value if
also available to the expression as `domain.name.old` in case you want to condition the trigger on
the prior value too. Note that all state variables have string values. So you'll have to do
comparisons against string values or cast the variable to an integer or float. These two examples
are essentially equivalent (note the use of single quotes inside the outer double quotes):
```python
@state_trigger("domain.light_level == '255' or domain.light2_level == '0'")
```
```python
@state_trigger("int(domain.light_level) == 255 or int(domain.light2_level) == 0")
```
although the second will give an exception if the variable string doesn't represent a valid integer.
If you want numerical inequalities you should use the second form, since string lexicographic
ordering is not the same as numeric ordering.

If you specify `@state_trigger("True")` the state trigger will never occur. While that might seem
counter-intuitive, the reason is that the expression will never be evaluated - it takes underlying
state variables in the expression to change before the expression is ever evaluated. Since this
expression has no state variables, it will never be evaluated. You can achieve a state trigger on
any value change with a decorator of the form:
```python
@state_trigger("True or domain.light_level")
```
The reason this works is that the expression is evaluated every time `domain.light_level` changes.
Because of operator short-circuiting, the expression evaluates to `True` without even checking the
value of `domain.light_level`. So the result is a trigger whenever the state variable changes to
any value. This idea can extend to multiple variables just by stringing them together.

Note that if a state variable is set to the same value, HA doesn't generate a state change event,
so the `@state_trigger` condition will not be checked.  It is only evaluated each time a state
variable changes to a new value.

When the trigger occurs and the function is executed (meaning any active checks passed too), keyword
arguments are passed to the function so it can tell which state variable caused it to succeed and
run, in cases where the trigger condition involves multiple variables. These are:
```python
kwargs = {
    "trigger_type": "state",
    "var_name": var_name,
    "value": new_value,
    "old_value": old_value
}
```
If your function needs to know any of these values, you can list the keyword arguments you need,
with defaults:
```python
@state_trigger("domain.light_level == '255' or domain.light2_level == '0'")
def light_turned_on(trigger_type=None, var_name=None, value=None):
    pass
```
Using `trigger_type` is helpful if you have multiple trigger decorators. The function can now tell
which type of trigger, and which of the two variables changed to cause the trigger. You can also
use the keyword catch-all declaration instead:
```python
@state_trigger("domain.light_level == '255' or domain.light2_level == '0'")
def light_turned_on(**kwargs)
    log.info(f"got arguments {kwargs}")
```
and all those values will simply get passed in into kwargs as a `dict`. That's the most useful
form to use if you have multiple decorators, since each one passes different variables into
the function (although all of them set `"trigger_type"`).

#### `@time_trigger(str_spec, ...)`

`@time_trigger` takes one or more strings that specify time-based triggers. When multiple time
triggers are specified, each are evaluated, and the earliest one is the next trigger. Then the
process repeats.

Several of the time specifications use a `datetime` format, which is ISO: `yyyy/mm/dd hh:mm:ss`,
except there is no time-zone (local is assumed). Seconds can include a decimal (fractional) portion
if you need finer resolution. The date is optional, and the year can be omitted with just `mm/dd`.
The date can also be replaced by a day of the week (either full or 3-letters, based on the locale).
The meaning of partial or missing dates depends on the trigger, as explained below. The time can
instead be `sunrise` or `sunset`. The `datetime` can be followed by an offset of the form
`[+-]number{sec|min|hours|days|weeks}` and single-letter abbreviations can be used. That allows
things like `sunrise + 30m` to mean 30 minutes after sunrise. The `number` can be floating point.
(Note, there is currently no i18n support for those offset abbreviations - they are in English.)

In `@time_trigger`, each string specification can take one of three forms:
- `"once(datetime)"` triggers once on the date and time. If the year is omitted, it triggers once
per year on the date and time (eg, birthday). If the date is just a day of week, it triggers once
on that day of the week. If the date is omitted, it triggers once each day at the indicated time.
- `"period(datetime_start, interval, datetime_end)"` or `"period(datetime_start, interval)"`
triggers every interval starting at the starting datetime and finishing at the optional ending
datetime. When there is no ending datetime, the periodic trigger runs forever. The interval has
the form `number{sec|min|hours|days|weeks}` (the same as datetime offset without the leading sign),
and single-letter abbreviations can be used.
- `"cron(min hr dom mon dow)"` triggers according to Linux-style crontab. Each of the five entries are
separated by spaces and correspond to minutes, hours, day-of-month, month, day-of-week (0 = sunday):

|field|allowed values|
|-----|--------------|
|minute|0-59
|hour|0-23
|day of month|1-31
|month|1-12
|day of week|0-6 (0 is Sun)

Each field can be a `*` (which means "all"), a single number, a range or comma-separated list of
numbers or ranges (no spaces). Ranges are inclusive. For example, if you specify hours as
`6,10-13` that means hours of 6,10,11,12,13. The trigger happens on the next minute, hour,
day that matches the specification. See any Linux documentation for examples and more details
(note: names for days of week and months are not supported; only their integer values are).

When the `@time_trigger` occurs, and the function is called, the only keyword argument is
`trigger_type`, which is set to `"time"`.

A final special form of `@time_trigger` has no arguments, which causes the function to
run once automatically on startup or reload:
```python
@time_trigger
def run_on_startup_or_reload():
    """This function runs automatically once on startup or reload"""
    pass
```
The function is not re-started after it returns, unless a reload occurs. Startup occurs
when the `EVENT_HOMEASSISTANT_STARTED` event is fired, which is after everything else
is initialized and ready, so this function can call any services etc. (Note: currently
no `trigger_type` or other arguments are  passed when this startup function is called.)

#### `@event_trigger(event_type, str_expr=None)`

`@event_trigger` triggers on the given `event_type`. An optional `str_expr` can be used to
match the event data, and the trigger will only occur if that expression evaluates to
`True` or non-zero. This expression has available all the event parameters sent with
the event, together with these two variables:
- `trigger_type` is set to "event"
- `event_type` is the string event type, which will be the same as the first argument
to `@event_trigger`

Note unlike state variables, the event data values are not forced to be strings, so typically
that data has its native type.

When the `@event_trigger` occurs, those same variables are passed as keyword arguments to
the function in case it needs them.

The `event_type` could be a user-defined string, or it could be one of the built-in events.
You can access the names of those events by importing from `homeassistant.const`, eg:
```python
from homeassistant.const import EVENT_CALL_SERVICE
````

To figure out what parameters are sent with an event and what objects (eg: `list`, `dict`)
are used to represent them, you can look at the HASS source code, or initially use the
`**kwargs` argument to capture all the parameters and log them.  For example, you might
want to trigger on certain service calls (not ones directed to pyscript), but you are
unsure which one and what parameters it has.  So initially you trigger on all service
calls just to see them:
```python
from homeassistant.const import EVENT_CALL_SERVICE

@event_trigger(EVENT_CALL_SERVICE)
def monitor_service_calls(**kwargs):
    log.info(f"got EVENT_CALL_SERVICE with kwargs={kwargs}")
```
After running that, you see that you are interested in the service call `lights.turn_on`,
and you see that the `EVENT_CALL_SERVICE` event has parameters `domain` set to `lights`
and `service` set to `turn_on`, and the service parameters are passed as a `dict`
in `service_data`.  So then you can narrow down the event trigger to that particular
service call:
```python
from homeassistant.const import EVENT_CALL_SERVICE

@event_trigger(EVENT_CALL_SERVICE, "domain == 'lights' and service == 'turn_on'")
def monitor_light_turn_on_service(service_data=None):
    log.info(f"lights.turn_on service called with service_data={service_data}")
```
This [wiki page](https://github.com/custom-components/pyscript/wiki/Event-based-triggers)
gives more examples of built-in and user events and how to create triggers for them.

#### `@state_active(str_expr)`

When any trigger occurs (whether time, state or event), the `@state_active` expression is evaluated.
If it evaluates to `False` (or zero), the trigger is ignored and the trigger function is not called.

#### `@time_active(str_spec, ...)`

`@time_active` takes one or more strings that specify time-based ranges. When any trigger
occurs (whether time, state or event), each time range specification is checked. If the
current time doesn't fall within any range specified, the trigger is ignored and the
trigger function is not called.

Each string specification can take two forms:
- `"range(datetime_start, datetime_end)"` is satisfied if the current time is in the indicated
range, including the end points. As in `@time_trigger`, the year or date can be omitted to
specify daily ranges. If the end is prior to the start, the range is satisfied if the current
time is either greater than or equal to the start or less than or equal to the end. That allows
a range like:
    ```python
    @time_active("range(sunset - 20min, sunrise + 15min)")
    ```
    to mean at least 20 minutes before sunset, or at least 15 minutes after sunrise (note: at
latitudes close to the polar circles, there can be cases where the sunset time is after midnight, so
it is before the sunrise time, so this might not work correctly; at even greater latitudes sunset
and sunrise will not be defined at all since there might not be daily sunrises or sunsets).
- `"cron(min hr dom mon dow)"` is satisfied if the current time matches the range specified by
the `cron` parameters. For example, if `hr` is `6-10` that means hours between 6 and 10
inclusive. If additionally `min` is `*` (i.e., any), then that would mean a time interval
from 6:00 to immediately prior to 11:00.

Each argument specification can optionally start with `not`, which inverts the meaning of that range
or cron specification.  If you specify multiple arguments without 'not', they are logically or'ed
together, meaning the active check is true if any of the (positive) time ranges are met.  If you
have several `not` arguments, they are logically and'ed together, so the active check will be true
if the current time doesn't match any of the "not" (negative) specifications.  `@time_active` allows
multiple arguments with and without `not`.  The condition will be met if the current time matches
any of the positive arguments, and none of the negative arguments.

#### `@service`

The `@service` decorator causes the function to be registered as a service so it can be called externally.
The `@state_active` and `@time_active` decorators don't affect the service - those only apply to time,
state and event triggers specified by other decorators.

The function is called with keyword parameters set to the service call parameters, plus `trigger_type`
is set to `"service"`.

The `doc_string` (the string immediately after the function declaration) is used as the service description
that appears is in the Services tab of the Developer Tools page. The function argument names are
used as the service parameter names, but there is no description.

Alternatively, if the `doc_string` starts with `yaml`, the rest of the string is used as a `yaml`
service description. Here's the first example above, with a more detailed `doc_string`:
```python
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
```

## Built-in Functions

Most of these have been mentioned already, but here is the complete list of additional functions
made available by `pyscript`.

Note that even though the function names contain a period, the left portion is not a class (e.g.,
`state` is not a class, and in fact isn't even defined). These are simply functions whose name
includes a period. This is one aspect where the interpreter behaves differently from real Python.

#### Accessing state variables

State variables can be used and set just by using them as normal Python variables. However, there could
be cases where you want to dynamically generate the variable name (eg, in a loop). These functions
allow you to get and set a variable using its string name. The set function also allows you to optionally
set the attributes, which you can't do if you are directly assigning to the variable:

`state.get(name)` returns the value of the state variable, or `None` if it doesn't exist

`state.set(name, value, attr=None)` sets the state variable to the given value, with the optional attributes.

Note that in HASS, all state variable values are coerced into strings.  For example, if a state variable
has a numeric value, you might want to convert it to a numeric type (eg, using `int()` or `float()`).

#### Service Calls

`service.call(domain, name, **kwargs)` calls the service `domain.name` with the given keyword arguments as
parameters.

`service.has_service(domain, name)` returns whether the service `domain.name` exists.

#### Event Firing

`event.fire(event_type, **kwargs)` sends an event with the given `event_type` string and the keyword
parameters as the event data.

#### Logging functions

Four logging functions are provided, with increasing levels of severity:
- `log.debug(str)`
- `log.info(str)`
- `log.warning(str)`
- `log.error(str)`

The [Logger](/integrations/logger/) component can be used to specify the logging level. Log messages
below the configured level will not appear in the log. Each log message function uses a log name of
the form:
```python
homeassistant.components.pyscript.file.FILENAME.FUNCNAME
```
where `FUNCNAME` is the name of the top-level Python function (e.g., the one called by a trigger or
service), defined in the script file `FILENAME.py`. That allows you to set the log level for each
Python top-level function separately if necessary. That setting also applies to any other Python
functions that the top-level Python function calls. For example, these settings:
```yaml
logger:
  default: info
  logs:
    homeassistant.components.pyscript.file: info
    homeassistant.components.pyscript.file.my_scripts.my_function: debug
```
will log all messages at `info` or higher (ie: `log.info()`, `log.warning()` and `log.error()`), and
inside `my_function` defined in the script file `my_scripts.py` (and any other functions it calls)
will log all messages at `debug` or higher.

#### Sleep

`task.sleep(seconds)` sleeps for the indicated number of seconds, which can be floating point. Do not
import `time` and use `time.sleep()` - that will block lots of other activity.

#### Task Unique

`task.unique(task_name, kill_me=False)` kills any running task that previously called `task.unique`
with the same `task_name`. The name can be any string. If `kill_me` is `True` then the current
task is killed if another task that is running previously called `task.unique` with the same
`task_name`.

Note that `task.unique` applies across all global contexts. It's up to you to use a convention
for `task_name` that avoids accidential collisions. For example, you could use a prefix of the
script file name, so that all `task_unique` calls in `FILENAME.py` use a `task_name` that
starts with `"FILENAME."`.

#### Waiting for events

`task.wait_until()` allows functions to wait for events, using identical syntax to the decorators.
This can be helpful if at some point during execution of some logic you want to wait for some
additional triggers.

It takes the following keyword arguments (all are optional):
- `state_trigger=None` can be set to a string just like `@state_trigger`.
- `time_trigger=None` can be set to a string or list of strings with datetime specifications, just
like `@time_trigger`.
- `event_trigger=None` can be set to a string or list of two strings, just like `@event_trigger`.
The first string is the name of the event, and the second string (when the setting is a two-element
list) is an expression based on the event parameters.
- `timeout=None` an overall timeout in seconds, which can be floating point.
- `state_check_now=True` if set, `task.wait_until()` checks any `state_trigger` immediately to see
if it is already `True`, and will return immediately if so. If `state_check_now=False`,
`task.wait_until()` waits until a state variable change occurs, before checking the
expression. Using `True` is safer to help avoid race conditions, although `False` makes
`task.wait_until()` behave like `@state_trigger`, which doesn't check at startup.
However, if you use the default of `True`, and your function will call `task.wait_until()`
again, it's recommended you set that state variable to some other value immediately after
`task.wait_until()` returns. Otherwise the next call will also return immediately.

When a trigger occurs, the return value is a `dict` containing the same keyword values that are
passed into the function when the corresponding decorator trigger occurs. There will always
be a key `trigger_type` that will be set to:
- `"state"`, `"time"` or `"event"` when each of those triggers occur.
- `"timeout"` if there is a timeout after `timeout` seconds (the `dict` has no other values)
- `"none"` if you specify only `time_trigger` and no `timeout`, and there is no future next
time that satisfies the trigger condition (e.g., a `range` or `once` is now in the past).
Otherwise, `task.wait_until()` would never return.

In the special case that `state_check_now=True` and `task.wait_until()` returns immediately, the
other return variables that capture the variable name and value that just caused the trigger are
not included in the `dict` - it will just contain `trigger_type="state"`.

Here's an example.  Whenever a door is opened, we want to do something if the door closes within
30 seconds.  If a timeout of more than 30 seconds elapses (ie, the door is still open), we want
to do some other action.  We use a decorator trigger when the door is opened, and we use
`task.wait_until` to wait for either the door to close, or a timeout of 30 seconds to elapse.
The return value tells which of the two events happened:
```python
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
```

`task.wait_until()` is logically equivalent to using the corresponding decorators, with some
important differences. Consider these two alternatives, which each run some code whenever
there is an event `test_event3` with parameters `args == 20` and `arg2 == 30`:
```python
@event_trigger("test_event3", "arg1 == 20 and arg2 == 30")
def process_test_event3(**trig_info):
    # do some things, including waiting a while
    task.sleep(5)
    # do some more things
```
versus:
```python
@time_trigger    # empty @time_trigger means run the function on startup
def wait_for_then_process_test_event3():
    while 1:
        trig_info = task.wait_until(
                        event_trigger=["test_event3", "arg1 == 20 and arg2 == 30"]
                    )
        # do some things, including waiting a while
        task.sleep(5)
        # do some more things
```
Logically they are the similar, but the important differences are:
- `task.wait_until()` only looks for the trigger conditions when it is called, and it stops
monitoring them as soon as it returns. That means the trigger (especially an event trigger) could
occur before or after `task.wait_until()` is called, and you will miss the event. In contrast, the
decorator triggers monitor the trigger conditions continuously, so they will not miss state changes
or events once they are initialized. The reason for the `state_check_now` argument, and its default
value of `True` is to help avoid this race condition for state triggers. Time triggers should
generally be safe.
- The decorators run each trigger function as a new independent task, and don't wait for it to
finish. So a function will be run for every matching event. In contrast, if your code runs for
a while before calling `task.wait_until()` again (e.g., `task.sleep()` or any code), or even if
there is no other code in the `while` loop, some events or state changes of interest will be
potentially missed.

Summary: use trigger decorators whenever you can. Be especially cautious using `task.wait_until()`
to wait for events; you must make sure your logic is robust to missing events that happen before or
after `task.wait_until()` runs.

#### Global Context functions

Each pyscript script file runs inside its own global context, which means their global variables and
functions are isolated from other script files. Each Jupyter session also runs in its own global
context.  For a script file called `FILENAME.py`, its global context is `file.FILENAME`. Each
Jupyter global context name is `jupyter_NNN` where `NNN` is a unique integer starting at 0.

In normal use you don't need to worry about global contexts. But for interactive debugging and
development, you might want your Jupyter session to access variables and functions defined in
a script file. Three functions are provided for getting, setting and listing the global
contexts. That allows you to interactively change the global context during a Jupyter
session. You could also use these functions in your script files, but that is strongly
discouraged.  Here are the functions:
- `pyscript.get_global_ctx()` returns the current global context name.
- `pyscript.list_global_ctx()` lists all the global contexts, with the current global context listed first.
- `pyscript.set_global_ctx(new_ctx_name)` sets the current global context to the given name.

When you exit a Jupyter session, its global context is deleted, which means any triggers, functions,
services and variables you created are deleted (HASS state variables survive). If you switch to
a script file's context, then any triggers, functions, services or variables you interactively
create there will persist after you exit the Jupyter session.  However, if you don't update the
corresponding script file, then upon the next pyscript reload or HASS restart, those interactive
changes will be lost.

## Contributing

Contributions are welcome! You are encouraged to submit PRs, bug reports, feature requests or
add to the Wiki with examples and tutorials. It would be fun to hear about unique and clever
applications you develop.

## Useful Links

* [Documentation](https://github.com/custom-components/pyscript/blob/master/README.md)
* [Issues](https://github.com/custom-components/pyscript/issues)
* [Wiki](https://github.com/custom-components/pyscript/wiki)
* [GitHub repository](https://github.com/custom-components/pyscript) (please add a star if you like `pyscript`!)
* [Jupyter notebook tutorial](https://github.com/custom-components/pyscript/blob/master/jupyter/pyscript_tutorial.ipynb)

## Copyright

Copyright (c) 2020 Craig Barratt.  May be freely used and copied according to the terms of the
[Apache 2.0 License](LICENSE).
