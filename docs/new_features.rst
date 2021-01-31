New Features
============

The releases and release notes are available on `GitHub <https://github.com/custom-components/pyscript/releases>`__.
Use HACS to install different versions of pyscript.

You can also install the master (head of tree) version from GitHub, either using HACS or manually.
Because pyscript has quite a few unit tests, generally the master version should work ok. But it's not
guaranteed to work at any random time, and newly-added features might change.

The latest release is 1.1.0, released on December 10, 2020.  Here is the `stable documentation
<https://hacs-pyscript.readthedocs.io/en/stable>`__ for that release.

Over time, the master (head of tree) version in GitHub will include new features and bug fixes.
Here is the `latest documentation <https://hacs-pyscript.readthedocs.io/en/latest>`__ if you want
to see the development version of the documentation.

If you want to see development progress since 1.1.0, see
`new features <https://hacs-pyscript.readthedocs.io/en/latest/new_features.html>`__
in the latest documentation.

Planned new features post 1.1.0 include:

- Consider supporting the built-in functions that do I/O, such as ``open``, ``read`` and ``write``, which
  are not currently supported to avoid I/O in the main event loop, and also to avoid security issues if people
  share pyscripts. The ``print`` function only logs a message, rather than implements the real ``print`` features,
  such as specifying an output file handle. Support might be added in the future using an executor job, perhaps
  enabled when ``allow_all_imports`` is set.

The new features since 1.1.0 in master include:

- Reload is automatically done whenever a script file, ``requirements.txt`` or ``yaml`` file below the
  ``pyscript`` folder is modified, created, renamed or deleted, or a directory is renamed, created or
  deleted; see #74.
- New functions ``task.create``, ``task.current_task``, ``task.cancel``, ``task.name2id``, ``task.wait``,
  ``task.add_done_callback``, ``task.remove_done_callback`` allow new background (async) tasks to be
  created, canceled, waited on, and completion callbacks to be added or deleted.  Proposed by @dlashua
  and @valsr; see #112, #130, #143, #144.
- Added support for ``now`` to ``@time_trigger`` time specifications, which means the current date and
  time when the trigger was first evaluated (eg, at startup or when created as an inner function or closure),
  and remains fixed for the lifetime of the trigger. This allows time triggers of the form ``once(now + 5min)``
  or ``period(now, 1hr)``.
- Function decorators are now supported. However, the existing trigger decorators are still hardcoded
  (ie, not available as function calls), and decorators on classes are not yet supported.  First
  implementation by @dlashua; see #43.
- New function decorator ``@pyscript.compile`` compiles a native Python function inside pyscript, which
  is helpful if you need a regular function (all pyscript functions are coroutines) for callbacks or
  other uses like ``map()``, or if you have code you want to run at compiled speed (see #71). The
  function body can't contain any pyscript-specific features, and closure of variables for an inner
  function that uses ``@pyscript.compile`` won't work either, since in pyscript local variables with
  scope binding are objects, not their native types.  Note also that this is an experimental feature
  and the decorator name or other features might change prior to release; feedback welcome.
  Proposed by @dlashua; see #71.
- A new variable ``pyscript.app_config`` is available in the global address space of an app's main
  file (ie, ``apps/YOUR_APP.py`` or ``apps/YOUR_APP/__init__.py``) and is set to the YAML configuration
  for your app (ie, ``pyscript.config["apps"][YOUR_APP]``). The latter is still available, but is
  deprecated and the ``apps`` entry in ``pyscript.config`` will be removed in a future release to
  prevent wayward applications from seeing configuration settings for other apps.
- Updated ``croniter`` to 1.0.2.
- Updated docs to explain how secret parameter values can be stored and retrieved from yaml
  configuration, by @exponentactivity; see #124.
- Report parsing errors on invalid ``@time_active`` arguments; by @dlashua; see #119.
- ``task.executor`` raises an exception when called with a pyscript function.

Breaking changes since 1.1.0 include:

None.  However, the use of ``pyscript.config["apps"][YOUR_APP]`` to get application configuration
is still available but now deprecated. The ``apps`` entry in ``pyscript.config`` will be removed in
a future release. This is to prevent wayward applications from seeing configuration settings for other
apps. The new ``pyscript.app_config`` variable should be used instead - it is set to
``pyscript.config["apps"][YOUR_APP]`` for each app.

Bug fixes since 1.1.0 include:

- Fixed shutdown trigger for case where it calls ``task.unique()``; reported by @dlashua (#117).
- Duplicate ``@service`` function definitions (ie, with the same name) now correctly register
  the service, reported by @wsw70; see #121.
- Added error message for invalid ``@time_active`` argument, by @dlashua; see #118.
- The ``scripts`` subdirectory is now recursively traversed for ``requirements.txt`` files.
- Inner functions and classes (defined inside a function) are added to global symbol table
  if declared as global.
- Reload all scripts if global settings ``allow_all_imports`` or ``hass_is_global`` change; see #74.
- Methods bound to class instances use weakrefs so that ``__del__`` works; reported by @dlashua; see #146.
- Inner functions and classes are added to global symbol table if declared as ``global``.
- Pyscript user-defined functions (which are all async) can now be called from native python async
  code; see #137.
- Internals that call ``open()`` now set ``encoding=utf-8`` so Windows platforms use the correct
  encoding; see #145.
- On Windows, python is missing ``locale.nl_langinfo``, which caused startup to fail when the
  locale-specific days of week were extracted.  Now the days of week in time trigger expressions
  are available on Windows, but only in English; see #145.
- ``task.name2id()`` raises ``NameError`` if task name is undefined. Also added ``kwargs`` to ``task.wait()``.
- Added ``"scripts/**" to ``REQUIREMENTS_PATHS``, so deeper directories are searched.
- Fixed typos in task reaper code, by @dlashua; see #116.
- Fixed exception on invalid service call positional arguments, reported by @huonw; see #131.
