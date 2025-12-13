# Pyscript: Python Scripting for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/custom-components/pyscript.svg?style=for-the-badge)](https://github.com/custom-components/pyscript/releases)
[![License](https://img.shields.io/github/license/custom-components/pyscript.svg?style=for-the-badge)](LICENSE)
[![hacs](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Project Maintenance](https://img.shields.io/badge/maintainer-%40craigbarratt-blue.svg?style=for-the-badge)](https://github.com/craigbarratt)

This HACS custom integration allows you to write Python functions and scripts that can implement a
wide range of automation, logic and triggers. State variables are bound to Python variables and
services are callable as Python functions, so it's easy and concise to implement logic.

Functions you write can be configured to be called as a service or run upon time, state-change or
event triggers. Functions can also call any service, fire events and set state variables.  Functions
can sleep or wait for additional changes in state variables or events, without slowing or affecting
other operations. You can think of these functions as small programs that run in parallel,
independently of each other, and they could be active for extended periods of time.

Pyscript also provides a kernel that interfaces with the Jupyter front-ends (eg, notebook, console,
lab and VSCode). That allows you to develop and test pyscript code interactively. Plus you can interact
with much of HASS by looking at state variables, calling services etc.

Pyscript can also generate IDE stub modules by calling the `pyscript.generate_stubs` service.
See the “IDE Helpers” section of the docs for setup details.

## Documentation

Here is the [pyscript documentation](https://hacs-pyscript.readthedocs.io/en/stable).

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

* Go to the Integrations menu in the Home Assistant Configuration UI and add `Pyscript Python scripting` from there. Alternatively, add `pyscript:` to `<config>/configuration.yaml`; pyscript has two optional configuration parameters that allow any python package to be imported if set and to expose `hass` as a variable; both default to `false`:
    ```yaml
    pyscript:
      allow_all_imports: true
      hass_is_global: true
    ```
* Add files with a suffix of `.py` in the folder `<config>/pyscript`.
* Restart HASS.
* Whenever you change a script file, make a `reload` service call to `pyscript`.
* Watch the HASS log for `pyscript` errors and logger output from your scripts.

## Contributing

Contributions are welcome! You are encouraged to submit PRs, bug reports, feature requests or add to
the Wiki with examples and tutorials. It would be fun to hear about unique and clever applications
you develop. Please see this [README](https://github.com/custom-components/pyscript/tree/master/tests)
for setting up a development environment and running tests.

Even if you aren't a developer, please participate in our
[discussions community](https://github.com/custom-components/pyscript/discussions).
Helping other users is another great way to contribute to pyscript!

## Useful Links

* [Documentation stable](https://hacs-pyscript.readthedocs.io/en/stable): latest release
* [Documentation latest](https://hacs-pyscript.readthedocs.io/en/latest): current master in Github
* [Discussion and help](https://github.com/custom-components/pyscript/discussions)
* [Issues](https://github.com/custom-components/pyscript/issues)
* [Wiki](https://github.com/custom-components/pyscript/wiki)
* [GitHub repository](https://github.com/custom-components/pyscript) (please add a star if you like pyscript!)
* [Release notes](https://github.com/custom-components/pyscript/releases)
* [Jupyter notebook tutorial](https://nbviewer.jupyter.org/github/craigbarratt/hass-pyscript-jupyter/blob/master/pyscript_tutorial.ipynb)

## Copyright

Copyright (c) 2020-2025 Craig Barratt.  May be freely used and copied according to the terms of the
[Apache 2.0 License](LICENSE).
