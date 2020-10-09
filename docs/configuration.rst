Configuration
=============

-  Go to the Integrations menu in the Home Assistant Configuration UI and add
   ``Pyscript Python scripting`` from there, or add ``pyscript:`` to 
   ``<config>/configuration.yaml``; pyscript has one optional configuration
   parameter that allows any python package to be imported if set, eg:

   .. code:: yaml

      pyscript:
        allow_all_imports: true

-  Add files with a suffix of ``.py`` in the folder ``<config>/pyscript``.
-  Restart HASS.
-  Whenever you change a script file, make a ``reload`` service call to ``pyscript``.
-  Watch the HASS log for ``pyscript`` errors and logger output from your scripts.
