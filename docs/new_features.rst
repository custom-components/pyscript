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

- use ``aionofity`` to auto-reload newly written script files, at least on linux (#74)
- consider allowing native Python functions inside pyscript (#71)
- consider allowing new tasks to be created via `task.create` (#112)
- consider implementing function decorators (#43)
- consider supporting the built-in functions that do I/O, such as ``open``, ``read`` and ``write``, which
  are not currently supported to avoid I/O in the main event loop, and also to avoid security issues if people
  share pyscripts. The ``print`` function only logs a message, rather than implements the real ``print`` features,
  such as specifying an output file handle. Support might be added in the future using an executor job, perhaps
  enabled when ``allow_all_imports`` is set.

The new features since 1.1.0 in master include:

Breaking changes since 1.1.0 include:

Bug fixes since 1.1.0 include:

- Fixed shutdown trigger for case where it calls ``task.unique()`` (#117).
- Added error message for invalid ``@time_active`` argument, by @dlashua (#118).
