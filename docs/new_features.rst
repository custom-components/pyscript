New Features
============

The releases and releae notes are available on `GitHub <https://github.com/custom-components/pyscript/releases>`__.
Use HACS to install different versions of pyscript.

You can also install the master (head of tree) version from GitHub, either using HACS or manually.
Because pyscript has quite a few unit tests, generally master should work ok. But it's not guaranteed
to work at any random time.

The latest release is 0.32, released on October 21, 2020.  Here is the `stable documentation <https://hacs-pyscript.readthedocs.io/en/stable>`__
for that release.

Since that release, the master (head of tree) version in GitHub has several new features and bug fixes.
Here is the master `latest documentation <https://hacs-pyscript.readthedocs.io/en/latest>`__.

The new features since 0.32 in master include:

- Pyscript state variables (entity_ids) can be persisted across pyscript reloads and HASS restarts,
  from @swazrgb and @dlashua (#48).
- The ``hass`` object is available in all pyscript global contexts if the ``hass_is_global`` configuration parameter
  is true (default false). This allows access to HASS internals that might not be otherwise exposed by pyscript.
  Use with caution (#51).
- Improvements to UI config flow, including allowing parameters to be updated, and the UI reload now works the same
  as the ``pyscript.reload`` service call, submitted by @raman325 (#53)
- State variables now support virtual attributes ``last_changed`` and ``last_updated`` for the UTC time when state
  values or any attribute was last changed.
- ``@state_trigger`` and ``task.wait_until`` now have an optional ``state_hold`` duration in seconds that requires
  the state trigger to remain true for that period of time. The trigger occurs after that time elapses. If the state
  trigger changes to false before the time expires, the process of waiting for a new trigger starts over.
- ``@time_active`` now has an optional ``hold_off`` duration in seconds, which ignores a new trigger if the last
  one happened within that time.  Can be used for rate limiting or debouncing. Also, ``@time_active`` can now take
  zero time range arguments, in case you want to just specify ``hold_off``.
- Added inbound ``context`` variable to trigger functions and support optional ``context`` setting on state,
  event firing and service calls. Proposal and PR by @dlashua (#50, #60).
- Logbook now supported using ``context`` and informational message based on trigger type. Proposal and PR by
  @dlashua (#50, #62).
- Required Python packages can be specified in ``requirements.txt`` files at the top-level pyscript
  directory, and each module's or app's directory. Those files are read and any missing packages are
  installed on HASS startup and pyscript reload. Contributed by @raman325 (#66, #68, #69).
- State variable attributes can be set by direct assignment, eg: ``DOMAIN.name.attr = value``. A
  equivalent new function ``state.setattr()`` allows a specific attribute to be set.
- The reload service now takes an optional parameter ``global_ctx`` that specifies just that
  global context is reloaded, eg: ``global_ctx="file.my_scripts"``.  Proposed by @dlashua (#63).
- The ``state.get_attr()`` function has been renamed ``state.getattr()``. The old function is
  still available and will be removed in some future release.
- Entities ``DOMAIN.ENTITY`` now support a virtual method ``SERVICE`` (eg, ``DOMAIN.ENTITY.SERVICE()``)
  that calls the service ``DOMAIN.SERVICE`` for any service that has an ``entity_id`` parameter, with
  that ``entity_id`` set to ``DOMAIN.ENTITY``. Proposed by @dlashua (#64).
- VSCode connections to pyscript's Jupyter kernel now work.  Two changes were required: VSCode immediately
  closes the heartbeat port, which no longer causes pyscript to shut down the kernel.  Also, ``stdout``
  messages are flushed prior to sending the execute complete message. This is to ensure `log` and `print`
  messages get displayed in VSCode. One benign but unresolved bug with VSCode is that when you connect
  to the pyscript kernel, VSCode starts a second pyscript Jupyter kernel, before shutting that second one
  down.

The bug fixes since 0.32 in master include:

- Jupyter autocomplete now works on multiline code blocks.
- Improved error message reporting for syntax errors inside f-strings.
- Fixed incorrect global context update on calling module that, in turn, does a callback (#58)
