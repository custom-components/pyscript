# Pyscript: Python scripting integration

This is a custom component that adds rich Python scripting to Home Assistant.

You can write Python functions and scripts that implement a wide range of automation, logic and
triggers.  State variables are bound to Python variables and services are callable as Python
functions, so it's easy and concise to implement high-level logic.

Functions you write can be configured to be called as a service or run upon time, state-change or
event triggers. Functions can also call any service, fire events and set state variables.
Functions can sleep or wait for additional changes in state variables or events, without slowing or
affecting other operations. You can think of these functions as small programs that run in
parallel, independently of each other, and they could be active for extended periods of time.

Pyscript also interfaces with the Jupyter front-ends (eg, notebook, console and lab).  That allows
you to develop and test pyscript functions, triggers, automation and logic interactively.

## Installation

Under HACS -> Integrations, select "+", search for `pyscript` and install it.

See the documentation if you want to install pyscript manually.

## Configuration

* Go to the Integrations menu in the Home Assistant Configuration UI and add `Pyscript Python scripting` from there, or add `pyscript:` to `<config>/configuration.yaml`; see docs for optional parameters
* Add files with a suffix of `.py` in the folder `<config>/pyscript`.
* Whenever you change a script file, make a `reload` service call to `pyscript`.
* Watch the HASS log for `pyscript` errors and logger output from your scripts.
* Consider installing the optional Jupyter kernel, so you can use pyscript interactively.

## Useful Links

* [Documentation](https://hacs-pyscript.readthedocs.io/en/stable)
* [Using Jupyter](https://github.com/craigbarratt/hass-pyscript-jupyter)
* [Jupyter notebook tutorial](https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb)
* [GitHub repository](https://github.com/custom-components/pyscript) (please add a star if you like pyscript!)
* [Issues](https://github.com/custom-components/pyscript/issues)
* [Wiki](https://github.com/custom-components/pyscript/wiki)
