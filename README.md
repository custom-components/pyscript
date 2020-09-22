# Pyscript: Python Scripting for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/custom-components/pyscript.svg?style=for-the-badge)](https://github.com/custom-components/pyscript/releases)
[![License](https://img.shields.io/github/license/custom-components/pyscript.svg?style=for-the-badge)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![Project Maintenance](https://img.shields.io/badge/maintainer-%40craigbarratt-blue.svg?style=for-the-badge)](https://github.com/craigbarratt)

This HACS custom integration allows you to write Python functions and scripts that can implement a
wide range of automation, logic and triggers. State variables are bound to Python variables and
services are callable as Python functions, so it's easy and concise to implement logic.

Functions you write can be configured to be called as a service or run upon time, state-change or
event triggers. Functions can also call any service, fire events and set state variables.  Functions
can sleep or wait for additional changes in state variables or events, without slowing or affecting
other operations. You can think of these functions as small programs that run in parallel,
independently of each other, and they could be active for extended periods of time.

Pyscript also provides a kernel that interfaces with the Jupyter front-ends (eg, notebook, console
and lab). That allows you to develop and test pyscript code interactively. Plus you can interact
with much of HASS by looking at state variables, calling services etc.

## Documentation

Here is the [pyscript documentation](https://hacs-pyscript.readthedocs.io/en/latest).

For more information about the Jupyter kernel, see the [README](https://github.com/craigbarratt/hass-pyscript-jupyter/blob/master/README.md).
There is also a [Jupyter notebook tutorial](https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb),
which can be downloaded and run interactively in Jupyter notebook connected to your live HASS with pyscript.

## Installation

### Option 1: HACS

Under HACS -> Integrations, select "+", search for `pyscript` and install it.

### Option 2: Manual

From the [latest release](https://github.com/custom-components/pyscript/releases) download the zip file `hass-custom-pyscript.zip`
```bash
cd YOUR_HASS_CONFIG_DIRECTORY    # same place as configuration.yaml
mkdir -p custom_components/pyscript
cd custom_components/pyscript
unzip hass-custom-pyscript.zip
```

Alternatively, you can install the current GitHub master version by cloning and copying:
```bash
mkdir SOME_LOCAL_WORKSPACE
cd SOME_LOCAL_WORKSPACE
git clone https://github.com/custom-components/pyscript.git
mkdir -p YOUR_HASS_CONFIG_DIRECTORY/custom_components
cp -pr pyscript/custom_components/pyscript YOUR_HASS_CONFIG_DIRECTORY/custom_components
```

### Install Jupyter Kernel

Installing the Pyscript Jupyter kernel is optional.  The steps to install and use it are in
this [README](https://github.com/craigbarratt/hass-pyscript-jupyter/blob/master/README.md).

## Configuration

* Add `pyscript:` to `<config>/configuration.yaml`; pyscript has one optional
configuration parameter that allows any python package to be imported if set, eg:
```yaml
pyscript:
  allow_all_imports: true
```
* Create the folder `<config>/pyscript`
* Add files with a suffix of `.py` in the folder `<config>/pyscript`.
* Restart HASS.
* Whenever you change a script file, make a `reload` service call to `pyscript`.
* Watch the HASS log for `pyscript` errors and logger output from your scripts.

## Contributing

Contributions are welcome! You are encouraged to submit PRs, bug reports, feature requests or add to
the Wiki with examples and tutorials. It would be fun to hear about unique and clever applications
you develop. Please see this [README](https://github.com/custom-components/pyscript/tree/master/tests)
for setting up a development environment and running tests.

## Useful Links

* [Documentation](https://hacs-pyscript.readthedocs.io/en/latest)
* [Issues](https://github.com/custom-components/pyscript/issues)
* [Wiki](https://github.com/custom-components/pyscript/wiki)
* [GitHub repository](https://github.com/custom-components/pyscript) (please add a star if you like pyscript!)
* [Jupyter notebook tutorial](https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb)

## Copyright

Copyright (c) 2020 Craig Barratt.  May be freely used and copied according to the terms of the
[Apache 2.0 License](LICENSE).
