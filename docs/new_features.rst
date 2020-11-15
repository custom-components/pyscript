New Features
============

The releases and release notes are available on `GitHub <https://github.com/custom-components/pyscript/releases>`__.
Use HACS to install different versions of pyscript.

You can also install the master (head of tree) version from GitHub, either using HACS or manually.
Because pyscript has quite a few unit tests, generally master should work ok. But it's not guaranteed
to work at any random time.

The latest release is 1.0.0, released on November 9, 2020.  Here is the `stable documentation <https://hacs-pyscript.readthedocs.io/en/stable>`__
for that release.

Since that release, the master (head of tree) version in GitHub has several new features and bug fixes.
Here is the master `latest documentation <https://hacs-pyscript.readthedocs.io/en/latest>`__.

Planned new features post 1.0.0 include:

- ``del`` can delete state variables and state variable attributes
- use ``aionofity`` to auto-reload newly written script files, at least on linux (#74)
- consider allowing native Python functions inside pyscript (#71)
- consider implementing function decorators (#43)

The new features since 1.0.0 in master include:

- Added ``state_hold_false=None`` optional period in seconds to ``@state_trigger`` and ``task.wait_until()``.
  This requires the trigger expression to be ``False`` for at least that period (including 0) before a
  successful trigger.  Proposed by @tchef69 (#89).

Bug fixes since 1.0.0 in master include:

- state setting now copies the attributes, to avoid a strange ``MappingProxyType`` recursion error
  inside HASS, reported by @github392 (#87).
- the deprecated function ``state.get_attr`` was missing an ``await``, which caused an exception; in 1.0.0 use
  ``state.getattr``, reported and fixed by @dlashua (#88).
