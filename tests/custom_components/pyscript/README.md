# Pyscript tests

These tests can be run from a clone of the `core` Home Assistant repository:
```bash
mkdir -p SOME_WORKING_DIR/homeassistant-dev
cd SOME_WORKING_DIR/homeassistant-dev
git clone https://github.com/home-assistant/core.git
cd core
script/setup
source venv/bin/activate
```

Install `pyscript` manually in `config/custom_components/pyscript` (see docs).
Alternatively, you can use the UI after starting `hass` to install HACS and pyscript:
```bash
hass -c config
```
Quit `hass` after install if you use that method.

Next, add `pyscript:` to `config/configuration.yaml`.

Copy the files in `tests/custom_components/pyscript` from this repository into `tests/custom_components/pyscript` in the current directory.  The tests should now run using this command:
```bash
tox -e py38 -- tests/custom_components/pyscript
```
