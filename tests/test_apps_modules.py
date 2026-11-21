"""Test the pyscript apps, modules and import features."""

from ast import literal_eval
import asyncio
from contextlib import ExitStack
import re
from unittest.mock import patch

from mock_open import MockOpen
import pytest

from custom_components.pyscript.const import DOMAIN, FOLDER
from custom_components.pyscript.global_ctx import GlobalContextMgr
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


@pytest.mark.asyncio
async def test_service_exists(hass, caplog):
    """Test importing a pyscript module."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/hello.py": """
import xyz2
from xyz2 import f_minus

@service(supports_response = "optional")
def func1():
    pyscript.done = [xyz2.f_add(1, 2), xyz2.f_mult(3, 4), xyz2.f_add(10, 20), f_minus(50, 20)]
    return {"a": 1}
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
def func20():
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

log.info(f"modules/xyz2 global_ctx={pyscript.get_global_ctx()};")

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
        #
        # this script file should auto-load
        #
        f"{conf_dir}/scripts/func13.py": """
@service
def func13():
    pass
""",
        #
        # this script file should auto-load
        #
        f"{conf_dir}/scripts/a/b/c/d/func14.py": """
@service
def func14():
    pass

log.info(f"func14 global_ctx={pyscript.get_global_ctx()};")

""",
        #
        # this script file should not auto-load
        #
        f"{conf_dir}/scripts/a/b/c/d/#func15.py": """
@service
def func15():
    pass
""",
        #
        # this script file should not auto-load
        #
        f"{conf_dir}/scripts/#a/b/c/d/func15.py": """
@service
def func15():
    pass
""",
    }

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None, root_dir=None, dir_fd=None, include_hidden=False):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    conf = {"apps": {"world": {}}}
    with (
        patch("custom_components.pyscript.os.path.isdir", return_value=True),
        patch("custom_components.pyscript.glob.iglob") as mock_glob,
        patch("custom_components.pyscript.global_ctx.open", mock_open),
        patch("custom_components.pyscript.open", mock_open),
        patch("homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}),
        patch("custom_components.pyscript.watchdog_start", return_value=None),
        patch("custom_components.pyscript.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os_path_isfile") as mock_isfile,
    ):
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

    await hass.async_start()
    await hass.async_block_till_done()

    assert not hass.services.has_service("pyscript", "func10")
    assert not hass.services.has_service("pyscript", "func11")
    assert hass.services.has_service("pyscript", "func1")
    assert hass.services.has_service("pyscript", "func13")
    assert hass.services.has_service("pyscript", "func14")
    assert not hass.services.has_service("pyscript", "func15")

    service_ret = await hass.services.async_call(
        "pyscript", "func1", {}, return_response=True, blocking=True
    )
    ret = await wait_until_done(notify_q)
    assert literal_eval(ret) == [1 + 2, 3 * 4, 10 + 20, 50 - 20]
    assert service_ret == {"a": 1}

    await hass.services.async_call("pyscript", "func20", {})
    ret = await wait_until_done(notify_q)
    assert literal_eval(ret) == [99, "xyz2", "xyz2.other", 1 + 5, 3 * 6, 10 + 30, 50 - 30]

    assert "modules/xyz2 global_ctx=modules.xyz2;" in caplog.text
    assert "func14 global_ctx=scripts.a.b.c.d.func14;" in caplog.text
    assert "ModuleNotFoundError: import of no_such_package not allowed" in caplog.text
    assert "SyntaxError: expected ':'" in caplog.text
    assert 'modules/bad_module.py", line 2' in caplog.text


@pytest.mark.asyncio
async def test_app_subfolder_relative_imports(hass, caplog):
    """Test relative imports between modules in an app's subfolders, from #726."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/apps/myapp/__init__.py": """
from .subfolder.a import foo
from .subfolder.b import bar, baz
from .subfolder.deep.c import qux

@service(supports_response="only")
def myapp_func():
    return {"done": [foo(), bar(), baz(), qux()]}
""",
        f"{conf_dir}/apps/myapp/subfolder/a.py": """
def foo():
    return "foo"
""",
        #
        # a plain module in a subfolder importing its sibling
        #
        f"{conf_dir}/apps/myapp/subfolder/b.py": """
from .a import foo

def bar():
    return "bar" + foo()

def baz():
    return __name__
""",
        #
        # a deeper subfolder importing from the parent subfolder
        #
        f"{conf_dir}/apps/myapp/subfolder/deep/c.py": """
from ..a import foo
from .. import b

def qux():
    return foo() + b.bar()
""",
    }

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None, root_dir=None, dir_fd=None, include_hidden=False):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    conf = {"apps": {"myapp": {}}}

    def file_patches(mtime):
        stack = ExitStack()
        for this_patch in (
            patch("custom_components.pyscript.os.path.isdir", return_value=True),
            patch("custom_components.pyscript.glob.iglob", side_effect=glob_side_effect),
            patch("custom_components.pyscript.global_ctx.open", mock_open),
            patch("custom_components.pyscript.open", mock_open),
            patch("homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}),
            patch("custom_components.pyscript.watchdog_start", return_value=None),
            patch("custom_components.pyscript.os.path.getmtime", return_value=mtime),
            patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=mtime),
            patch("custom_components.pyscript.global_ctx.os_path_isfile", side_effect=isfile_side_effect),
        ):
            stack.enter_context(this_patch)
        return stack

    with file_patches(1000):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: conf})

    await hass.async_start()
    await hass.async_block_till_done()

    expected = {"done": ["foo", "barfoo", "myapp.subfolder.b", "foobarfoo"]}

    def ctx_names():
        return sorted(name for name, _ in GlobalContextMgr.items())

    assert hass.services.has_service("pyscript", "myapp_func")
    ret = await hass.services.async_call("pyscript", "myapp_func", {}, return_response=True, blocking=True)
    assert ret == expected

    #
    # no phantom contexts like apps.myapp.subfolder.b.a, so a reload keeps everything
    #
    names = ctx_names()
    assert names == [
        "apps.myapp",
        "apps.myapp.subfolder.a",
        "apps.myapp.subfolder.b",
        "apps.myapp.subfolder.deep.c",
    ]

    with file_patches(2000):
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
    await hass.async_block_till_done()

    assert ctx_names() == names
    assert hass.services.has_service("pyscript", "myapp_func")
    ret = await hass.services.async_call("pyscript", "myapp_func", {}, return_response=True, blocking=True)
    assert ret == expected


@pytest.mark.asyncio
async def test_app_subfolder_legacy_relative_imports(hass, caplog):
    """Test that the old form of relative imports in an app's subfolders still works, with a warning."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/apps/myapp/__init__.py": """
from .subfolder.a import foo
from .subfolder.b import bar, baz
from .subfolder.deep.c import qux

@service(supports_response="only")
def myapp_func():
    return {"done": [foo(), bar(), baz(), qux()]}
""",
        f"{conf_dir}/apps/myapp/subfolder/a.py": """
def foo():
    return "foo"
""",
        #
        # a plain module in a subfolder importing its sibling the old way, relative to
        # the folder of the package that imported it
        #
        f"{conf_dir}/apps/myapp/subfolder/b.py": """
from .subfolder.a import foo

def bar():
    return "bar" + foo()

def baz():
    return __name__
""",
        #
        # a deeper subfolder importing from the parent subfolder
        #
        f"{conf_dir}/apps/myapp/subfolder/deep/c.py": """
from ..a import foo
from .. import b

def qux():
    return foo() + b.bar()
""",
    }

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None, root_dir=None, dir_fd=None, include_hidden=False):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    conf = {"apps": {"myapp": {}}}

    def file_patches(mtime):
        stack = ExitStack()
        for this_patch in (
            patch("custom_components.pyscript.os.path.isdir", return_value=True),
            patch("custom_components.pyscript.glob.iglob", side_effect=glob_side_effect),
            patch("custom_components.pyscript.global_ctx.open", mock_open),
            patch("custom_components.pyscript.open", mock_open),
            patch("homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}),
            patch("custom_components.pyscript.watchdog_start", return_value=None),
            patch("custom_components.pyscript.os.path.getmtime", return_value=mtime),
            patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=mtime),
            patch("custom_components.pyscript.global_ctx.os_path_isfile", side_effect=isfile_side_effect),
        ):
            stack.enter_context(this_patch)
        return stack

    def old_rules_warnings():
        return [rec.getMessage() for rec in caplog.records if "only with the old rules" in rec.getMessage()]

    warning = (
        "apps.myapp.subfolder.b: the relative import .subfolder.a was found only with the old rules, "
        "which are deprecated and will be removed in a future release; relative imports now start "
        "from the importing file's own folder, as in Python, so write it as .a"
    )

    with file_patches(1000):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: conf})

    await hass.async_start()
    await hass.async_block_till_done()

    expected = {"done": ["foo", "barfoo", "myapp.subfolder.b", "foobarfoo"]}

    def ctx_names():
        return sorted(name for name, _ in GlobalContextMgr.items())

    assert hass.services.has_service("pyscript", "myapp_func")
    ret = await hass.services.async_call("pyscript", "myapp_func", {}, return_response=True, blocking=True)
    assert ret == expected

    #
    # only the old-form import is warned about, and both forms load the same contexts,
    # so there are no phantom contexts like apps.myapp.subfolder.b.subfolder.a
    #
    assert old_rules_warnings() == [warning]
    names = ctx_names()
    assert names == [
        "apps.myapp",
        "apps.myapp.subfolder.a",
        "apps.myapp.subfolder.b",
        "apps.myapp.subfolder.deep.c",
    ]

    caplog.clear()
    with file_patches(2000):
        await hass.services.async_call("pyscript", "reload", {}, blocking=True)
    await hass.async_block_till_done()

    assert ctx_names() == names
    assert old_rules_warnings() == [warning]
    assert hass.services.has_service("pyscript", "myapp_func")
    ret = await hass.services.async_call("pyscript", "myapp_func", {}, return_response=True, blocking=True)
    assert ret == expected


@pytest.mark.asyncio
async def test_relative_import_context_names(hass, caplog):
    """Test that relative imports name each module's context after its file, with either rules."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/apps/myapp/__init__.py": """
from . import a
from .sub import sub_names
from .lib.m import m_names
import other

@service(supports_response="only")
def myapp_func():
    return {"done": [__name__, a.name(), sub_names(), m_names(), other.names()]}
""",
        f"{conf_dir}/apps/myapp/a.py": """
def name():
    return __name__
""",
        #
        # a subpackage, importing from its own folder and the folder above it; it
        # loads lib/m.py first, with the old rules relative to apps/myapp
        #
        f"{conf_dir}/apps/myapp/sub/__init__.py": """
from .s import name as s_name
from ..a import name as a_name
from ..lib.m import m_names

def sub_names():
    return [__name__, s_name(), a_name()]
""",
        f"{conf_dir}/apps/myapp/sub/s.py": """
def name():
    return __name__
""",
        #
        # imports the old way; lib/p.py is found only with the old rules, and itself
        # imports the new way
        #
        f"{conf_dir}/apps/myapp/lib/m.py": """
from .lib.n import name as n_name
from .lib.p import p_names

def m_names():
    return [__name__, n_name(), p_names()]
""",
        f"{conf_dir}/apps/myapp/lib/n.py": """
def name():
    return __name__
""",
        f"{conf_dir}/apps/myapp/lib/p.py": """
from .n import name as n_name

def p_names():
    return [__name__, n_name()]
""",
        #
        # a plain module in the apps folder, imported by name, imports from apps/other/
        #
        f"{conf_dir}/apps/other.py": """
from .y import name as y_name

def names():
    return [__name__, y_name()]
""",
        f"{conf_dir}/apps/other/y.py": """
def name():
    return __name__
""",
        #
        # an import that neither rule finds fails as before, without a warning
        #
        f"{conf_dir}/apps/badapp/__init__.py": """
from .sub.b import x
""",
        f"{conf_dir}/apps/badapp/sub/b.py": """
from .missing import x
""",
    }

    mock_open = MockOpen()
    for key, value in file_contents.items():
        mock_open[key].read_data = value

    def isfile_side_effect(arg):
        return arg in file_contents

    def glob_side_effect(path, recursive=None, root_dir=None, dir_fd=None, include_hidden=False):
        result = []
        path_re = path.replace("*", "[^/]*").replace(".", "\\.")
        path_re = path_re.replace("[^/]*[^/]*/", ".*")
        for this_path in file_contents:
            if re.match(path_re, this_path):
                result.append(this_path)
        return result

    conf = {"apps": {"myapp": {}, "badapp": {}}}

    with (
        patch("custom_components.pyscript.os.path.isdir", return_value=True),
        patch("custom_components.pyscript.glob.iglob", side_effect=glob_side_effect),
        patch("custom_components.pyscript.global_ctx.open", mock_open),
        patch("custom_components.pyscript.open", mock_open),
        patch("homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}),
        patch("custom_components.pyscript.watchdog_start", return_value=None),
        patch("custom_components.pyscript.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000),
        patch("custom_components.pyscript.global_ctx.os_path_isfile", side_effect=isfile_side_effect),
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: conf})

    await hass.async_start()
    await hass.async_block_till_done()

    assert hass.services.has_service("pyscript", "myapp_func")
    ret = await hass.services.async_call("pyscript", "myapp_func", {}, return_response=True, blocking=True)
    assert ret == {
        "done": [
            "myapp",
            "myapp.a",
            ["myapp.sub", "myapp.sub.s", "myapp.a"],
            ["myapp.lib.m", "myapp.lib.n", ["myapp.lib.p", "myapp.lib.n"]],
            ["other", "other.y"],
        ]
    }
    assert sorted(name for name, _ in GlobalContextMgr.items()) == [
        "apps.myapp",
        "apps.myapp.a",
        "apps.myapp.lib.m",
        "apps.myapp.lib.n",
        "apps.myapp.lib.p",
        "apps.myapp.sub",
        "apps.myapp.sub.s",
        "apps.other",
        "apps.other.y",
    ]

    warnings = [rec.getMessage() for rec in caplog.records if "only with the old rules" in rec.getMessage()]
    assert len(warnings) == 2
    assert warnings[0].startswith("apps.myapp.lib.m: the relative import .lib.n was found only")
    assert warnings[0].endswith("so write it as .n")
    assert warnings[1].startswith("apps.myapp.lib.m: the relative import .lib.p was found only")
    assert warnings[1].endswith("so write it as .p")
    assert "ModuleNotFoundError: import from missing not allowed" in caplog.text
