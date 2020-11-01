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
  Use with caution. PR #51.
- Improvements to UI config flow, including allows parameters to be updated, and the UI reload now works the same
  as the ``pyscript.reload`` service call, submitted by @raman325 (#53)
- State variables now support virtual attributes last_changed and last_updated for the UTC time when state values
  or any attribute was last changed.
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

The bug fixes since 0.32 in master include:

- Jupyter autocomplete now works on multiline code blocks.
- Improved error message reporting for syntax errors inside f-strings.
- Fixed incorrect global context update on calling module that, in turn, does a callback (#58)
