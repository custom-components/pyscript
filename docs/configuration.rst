Configuration
=============

- Pyscript can be configured using the UI, or via yaml. To use the UI, go to the
  Configuration -> Integrations page and selection "+" to add ``Pyscript Python scripting``.
  After that, you can change the settings anytime by selecting Options under Pyscript
  in the Configuration page.

  Alternatively, for yaml configuration, add ``pyscript:`` to ``<config>/configuration.yaml``.
  Pyscript has three optional configuration parameters that allow any python package to be
  imported, expose the ``hass`` variable as a global, and temporarily switch back to the
  legacy decorator subsystem (all three options default to ``false``):

  .. code:: yaml

     pyscript:
       allow_all_imports: true
       hass_is_global: true
       legacy_decorators: true

  Starting with version ``2.0.0``, pyscript uses the new decorator subsystem by default.
  If you run into a problem in the new implementation, you can temporarily set
  ``legacy_decorators: true`` to switch back to the legacy subsystem. If you do that,
  please also file a bug report in the `GitHub issue tracker <https://github.com/custom-components/pyscript/issues>`__
  so the problem can be fixed.

- Add files with a suffix of ``.py`` in the folder ``<config>/pyscript``.
- Restart HASS after installing pyscript.
- Whenever you change a script file or app, pyscript will automatically reload the changed files.
  To reload all files and apps, call the ``pyscript.reload`` service with the optional
  ``global_ctx`` parameter to ``*``.
- Watch the HASS log for ``pyscript`` errors and logger output from your scripts.
