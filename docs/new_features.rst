New Features
============

The releases and release notes are available on `GitHub <https://github.com/custom-components/pyscript/releases>`__.
Use HACS to install different versions of pyscript.

You can also install the master (head of tree) version from GitHub, either using HACS or manually.
Because pyscript has quite a few unit tests, generally the master version should work ok. But it's not
guaranteed to work at any random time, and newly-added features might change.

The latest release is 1.2.1, released on February 9, 2021.  Here is the `stable documentation
<https://hacs-pyscript.readthedocs.io/en/stable>`__ for that release.

Over time, the master (head of tree) version in GitHub will include new features and bug fixes.
Here is the `latest documentation <https://hacs-pyscript.readthedocs.io/en/latest>`__ if you want
to see the development version of the documentation.

If you want to see development progress since 1.2.1, see
`new features <https://hacs-pyscript.readthedocs.io/en/latest/new_features.html>`__
in the latest documentation.

Planned new features post 1.2.1 include:

- Consider supporting the built-in functions that do I/O, such as ``open``, ``read`` and ``write``, which
  are not currently supported to avoid I/O in the main event loop, and also to avoid security issues if people
  share pyscripts. The ``print`` function only logs a message, rather than implements the real ``print`` features,
  such as specifying an output file handle. Support might be added in the future using an executor job, perhaps
  enabled when ``allow_all_imports`` is set.

The new features since 1.2.1 in master include:

- Multiple trigger decorators (``@state_trigger``, ``@time_trigger``, ``@event_trigger`` or ``@mqtt_trigger``)
  per function are now supported. See #157.
- Trigger decorators (``@state_trigger``, ``@time_trigger``, ``@event_trigger`` or ``@mqtt_trigger``) support
  an optional ``kwargs`` keyword argument that can be set to a ``dict`` of keywords and values, which are
  passed to the trigger function. See #157.
- The ``@service`` decorator now takes one of more optional arguments to specify the name of the service of the
  form ``"DOMAIN.SERVICE"``. The ``@service`` also can be used multiple times as an alternative to using multiple
  arguments. The default continues to be ``pyscript.FUNC_NAME``.
- Added ``@pyscript_executor`` decorator, which does same thing as ``@pyscript_compile`` and additionally wraps
  the resulting function with a call to ``task.executor``.  See #71.
- Errors in trigger-related decorators (eg, wrong arguments, unregonized decorator type) raise exceptions rather
  than logging an error.
- Added error checking for ``@pyscript_compile`` and ``@pyscript_executor`` to enforce there are no args or kwargs.

Breaking changes since 1.2.1 include:

- Since decorator errors now raise exceptions, if you had a script with such an error that otherwise works, that
  script will now give an exception and fail to load. The error should be self-explanatory, and it's good to know
  so you can fix it.

Bug fixes since 1.2.1 include:

- Fixed ``@state_trigger`` with only a ``.old`` variable, which previously never triggered; reported by stigvi.
- Reload with global_ctx="*" now starts triggers, reported by Fabio C.
- Fixed subscripts when running python 3.9.x.
- Timeouts that implement time triggers might infrequenctly occur a tiny time before the target time. A fix was added
  to do an additional short timeout when there is an early timeout, to make sure any time trigger occurs at or shortly
  after the target time (and never before).
- When exception text is created, ensure lineno is inside code_list[]; with lambda function or eval it might not be.
- An exception is raised when a function is called with unexpected keyword parameters that don't have corresponding
  keyword arguments (however, the trigger parameter names are excluded from this check, since trigger functions
  are allowed to have any subset of keyword arguments).
