"""Test the pyscript apps, modules and import features."""

from ast import literal_eval
import asyncio
import re

from custom_components.pyscript.const import DOMAIN, FOLDER
from mock_open import MockOpen
from pytest_homeassistant_custom_component.async_mock import patch

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


async def test_service_exists(hass, caplog):
    """Test importing a pyscript module."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/hello.py": """
import xyz2
from xyz2 import f_minus

@service
def func1():
    pyscript.done = [xyz2.f_add(1, 2), xyz2.f_mult(3, 4), xyz2.f_add(10, 20), f_minus(50, 20)]
""",
        #
        # this will fail to load since import doesn't exist
        #
        f"{conf_dir}/bad_import.py": """
import no_such_package

@service
def func10():
    pass
""",
        #
        # this will fail to load since import has a syntax error
        #
        f"{conf_dir}/bad_import2.py": """
import bad_module

@service
def func11():
    pass
""",
        #
        # This will load, since there is an apps/world config entry
        #
        f"{conf_dir}/apps/world.py": """
from xyz2 import *

@service
def func2():
    pyscript.done = [get_x(), get_name(), other_name(), f_add(1, 5), f_mult(3, 6), f_add(10, 30), f_minus(50, 30)]
""",
        #
        # This will not load, since there is no apps/world2 config entry
        #
        f"{conf_dir}/apps/world2.py": """
from xyz2 import *

@service
def func10():
    pass
""",
        f"{conf_dir}/modules/xyz2/__init__.py": """
from .other import f_minus, other_name

x = 99

def f_add(a, b):
    return a + b

def f_mult(a, b):
    return a * b

def get_x():
    return x

def get_name():
    return __name__
""",
        f"{conf_dir}/modules/xyz2/other.py": """
def f_minus(a, b):
    return a - b

def other_name():
    return __name__
""",
        #
        # this module has a syntax error (missing :)
        #
        f"{conf_dir}/modules/bad_module.py": """
def func12()
    pass
""",
    }

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        print(f"glob_side_effect: path={path}, path_re={path_re}, result={result}")
        return result

    conf = {"apps": {"world": {}}}
    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob"
    ) as mock_glob, patch("custom_components.pyscript.global_ctx.open", mock_open), patch(
        "homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}
    ), patch(
        "os.path.isfile"
    ) as mock_isfile:
        mock_isfile.side_effect = isfile_side_effect
        mock_glob.side_effect = glob_side_effect
        assert await async_setup_component(hass, "pyscript", {DOMAIN: conf})

    notify_q = asyncio.Queue(0)

    async def state_changed(event):
        var_name = event.data["entity_id"]
        if var_name != "pyscript.done":
            return
        value = event.data["new_state"].state
        await notify_q.put(value)

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)

    assert not hass.services.has_service("pyscript", "func10")
    assert not hass.services.has_service("pyscript", "func11")
    assert not hass.services.has_service("pyscript", "func12")

    await hass.services.async_call("pyscript", "func1", {})
    ret = await wait_until_done(notify_q)
    assert literal_eval(ret) == [1 + 2, 3 * 4, 10 + 20, 50 - 20]

    await hass.services.async_call("pyscript", "func2", {})
    ret = await wait_until_done(notify_q)
    assert literal_eval(ret) == [99, "xyz2", "xyz2.other", 1 + 5, 3 * 6, 10 + 30, 50 - 30]

    assert "ModuleNotFoundError: import of no_such_package not allowed" in caplog.text
    assert "SyntaxError: invalid syntax (bad_module.py, line 2)" in caplog.text
