Configuration
=============

-  Add ``pyscript:`` to ``<config>/configuration.yaml``; pyscript has
   one optional configuration parameter that allows any python package
   to be imported if set, eg:

   .. code:: yaml

      pyscript:
        allow_all_imports: true

-  Create the folder ``<config>/pyscript``
-  Add files with a suffix of ``.py`` in the folder ``<config>/pyscript``.
-  Restart HASS.
-  Whenever you change a script file, make a ``reload`` service call to ``pyscript``.
-  Watch the HASS log for ``pyscript`` errors and logger output from your scripts.
