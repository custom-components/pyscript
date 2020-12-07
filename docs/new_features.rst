New Features
============

The releases and release notes are available on `GitHub <https://github.com/custom-components/pyscript/releases>`__.
Use HACS to install different versions of pyscript.

You can also install the master (head of tree) version from GitHub, either using HACS or manually.
Because pyscript has quite a few unit tests, generally master should work ok. But it's not guaranteed
to work at any random time.

The latest release is 1.0.0, released on November 9, 2020.  Here is the `stable documentation <https://hacs-pyscript.readthedocs.io/en/stable>`__
for that release.

Over time, the master (head of tree) version in GitHub will include new features and bug fixes.
Here is the master `latest documentation <https://hacs-pyscript.readthedocs.io/en/latest>`__
if you want to see the new features or bug fixes.

Planned new features post 1.0.0 include:

- support mqtt triggers (#98, #105)
- use ``aionofity`` to auto-reload newly written script files, at least on linux (#74)
- consider allowing native Python functions inside pyscript (#71)
- consider implementing function decorators (#43)
- consider supporting the built-in functions that do I/O, such as ``open``, ``read`` and ``write``, which
  are not currently supported to avoid I/O in the main event loop, and also to avoid security issues if people
  share pyscripts. The ``print`` function only logs a message, rather than implements the real ``print`` features,
  such as specifying an output file handle. Support might be added in the future using an executor job, perhaps
  enabled when ``allow_all_imports`` is set.

The new features since 1.0.0 in master include:

- ``pyscript.reload`` only reloads changed files (changed contents, mtime, or an app's yaml configuration).
  All files in an app or module are reloaded if any one has changed, and any script, app or module that
  imports a changed modules (directly or indirectly) is also reloaded. Setting the optional ``global_ctx``
  service parameter to ``*`` forces reloading all files (which is the behavior in 1.0.0 and earlier).
- Adding new decorator ``@mqtt_trigger`` by @dlashua (#98, #105).
- Added ``state_hold_false=None`` optional period in seconds to ``@state_trigger()`` and ``task.wait_until()``.
  This requires the trigger expression to be ``False`` for at least that period (including 0) before
  a successful trigger. Setting this optional parameter makes state triggers edge triggered (ie,
  triggers only on transition from ``False`` to ``True``), instead of the default level trigger
  (ie, only has to evaluate to ``True``). Proposed by @tchef69 (#89).
- All .py files below the ``pyscript/scripts`` directory are autoloaded, recursively.  Also, any
  file name or directory starting with ``#`` is skipped (including top-level and ``apps``), which is
  an in-place way of disabling a specific script, app or directory tree (#97).
- ``del`` and new function ``state.delete()`` can delete state variables and state variable attributes.

Bug fixes since 1.0.0 in master include:

- State setting now copies the attributes, to avoid a strange ``MappingProxyType`` recursion error
  inside HASS, reported by @github392 (#87).
- The deprecated function ``state.get_attr`` was missing an ``await``, which causes an exception; in 1.0.0 use
  ``state.getattr``, reported and fixed by @dlashua (#88).
- The ``packaging`` module is installed if not found, since certain HASS configurations might not include it;
  fixed by @raman325 (#90, #91).
