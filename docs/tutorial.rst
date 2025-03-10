Tutorial
========

Jupyter Tutorial
----------------

The best way to learn about pyscript is to interactively step through the
`Jupyter tutorial <https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb>`__.
After you have installed the pyscript Jupyter kernel, the tutorial can be downloaded
with:

.. code:: bash

   wget https://github.com/craigbarratt/hass-pyscript-jupyter/raw/master/pyscript_tutorial.ipynb

and opened with:

.. code:: bash

   jupyter notebook pyscript_tutorial.ipynb

You can step through each command by hitting <Shift>Enter. There are various ways to navigate and
run cells in Jupyter that you can read in the Jupyter documentation.

Writing your first script
-------------------------

Create a file ``example.py`` in the ``<config>/pyscript`` folder (you
can use any filename, so long as it ends in ``.py``) that contains:

.. code:: python

   @service
   def hello_world(action=None, id=None):
       """hello_world example using pyscript."""
       log.info(f"hello world: got action {action} id {id}")
       if action == "turn_on" and id is not None:
           light.turn_on(entity_id=id, brightness=255)
       elif action == "fire" and id is not None:
           event.fire(id, param1=12, param2=80)

After starting Home Assistant, use the Actions tab in the Developer
Tools page to call the service ``pyscript.hello_world`` with parameters

.. code:: yaml

action: pyscript.hello_world
data:
  action: hello
  id: world


The function decorator ``@service`` means ``pyscript.hello_world`` is
registered as a service. The expected service parameters are keyword
arguments to the function. This function prints a log message showing
the ``action`` and ``id`` that the service was called with. Then, if the
action is ``"turn_on"`` and the ``id`` is specified, the
``light.turn_on`` service is called. Otherwise, if the action is
``"fire"`` then an event type with that ``id`` is fired with the given
parameters. You can experiment by calling the service with different
parameters. (Of course, it doesn't make much sense to have a function
that either does nothing, calls another service, or fires an event, but,
hey, this is just an example.)

.. note::

   You'll need to look at the log messages to see the output (unless you are using Jupyter, in which
   case all log messages will be displayed, independent of the log setting). The log message won't
   be visible unless the ``Logger`` is enabled at least for level ``info``, for example:

   .. code:: yaml

      logger:
        default: info
        logs:
          custom_components.pyscript: info

An example using triggers
-------------------------

Here's another example:

.. code:: python

   @state_trigger("security.rear_motion == '1' or security.side_motion == '1'")
   @time_active("range(sunset - 20min, sunrise + 15min)")
   def motion_light_rear():
       """Turn on rear light for 5 minutes when there is motion and it's dark"""
       log.info(f"triggered; turning on the light")
       light.turn_on(entity_id="light.outside_rear", brightness=255)
       task.sleep(300)
       light.turn_off(entity_id="light.outside_rear")

This introduces two new function decorators

-  ``@state_trigger`` describes the condition(s) that trigger the
   function (the other two trigger types are ``@time_trigger`` and
   ``@event_trigger``, which we'll describe below). This condition is
   evaluated each time the variables it refers to change, and if it
   evaluates to ``True`` or non-zero then the trigger occurs.

-  ``@time_active`` describes a time range that is checked whenever a
   potential trigger occurs. The Python function is only executed if the
   ``@time_active`` criteria is met. In this example the time range is
   from 20 minutes before sunset to 15 minutes after sunrise (i.e., from
   dusk to dawn). Whenever the trigger is ``True`` and the active
   conditions are met, the function is executed as a new task. The
   trigger logic doesn't wait for the function to finish; it goes right
   back to checking for the next condition. The function turns on the
   rear outside light, waits for 5 minutes, and then turns it off.

However, this example has a problem. During those 5 minutes, any
additional motion event will cause another instance of the function to
be executed. You might have dozens of them running, which is perfectly
ok for ``pyscript``, but probably not the behavior you want, since as
each earlier one finishes the light will be turned off, which could be
much less than 5 minutes after the most recent motion event.

There is a special function provided to ensure just one function
uniquely handles a task, if that's the behavior you prefer. Here's the
improved example:

.. code:: python

   @state_trigger("security.rear_motion == '1' or security.side_motion == '1'")
   @time_active("range(sunset - 20min, sunrise + 20min)")
   def motion_light_rear():
       """Turn on rear light for 5 minutes when there is motion and it's dark"""
       task.unique("motion_light_rear")
       log.info(f"triggered; turning on the light")
       light.turn_on(entity_id="light.outside_rear", brightness=255)
       task.sleep(300)
       light.turn_off(entity_id="light.outside_rear")

The ``task.unique`` function will terminate any task that previously
called ``task.unique("motion_light_rear")``, and our instance will
survive. (The function takes a second argument that causes the opposite
to happen: the older task survives and we are terminated - so long!)

As before, this example will turn on the light for 5 minutes, but when
there is a new motion event, the old function (which is part way through
waiting for 5 minutes) is terminated, and we start another 5 minute
timer. The effect is the light will stay on for 5 minutes after the last
motion event, and stays on until there are no motion events for at least
5 minutes. If instead the second argument to ``task.unique`` is set,
that means the new task is terminated instead. The result is that the
light will go on for 5 minutes following a motion event, and any new
motion events during that time will be ignored since each new triggered
function will be terminated. Depending on your application, either
behavior might be preferred.

There are some other improvements we could make. We could check if the
light is already on so we don't have to turn it on again by checking
the relevant state variable:

.. code:: python

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

You could also create another function that calls
``task.unique("motion_light_rear")`` if the light is manually turned on
(by doing a ``@state_trigger`` on the relevant state variable), so that
the motion logic is stopped when there is a manual event that you want
to override the motion logic.
