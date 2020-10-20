Configuration
=============

-  Pyscript can be configured using the UI, or via yaml. To use the UI, go to the
   Configuration -> Integrations page and selection "+" to add ``Pyscript Python scripting``.
   After that, you can change the settings anytime by selecting Options under Pyscript
   in the Configuration page.

   Alternatively, for yaml configuration, add ``pyscript:`` to ``<config>/configuration.yaml``.
   Pyscript has two optional configuration parameters that allow any python package to be
   imported and exposes the ``hass`` variable as a global (both options default to ``false``):

   .. code:: yaml

      pyscript:
        allow_all_imports: true
        hass_is_global: true

-  Add files with a suffix of ``.py`` in the folder ``<config>/pyscript``.
-  Restart HASS after installing pyscript.
-  Whenever you change a script file or configuration, make a ``reload`` service call to ``pyscript``.
-  Watch the HASS log for ``pyscript`` errors and logger output from your scripts.
