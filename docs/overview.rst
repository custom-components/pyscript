Overview
--------

This HACS custom integration allows you to write Python functions and
scripts that can implement a wide range of automation, logic and
triggers. State variables are bound to Python variables and services are
callable as Python functions, so it's easy and concise to implement
logic.

Functions you write can be configured to be called as a service or run
upon time, state-change or event triggers. Functions can also call any
service, fire events and set state variables. Functions can sleep or
wait for additional changes in state variables or events, without
slowing or affecting other operations. You can think of these functions
as small programs that run in parallel, independently of each other, and
they could be active for extended periods of time.

State, event and time triggers are specified by Python function
decorators (the "@" lines immediately before each function definition).
A state trigger can be any Python expression using state variables - the
trigger is evaluated only when a state variable it references changes,
and the trigger occurs when the expression is true or non-zero. A time
trigger could be a single event (eg: date and time), a repetitive event
(eg: at a particular time each day or weekday, daily relative to sunrise
or sunset or any regular time period within an optional range) or using
cron syntax (where events occur periodically based on a concise
specification of ranges of minutes, hours, days of week, days of month
and months). An event trigger specifies the event type, and an optional
Python trigger test based on the event data that runs the Python
function if true.

Pyscript implements a Python interpreter using the ast parser output, in
a fully async manner. That allows several of the "magic" features to be
implemented in a seamless Pythonic manner, such as binding of variables
to states and functions to services. Pyscript supports imports, although
by default the valid import list is restricted for security reasons
(there is a configuration option ``allow_all_imports`` to allow all
imports). Pyscript supports almost all Python language features except
generators, ``yield``, and defining special class methods.
(see `language limitations <reference.html#language-limitations>`__).
Pyscript provides a handful of additional built-in functions that connect
to HASS features, like logging, accessing state variables as strings
(if you need to compute their names dynamically), running and managing
tasks, sleeping and waiting for triggers.

Pyscript also provides a kernel that interfaces with the Jupyter
front-ends (eg, notebook, console, lab and VSC). That allows you to develop
and test pyscript code interactively. Plus you can interact with much of
HASS by looking at state variables, calling services etc, in a similar
way to `HASS
CLI <https://github.com/home-assistant-ecosystem/home-assistant-cli>`__,
although the CLI provides status on many other parts of HASS.

For more information about the Jupyter kernel, see the
`README <https://github.com/craigbarratt/hass-pyscript-jupyter/blob/master/README.md>`__.
There is also a `Jupyter notebook
tutorial <https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb>`__,
which can be downloaded and run interactively in Jupyter notebook or VSC
connected to your live HASS with pyscript.

Pyscript provides functionality that complements the existing
automations, templates and triggers. Pyscript is most similar to
`AppDaemon <https://appdaemon.readthedocs.io/en/latest/>`__, and some
similarities and differences are discussed in this `Wiki
page <https://github.com/custom-components/pyscript/wiki/Comparing-Pyscript-to-AppDaemon>`__.
Pyscript with Jupyter makes it extremely easy to learn, use and debug.
Pyscripts presents a simplified and more integrated binding for Python
scripting than `Python
Scripts <https://www.home-assistant.io/integrations/python_script>`__,
which requires a lot more expertise and scaffolding using direct access
to Home Assistant internals.
