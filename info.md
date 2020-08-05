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
 
## Installation

Under HACS -> Integrations, select "+", search for `pyscript` and install it.

See the documentation if you want to install manually or install the latest unreleased version.

## Configuration

* Add `pyscript:` to `<config>/configuration.yaml`; pyscript doesn't have any configuration settings
* Create the folder `<config>/pyscript`
* Add files with a suffix of `.py` in the folder `<config>/pyscript`.
* Whenever you change a script file, make a `reload` service call to `pyscript`.
* Watch the HASS log for `pyscript` errors and logger output from your scripts.

## Useful Links

* [Documentation](https://github.com/custom-components/pyscript)
* [Issues](https://github.com/custom-components/pyscript/issues)
* [Wiki](https://github.com/custom-components/pyscript/wiki)
* [GitHub repository](https://github.com/custom-components/pyscript) (please add a star if you like `pyscript`!)
