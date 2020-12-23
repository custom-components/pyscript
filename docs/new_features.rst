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

- Consider implementing function decorators (#43)
- Consider supporting the built-in functions that do I/O, such as ``open``, ``read`` and ``write``, which
  are not currently supported to avoid I/O in the main event loop, and also to avoid security issues if people
  share pyscripts. The ``print`` function only logs a message, rather than implements the real ``print`` features,
  such as specifying an output file handle. Support might be added in the future using an executor job, perhaps
  enabled when ``allow_all_imports`` is set.

The new features since 1.1.0 in master include:

- Reload is automatically done whenever a script file, ``requirements.txt`` or ``yaml`` file below the
  ``pyscript`` folder is modified, created, renamed or deleted, or a directory is renamed, created or
  deleted (see #74).
- New functions ``task.create``, ``task.cancel``, ``task.wait``, ``task.add_done_callback``,
  ``task.remove_done_callback`` allow new background (async) tasks to be created, canceled, waited on,
  and completion callbacks to be added or deleted (see #112).
- New function decorator ``@pyscript.compile`` compiles a native Python function inside pyscript, which
  is helpful if you need a regular function (all pyscript functions are coroutines) for callbacks or
  other uses like ``map()``, or if you have code you want to run at compiled speed (see #71). The
  function body can't contain any pyscript-specific features, and closure of variables for an inner
  function that uses ``@pyscript.compile`` won't work either, since in pyscript local variables with
  scope binding are objects, not their native types.  Note also that this is an experimental feature
  and the decorator name or other features might change prior to release; feedback welcome.

Breaking changes since 1.1.0 include:

Bug fixes since 1.1.0 include:

- Fixed shutdown trigger for case where it calls ``task.unique()`` (#117).
- Duplicate ``@service`` function definitions (ie, with the same name) now correctly register
  the service, reported by @wsw70 (#121)
- Added error message for invalid ``@time_active`` argument, by @dlashua (#118).
- The ``scripts`` subdirectory is now recursively traversed for ``requirements.txt`` files.
