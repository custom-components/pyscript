"""Test the pyscript apps, modules and import features."""

import asyncio
import re

from custom_components.pyscript.const import DOMAIN, FOLDER
from mock_open import MockOpen
from pytest_homeassistant_custom_component.async_mock import patch

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def test_reload(hass, caplog):
    """Test reload a pyscript module."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/hello.py": """
import xyz2
from xyz2 import xyz

log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()} xyz={xyz} xyz2.xyz={xyz2.xyz}")

@service
def func1():
    pass
""",
        #
        # This will load, since there is an apps/world config entry
        #
        f"{conf_dir}/apps/world.py": """
from xyz2 import *

log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()} xyz={xyz}")

@service
def func2():
    pass
""",
        #
        # This will load, since there is an apps/world2 config entry
        #
        f"{conf_dir}/apps/world2/__init__.py": """
from .other import *

log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()} var1={pyscript.config['apps']['world2']['var1']}, other_abc={other_abc}")

@service
def func3():
    pass
""",
        f"{conf_dir}/apps/world2/other.py": """
other_abc = 987

log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()}")
""",
        f"{conf_dir}/modules/xyz2/__init__.py": """
from .other import xyz

log.info(f"modules/xyz2 global_ctx={pyscript.get_global_ctx()};")
""",
        f"{conf_dir}/modules/xyz2/other.py": """
log.info(f"modules/xyz2/other global_ctx={pyscript.get_global_ctx()};")

xyz = 123
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
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    conf = {"apps": {"world": {}, "world2": {"var1": 100}}}
    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob"
    ) as mock_glob, patch("custom_components.pyscript.global_ctx.open", mock_open), patch(
        "custom_components.pyscript.open", mock_open
    ), patch(
        "homeassistant.util.yaml.loader.open", mock_open
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.os.path.isfile"
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

        assert hass.services.has_service("pyscript", "func1")
        assert hass.services.has_service("pyscript", "func2")
        assert hass.services.has_service("pyscript", "func3")

        assert "modules/xyz2 global_ctx=modules.xyz2.__init__;" in caplog.text
        assert "modules/xyz2/other global_ctx=modules.xyz2.other;" in caplog.text
        assert "hello global_ctx=file.hello xyz=123 xyz2.xyz=123" in caplog.text
        assert "world2.other global_ctx=apps.world2.other" in caplog.text
        assert "world2.__init__ global_ctx=apps.world2.__init__ var1=100, other_abc=987" in caplog.text

        #
        # add a new script file
        #
        file_contents[
            f"{conf_dir}/hello2.py"
        ] = """
log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()};")

@service
def func20():
    pass
"""
        mock_open[f"{conf_dir}/hello2.py"].read_data = file_contents[f"{conf_dir}/hello2.py"]

        #
        # should not load the new script if we reload something else
        #
        await hass.services.async_call("pyscript", "reload", {"global_ctx": "file.hello"}, blocking=True)
        assert not hass.services.has_service("pyscript", "func20")
        assert "hello2 global_ctx=file.hello2;" not in caplog.text

        #
        # should load new file
        #
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
        assert hass.services.has_service("pyscript", "func20")
        assert "hello2 global_ctx=file.hello2;" in caplog.text

        #
        # delete the script file
        #
        del file_contents[f"{conf_dir}/hello2.py"]

        #
        # should not delete the script file if we reload something else
        #
        await hass.services.async_call("pyscript", "reload", {"global_ctx": "file.hello"}, blocking=True)
        assert hass.services.has_service("pyscript", "func20")

        #
        # should delete the script file
        #
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
        assert not hass.services.has_service("pyscript", "func20")

        #
        # change a module file and confirm the parent script is reloaded too
        #
        file_contents[
            f"{conf_dir}/modules/xyz2/other.py"
        ] = """
log.info(f"modules/xyz2/other global_ctx={pyscript.get_global_ctx()};")

xyz = 456
"""
        mock_open[f"{conf_dir}/modules/xyz2/other.py"].read_data = file_contents[
            f"{conf_dir}/modules/xyz2/other.py"
        ]

        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
        assert "hello global_ctx=file.hello xyz=456 xyz2.xyz=456" in caplog.text

        #
        # change the app config
        #
        conf["apps"]["world2"]["var1"] = 200
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
        assert "world2.__init__ global_ctx=apps.world2.__init__ var1=200, other_abc=987" in caplog.text

        #
        # change a module inside an app
        #
        file_contents[
            f"{conf_dir}/apps/world2/other.py"
        ] = """
other_abc = 654

log.info(f"{__name__} global_ctx={pyscript.get_global_ctx()}")
"""
        mock_open[f"{conf_dir}/apps/world2/other.py"].read_data = file_contents[
            f"{conf_dir}/apps/world2/other.py"
        ]
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
        assert "world2.__init__ global_ctx=apps.world2.__init__ var1=200, other_abc=654" in caplog.text

        #
        # now confirm certain files reloaded the correct number of times,
        # and reload everything a few times
        #
        for i in range(3):
            assert caplog.text.count("world global_ctx=apps.world xyz=") == 2 + i
            assert caplog.text.count("world2.__init__ global_ctx=apps.world2.__init__ var1=") == 3 + i
            assert caplog.text.count("hello global_ctx=file.hello xyz=") == 4 + i
            assert caplog.text.count("modules/xyz2/other global_ctx=modules.xyz2.other") == 2 + i
            assert caplog.text.count("modules/xyz2 global_ctx=modules.xyz2.__init__") == 2 + i
            if i < 2:
                await hass.services.async_call("pyscript", "reload", {"global_ctx": "*"}, blocking=True)
