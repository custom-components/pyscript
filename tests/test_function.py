"""Test the pyscript component."""

from ast import literal_eval
import asyncio
from datetime import datetime as dt
import re
from unittest.mock import MagicMock, Mock, patch

from mock_open import MockOpen
import pytest

from custom_components.pyscript import trigger
from custom_components.pyscript.const import CONF_ALLOW_ALL_IMPORTS, CONF_HASS_IS_GLOBAL, DOMAIN, FOLDER
from custom_components.pyscript.function import Function
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.core import Context
from homeassistant.setup import async_setup_component


@pytest.fixture()
def ast_functions():
    """Test for ast function name completion."""
    return {
        "domain_ast.func_name": lambda ast_ctx: ast_ctx.func(),
        "domain_ast.other_func": lambda ast_ctx: ast_ctx.func(),
    }


@pytest.fixture
def functions():
    """Test for regular function name completion."""
    mock_func = Mock()

    return {
        "domain.func_1": mock_func,
        "domain.func_2": mock_func,
        "helpers.get_today": mock_func,
        "helpers.entity_id": mock_func,
    }


@pytest.fixture
def services():
    """Test for services name completion."""
    return {
        "domain": {"turn_on": None, "turn_off": None, "toggle": None},
        "helpers": {"set_state": None, "restart": None},
    }


def test_install_ast_funcs(ast_functions):  # pylint: disable=redefined-outer-name
    """Test installing ast functions."""
    ast_ctx = MagicMock()
    ast_ctx.func.return_value = "ok"

    with patch.object(Function, "ast_functions", ast_functions):
        Function.install_ast_funcs(ast_ctx)
        assert len(ast_ctx.method_calls) == 3


@pytest.mark.parametrize(
    "root,expected",
    [
        ("helpers", {"helpers.entity_id", "helpers.get_today"}),
        ("domain", {"domain.func_2", "domain_ast.func_name", "domain_ast.other_func", "domain.func_1"}),
        ("domain_", {"domain_ast.func_name", "domain_ast.other_func"}),
        ("domain_ast.func", {"domain_ast.func_name"}),
        ("no match", set()),
    ],
    ids=lambda x: x if not isinstance(x, set) else f"set({len(x)})",
)
@pytest.mark.asyncio
async def test_func_completions(
    ast_functions, functions, root, expected
):  # pylint: disable=redefined-outer-name
    """Test function name completion."""
    with patch.object(Function, "ast_functions", ast_functions), patch.object(
        Function, "functions", functions
    ):
        words = await Function.func_completions(root)
        assert words == expected


@pytest.mark.parametrize(
    "root,expected",
    [
        ("do", {"domain"}),
        ("domain.t", {"domain.toggle", "domain.turn_on", "domain.turn_off"}),
        ("domain.turn", {"domain.turn_on", "domain.turn_off"}),
        ("helpers.set", {"helpers.set_state"}),
        ("no match", set()),
    ],
    ids=lambda x: x if not isinstance(x, set) else f"set({len(x)})",
)
@pytest.mark.asyncio
async def test_service_completions(root, expected, hass, services):  # pylint: disable=redefined-outer-name
    """Test service name completion."""
    with patch.object(hass.services, "async_services", return_value=services), patch.object(
        Function, "hass", hass
    ):
        words = await Function.service_completions(root)
        assert words == expected


async def setup_script(hass, notify_q, notify_q2, now, source, config=None):
    """Initialize and load the given pyscript."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {f"{conf_dir}/hello.py": source}

    Function.hass = None

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

    if not config:
        config = {DOMAIN: {CONF_ALLOW_ALL_IMPORTS: True}}
    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob"
    ) as mock_glob, patch("custom_components.pyscript.global_ctx.open", mock_open), patch(
        "custom_components.pyscript.trigger.dt_now", return_value=now
    ), patch(
        "custom_components.pyscript.open", mock_open
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value=config
    ), patch(
        "custom_components.pyscript.install_requirements",
        return_value=None,
    ), patch(
        "custom_components.pyscript.watchdog_start", return_value=None
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.os.path.isfile"
    ) as mock_isfile:
        mock_isfile.side_effect = isfile_side_effect
        mock_glob.side_effect = glob_side_effect
        assert await async_setup_component(hass, "pyscript", config)

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the given list of times in now.
    #
    def return_next_time():
        nonlocal now
        if isinstance(now, list):
            if len(now) > 1:
                return now.pop(0)
            return now[0]
        return now

    trigger.__dict__["dt_now"] = return_next_time

    if notify_q or notify_q2:

        async def state_changed(event):
            var_name = event.data["entity_id"]
            if var_name == "pyscript.done":
                value = event.data["new_state"].state
                if notify_q:
                    await notify_q.put(value)
            if var_name == "pyscript.done2":
                value = event.data["new_state"].state
                if notify_q2:
                    await notify_q2.put(value)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


@pytest.mark.asyncio
async def test_state_trigger(hass, caplog):
    """Test state trigger."""
    notify_q = asyncio.Queue(0)
    notify_q2 = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        notify_q2,
        [dt(2020, 7, 1, 10, 59, 59, 999998)],
        """

from math import sqrt
from homeassistant.core import Context
import asyncio

seq_num = 0

@time_trigger("startup", kwargs={"var1": 123})
def func_startup_sync(trigger_type=None, trigger_time=None, var1=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}, var1 = {var1}")
    pyscript.done = seq_num

@state_trigger("pyscript.f1var1 == '1'", state_check_now=True, state_hold_false=0, kwargs={"var1": 321})
def func1(var_name=None, value=None, var1=None):
    global seq_num

    seq_num += 1
    log.info(f"func1 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, int(value), sqrt(1024), __name__, var1]

@state_trigger("pyscript.f1var1 == '1'", "pyscript.f2var2 == '2'")
@state_trigger("pyscript.no_such_var == '10'", "pyscript.no_such_var.attr == 100")
@state_active("pyscript.f2var3 == '3' and pyscript.f2var4 == '4'")
def func2(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func2 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, int(value), sqrt(4096)]

#
# trigger every time int(pyscript.f1var1) >= 11 and twice if it's 13;
# use a lock since two tasks will run at almost the same time
#
func2a_lock = asyncio.Lock()

@state_trigger("int(pyscript.f1var1) >= 11", state_hold_false=None)
@state_trigger("int(pyscript.f1var1) == 13")
def func2a(var_name=None, value=None):
    global seq_num

    async with func2a_lock:
        seq_num += 1
        log.info(f"func2a var = {var_name}, value = {value}")
        pyscript.done = [seq_num, var_name, int(value)]

#
# just trigger the first time int(pyscript.f1var1) >= 10
#
@state_trigger("int(pyscript.f1var1) >= 10", state_hold_false=0)
def func2b(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func2b var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, int(value)]

#
# this should never trigger
#
@state_trigger("int(pyscript.f1var1) >= 10", state_hold_false=1000, state_check_now=True)
def func2c(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func2c var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, int(value)]

@state_trigger("pyscript.f1var2.old == '15'")
def func2d(var_name=None, value=None, old_value=None):
    global seq_num

    seq_num += 1
    log.info(f"func2d var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, int(value), int(old_value)]


@event_trigger("fire_event")
def fire_event(**kwargs):
    context = Context(user_id="1234", parent_id="5678", id="8901")
    event.fire(kwargs["new_event"], arg1=kwargs["arg1"], arg2=kwargs["arg2"], context=context)

@event_trigger("test_event3", "arg1 == 20 and arg2 == 131")
@event_trigger("test_event3", "arg1 == 20 and arg2 == 30", kwargs=dict(var1=987, var2='test'))
def func3(trigger_type=None, event_type=None, **kwargs):
    global seq_num

    seq_num += 1
    log.info(f"func3 trigger_type = {trigger_type}, event_type = {event_type}, event_data = {kwargs}")
    exec_test = task.executor(sum, range(5))
    if len(kwargs["context"].id) <= 8:
        kwargs["context"] = {"user_id": kwargs["context"].user_id, "parent_id": kwargs["context"].parent_id, "id": kwargs["context"].id}
    else:
        kwargs["context"] = {"user_id": kwargs["context"].user_id, "parent_id": kwargs["context"].parent_id, "id": "1234"}
    pyscript.done = [seq_num, trigger_type, event_type, kwargs, exec_test]

@service
def func4a_hold_false():
    global seq_num

    seq_num += 1
    res = task.wait_until(state_trigger="int(pyscript.f4avar2) >= 10", state_hold_false=0, __test_handshake__=["pyscript.done2", seq_num], state_check_now=True)
    pyscript.done = [seq_num, res.get("value", None)]

@service
def func4b_hold_false():
    global seq_num

    while 1:
        seq_num += 1
        res = task.wait_until(state_trigger="int(pyscript.f4bvar2) >= 10", state_hold_false=0, __test_handshake__=["pyscript.done2", seq_num], state_check_now=False)
        pyscript.done = [seq_num, res["value"]]

@service
def func4c_hold_false():
    global seq_num

    while 1:
        # this should never trigger
        seq_num += 1
        res = task.wait_until(state_trigger="int(pyscript.f4bvar2) >= 10", state_hold_false=1000, __test_handshake__=["pyscript.done2", seq_num], state_check_now=True)
        pyscript.f4bvar2 = 0
        pyscript.done = [seq_num, res.get("value", None)]

@state_trigger("pyscript.f5var1")
@time_active("cron(* * * * *)")
def func5(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func5 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value]

@state_trigger("pyscript.f6var1.attr1 == 123", state_hold=1e-6)
@time_active("not range(2019/1/1, 2019/1/2)")
def func6(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func6 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, pyscript.f6var1.attr1]

@state_trigger("pyscript.f6avar1.attr1 == 123 and pyscript.f6avar2.attr1 == 456")
def func6a(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func6a var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value.attr1]

@state_trigger("pyscript.f6bvar1.attr3", watch=["pyscript.f6bvar1.attr3"])
def func6b(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func6b var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value, pyscript.f6bvar1.attr3]

@state_trigger("pyscript.f6cvar1.*", watch=["pyscript.f6cvar1.*"])
def func6c(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func6c var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value, state.get_attr("pyscript.f6cvar1"), state.getattr("pyscript.f6cvar1")]

@state_trigger("pyscript.f6dvar1.attr1 == 10 and pyscript.f6dvar1.old.attr1 == 20 and pyscript.f6dvar1.nosuchattr == None and pyscript.f6dvar1.old.nosuchattr == None")
def func6d(var_name=None, value=None, old_value=None):
    global seq_num

    seq_num += 1
    log.info(f"func6d var = {var_name}, value = {value}, old_value={old_value}")
    pyscript.done = [seq_num, var_name, value.attr1, old_value.attr1]

@state_trigger("pyscript.f7var1 == '2' and pyscript.f7var1.old == '1'", state_check_now=True)
@state_active("pyscript.f7var1 == '2' and pyscript.f7var1.old == '1' and pyscript.no_such_variable is None")
def func7(var_name=None, value=None, old_value=None):
    global seq_num

    seq_num += 1
    log.info(f"func7 var = {var_name}, value = {value}")
    secs = (pyscript.f7var1.last_updated - pyscript.f7var1.last_changed).total_seconds()
    pyscript.done = [seq_num, var_name, value, old_value, secs]

@state_trigger("state.get('pyscript.f8var1') == '2'", state_check_now=True, watch={"pyscript.f8var1"})
@time_active(hold_off=10000)
def func8(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func8 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value]

@state_trigger("pyscript.f8bvar1 == '30'", state_hold=100000)
def func8b(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func8b var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value]

@state_trigger("pyscript.f9var1 == '2' and pyscript.f9var1.old == None", watch=["pyscript.f9var1"])
@state_active("pyscript.no_such_variable is None")
def func9(var_name=None, value=None, old_value=None):
    global seq_num

    seq_num += 1
    log.info(f"func9 var = {var_name}, value = {value}")
    pyscript.done = [seq_num, var_name, value, old_value]
""",
    )
    # initialize the trigger and active variables
    seq_num = 0
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f1var2", 0)
    hass.states.async_set("pyscript.f2var2", 0)
    hass.states.async_set("pyscript.f2var3", 0)
    hass.states.async_set("pyscript.f2var4", 0)
    hass.states.async_set("pyscript.f4avar2", 0)
    hass.states.async_set("pyscript.f4bvar2", 0)

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num
    assert (
        "func_startup_sync setting pyscript.done = 1, trigger_type = time, trigger_time = startup, var1 = 123"
        in caplog.text
    )

    seq_num += 1

    # try some values that shouldn't work, then one that does
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f1var1", "-2")
    hass.states.async_set("pyscript.f1var1", -1)
    hass.states.async_set("pyscript.f1var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f1var1",
        1,
        32,
        "hello",
        321,
    ]
    assert "func1 var = pyscript.f1var1, value = 1" in caplog.text
    # try some other settings that should not cause it to re-trigger
    hass.states.async_set("pyscript.f1var1", 1, {"attr1": 123, "attr2": 10})
    hass.states.async_set("pyscript.f1var1", 1, {"attr1": 123, "attr2": 20})

    seq_num += 1
    hass.states.async_set("pyscript.f2var3", 3)
    hass.states.async_set("pyscript.f2var4", 0)
    hass.states.async_set("pyscript.f2var2", 0)
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f1var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f1var1",
        1,
        32,
        "hello",
        321,
    ]

    #
    # now check state_hold_false
    #
    for num in range(10, 15):
        seq_num += 1
        hass.states.async_set("pyscript.f1var1", num)
        assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f1var1", num]
        if num == 13:
            #
            # when num == 13 it triggers twice
            #
            seq_num += 1
            assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f1var1", num]

    #
    # check that just a .old variable triggers
    #
    seq_num += 1
    hass.states.async_set("pyscript.f1var2", 10)
    hass.states.async_set("pyscript.f1var2", 15)
    hass.states.async_set("pyscript.f1var2", 10)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f1var2",
        10,
        15,
    ]

    seq_num += 1
    hass.states.async_set("pyscript.f2var4", 4)
    hass.states.async_set("pyscript.f2var2", 2)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f2var2",
        2,
        64,
    ]
    assert "func2 var = pyscript.f2var2, value = 2" in caplog.text

    context = {"user_id": "1234", "parent_id": "5678", "id": "8901"}

    seq_num += 1
    hass.bus.async_fire("test_event3", {"arg1": 12, "arg2": 34})
    hass.bus.async_fire("test_event3", {"arg1": 20, "arg2": 29})
    hass.bus.async_fire("test_event3", {"arg1": 12, "arg2": 30})
    hass.bus.async_fire("fire_event", {"new_event": "test_event3", "arg1": 20, "arg2": 30})
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "event",
        "test_event3",
        {"arg1": 20, "arg2": 30, "var1": 987, "var2": "test", "context": context},
        10,
    ]

    context = {"user_id": None, "parent_id": None, "id": "1234"}

    #
    # test deleting a state variable; func5() triggers on any change to pyscript.f5var1
    #
    seq_num += 1
    hass.states.async_set("pyscript.f5var1", 0)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f5var1", "0"]
    #
    # make sure it doesn't trigger on attribute changes
    #
    hass.states.async_set("pyscript.f5var1", 0, {"attr1": 123, "attr2": 10})
    hass.states.async_set("pyscript.f5var1", 0, {"attr1": 123, "attr2": 20})

    seq_num += 1
    hass.states.async_set("pyscript.f5var1", "")
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f5var1", ""]

    seq_num += 1
    hass.states.async_remove("pyscript.f5var1")
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f5var1", None]

    #
    # check that we can state_trigger off an attribute
    #
    seq_num += 1
    hass.states.async_set("pyscript.f6var1", 1, {"attr1": 123, "attr2": 10})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f6var1", 123]

    #
    # check that back-to-back attribute settings still trigger
    #
    seq_num += 1
    hass.states.async_set("pyscript.f6avar2", 1, {"attr1": 450, "attr2": 10})
    hass.states.async_set("pyscript.f6avar1", 1, {"attr1": 123, "attr2": 10})
    hass.states.async_set("pyscript.f6avar2", 1, {"attr1": 456, "attr2": 10})  # this will trigger
    hass.states.async_set("pyscript.f6avar1", 1, {"attr1": 120, "attr2": 10})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f6avar2", 456]

    #
    # check that we can state_trigger off any change to an attribute
    #
    seq_num += 1
    hass.states.async_set("pyscript.f6bvar1", 1, {"attr3": 987, "attr2": 10})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f6bvar1", "1", 987]
    seq_num += 1
    hass.states.async_set("pyscript.f6bvar1", 1, {"attr3": 987, "attr2": 10})
    hass.states.async_set("pyscript.f6bvar1", 2, {"attr3": 987, "attr2": 10})
    hass.states.async_set("pyscript.f6bvar1", 3, {"attr3": 987, "attr2": 10})
    hass.states.async_set("pyscript.f6bvar1", 1, {"attr3": 985, "attr2": 10})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f6bvar1", "1", 985]

    #
    # check that we can state_trigger off any change to any attribute
    #
    seq_num += 1
    hass.states.async_set("pyscript.f6cvar1", 1, {"attr3": 987, "attr4": 10})
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f6cvar1",
        "1",
        {"attr3": 987, "attr4": 10},
        {"attr3": 987, "attr4": 10},
    ]
    seq_num += 1
    hass.states.async_set("pyscript.f6cvar1", 1, {"attr3": 987, "attr4": 10})
    hass.states.async_set("pyscript.f6cvar1", 2, {"attr3": 987, "attr4": 10})
    hass.states.async_set("pyscript.f6cvar1", 3, {"attr3": 987, "attr4": 10})
    hass.states.async_set("pyscript.f6cvar1", 1, {"attr3": 987, "attr4": 4})
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f6cvar1",
        "1",
        {"attr3": 987, "attr4": 4},
        {"attr3": 987, "attr4": 4},
    ]
    assert "state.get_attr() is deprecated: use state.getattr() instead" in caplog.text

    #
    # check we can trigger based on an old attrib value
    #
    seq_num += 1
    hass.states.async_set("pyscript.f6dvar1", 1, {"attr1": 30})
    hass.states.async_set("pyscript.f6dvar1", 1, {"attr1": 20})
    hass.states.async_set("pyscript.f6dvar1", 1, {"attr1": 10})
    hass.states.async_set("pyscript.f6dvar1", 1, {"attr1": 5})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f6dvar1", 10, 20]

    #
    # check that state_var.old works in a state_trigger
    #
    seq_num += 1
    hass.states.async_set("pyscript.f7var1", 2)
    hass.states.async_set("pyscript.f7var1", 10)
    hass.states.async_set("pyscript.f7var1", 2)
    hass.states.async_set("pyscript.f7var1", 1)
    hass.states.async_set("pyscript.f7var1", 2)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f7var1", "2", "1", 0.0]

    #
    # check that hold_off prevents multiple triggers
    #
    seq_num += 1
    hass.states.async_set("pyscript.f8var1", 2)
    hass.states.async_set("pyscript.f8var1", 0)
    hass.states.async_set("pyscript.f8var1", 2)
    hass.states.async_set("pyscript.f8var1", 0)
    hass.states.async_set("pyscript.f8var1", 2)
    hass.states.async_set("pyscript.f8var1", 0)
    hass.states.async_set("pyscript.f8var1", 2)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f8var1", "2"]

    #
    # check that state_hold prevents any triggers that don't remain True
    #
    hass.states.async_set("pyscript.f8bvar1", 30)
    hass.states.async_set("pyscript.f8bvar1", 31)
    hass.states.async_set("pyscript.f8bvar1", 30)
    hass.states.async_set("pyscript.f8bvar1", 31)
    hass.states.async_set("pyscript.f8bvar1", 30)
    hass.states.async_set("pyscript.f8bvar1", 31)

    #
    # check that state_var.old is None first time
    #
    seq_num += 1
    hass.states.async_set("pyscript.f9var1", 2)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f9var1", "2", None]

    #
    # check state_hold_false
    #
    seq_num += 1
    await hass.services.async_call("pyscript", "func4a_hold_false", {})
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num
    hass.states.async_set("pyscript.f4avar2", 6)
    hass.states.async_set("pyscript.f4avar2", 15)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "15"]

    # will trigger immediately
    seq_num += 1
    await hass.services.async_call("pyscript", "func4a_hold_false", {})
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, None]

    seq_num += 1
    hass.states.async_set("pyscript.f4avar2", 6)
    await hass.services.async_call("pyscript", "func4a_hold_false", {})
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num
    hass.states.async_set("pyscript.f4avar2", 7)
    hass.states.async_set("pyscript.f4avar2", 8)
    # this will trigger
    hass.states.async_set("pyscript.f4avar2", 19)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "19"]

    hass.states.async_set("pyscript.f4bvar2", 6)
    seq_num += 1
    await hass.services.async_call("pyscript", "func4b_hold_false", {})
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num
    seq_num += 1
    await hass.services.async_call("pyscript", "func4c_hold_false", {})
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num

    hass.states.async_set("pyscript.f4bvar2", 15)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "15"]

    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num
    hass.states.async_set("pyscript.f4bvar2", 6)
    hass.states.async_set("pyscript.f4bvar2", 8)
    hass.states.async_set("pyscript.f4bvar2", 1)
    hass.states.async_set("pyscript.f4bvar2", 20)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "20"]
    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num
    hass.states.async_set("pyscript.f4bvar2", 21)
    hass.states.async_set("pyscript.f4bvar2", 22)
    hass.states.async_set("pyscript.f4bvar2", 23)
    hass.states.async_set("pyscript.f4bvar2", 1)
    hass.states.async_set("pyscript.f4bvar2", 25)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "25"]
    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num


@pytest.mark.asyncio
async def test_state_trigger_time(hass, caplog):
    """Test state trigger."""
    notify_q = asyncio.Queue(0)
    notify_q2 = asyncio.Queue(0)
    now = [dt(2020, 7, 1, 10, 59, 59, 999999), dt(2020, 7, 1, 11, 59, 59, 999999)]
    await setup_script(
        hass,
        notify_q,
        notify_q2,
        now,
        """

from math import sqrt
from homeassistant.core import Context

seq_num = 0

#
# Instead of just a bare @time_trigger, do a real time trigger.
# The first value of now() causes func_startup_sync() to start almost
# immediately.  The remaining values of now() are all an hour later at
# 11:59:59.999998, so this trigger won't happen again for another 24 hours.
# (we don't use 11:59:59.999999 because of a bug in croniter.match();
# see https://github.com/taichino/croniter/issues/151, although this is
# now fixed).  We also confirm @time_active checks for the precise time
# of the time trigger.
#
@time_trigger("once(2020/07/01 11:00:00)")
@time_active("range(2020/07/01 11:00:00, 2020/07/01 11:00:00)")
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}")
    pyscript.done = seq_num

    seq_num += 1
    res = task.wait_until(event_trigger=["test_event4b", "arg1 == 25 and arg2 == 35"], timeout=10, __test_handshake__=["pyscript.done2", seq_num])
    del res["context"]
    log.info(f"func4 trigger_type = {res}")

    pyscript.done = [seq_num, res]

    seq_num += 1
    res = task.wait_until(state_trigger="pyscript.f4var2 == '2'", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    pyscript.setVar1 = 1
    pyscript.setVar2 = "var2"
    state.set("pyscript.setVar3", {"foo": "bar"})
    state.set("pyscript.setVar1", 1 + int(state.get("pyscript.setVar1")), {"attr1": 456, "attr2": 987})

    seq_num += 1
    res = task.wait_until(state_trigger=["False", "pyscript.xyznotset", "pyscript.f4var2 == '10' and pyscript.f4var2.old == '2'"], timeout=10, state_hold=1e-6)
    log.info(f"func4 trigger_type = {res}")
    res["context"] = {"user_id": res["context"].user_id, "parent_id": res["context"].parent_id, "id": "1234"}
    pyscript.done = [seq_num, res, pyscript.setVar1, pyscript.setVar1.attr1, state.get("pyscript.setVar1.attr2"),
    pyscript.setVar2, state.get("pyscript.setVar3")]

    #
    # check some fall-though cases based on attribute state triggers
    #
    seq_num += 1
    res = task.wait_until(state_trigger=["pyscript.setVar1 == '2' and pyscript.setVar1.attr2 == 987"], timeout=10, state_hold=1e-6)
    pyscript.done = [seq_num, pyscript.setVar1, pyscript.setVar1.attr2]

    seq_num += 1
    res = task.wait_until(state_trigger="pyscript.setVar1 == '2' and pyscript.setVar1.attr1 == 456 and pyscript.setVar1.nosuchattr == None")
    pyscript.done = [seq_num, pyscript.setVar1, pyscript.setVar1.attr1]

    seq_num += 1
    #
    # now() returns 1usec before 2020/7/1 12:00:00, so trigger right
    # at noon
    #
    res = task.wait_until(time_trigger="once(2020/07/01 12:00:00)", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res["trigger_type"], str(res["trigger_time"])]

    seq_num += 1
    #
    # this should pick up the trigger interval at noon, and not trigger immediately
    # due to the state change because of the state_hold
    #
    res = task.wait_until(time_trigger="period(2020/07/01 11:00, 1 hour)", timeout=10, state_trigger="pyscript.f4var2 == '10'", state_hold=10000)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res["trigger_type"], str(res["trigger_time"])]

    seq_num += 1
    #
    # cron triggers at 10am, 11am, noon, 1pm, 2pm, 3pm, so this
    # should trigger at noon.
    #
    res = task.wait_until(time_trigger="cron(0 10-15 * * *)", timeout=10, state_trigger="pyscript.f4var2 == '10'", state_hold=10000)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res["trigger_type"], str(res["trigger_time"])]

    seq_num += 1
    #
    # also add some month and day ranges; should still trigger at noon
    # on 7/1.
    #
    res = task.wait_until(time_trigger="cron(0 10-15 1-5 6,7 *)", timeout=10, state_trigger="pyscript.f4var2 == '15'")
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res["trigger_type"], str(res["trigger_time"])]

    seq_num += 1
    #
    # make sure a short timeout works, for a trigger further out in time
    # (7/5 at 3pm)
    #
    res = task.wait_until(time_trigger="cron(0 15 5 6,7 *)", timeout=1e-6)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure a short timeout works when there is only a state trigger
    # that isn't true
    #
    res = task.wait_until(state_trigger="pyscript.f4var2 == '20'", timeout=1e-6)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure a short timeout works when there is a state trigger that is true
    # immediately but is waiting for state_hold
    #
    res = task.wait_until(state_trigger="pyscript.f4var2 == '10'", timeout=1e-6, state_hold=1)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure a short timeout works when there is a state trigger that while
    # true isn't checked immediately
    #
    res = task.wait_until(state_trigger="pyscript.f4var2 == '11'", timeout=1e-6, state_check_now=False)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure a short timeout works when there are no other triggers
    #
    res = task.wait_until(timeout=1e-6)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure we return when there are no triggers and no timeout
    #
    res = task.wait_until()
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    seq_num += 1
    #
    # make sure we return when there only past triggers and no timeout
    #
    res = task.wait_until(time_trigger="once(2020/7/1 11:59:59.999)")
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seq_num, res]

    #
    # create a run-time exception
    #
    "xyz" + 123

""",
    )
    # initialize the trigger and active variables
    seq_num = 0
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f2var2", 0)
    hass.states.async_set("pyscript.f2var3", 0)
    hass.states.async_set("pyscript.f2var4", 0)
    hass.states.async_set("pyscript.f4avar2", 0)
    hass.states.async_set("pyscript.f4bvar2", 0)

    context = {"user_id": None, "parent_id": None, "id": "1234"}

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num
    assert (
        "func_startup_sync setting pyscript.done = 1, trigger_type = time, trigger_time = 2020-07-01 11:00:00"
        in caplog.text
    )

    seq_num += 1

    hass.states.async_set("pyscript.f4var2", 2)
    assert literal_eval(await wait_until_done(notify_q2)) == seq_num

    hass.bus.async_fire("test_event4b", {"arg1": 20, "arg2": 25})
    hass.bus.async_fire("test_event4b", {"arg1": 15, "arg2": 25})
    hass.bus.async_fire("test_event4b", {"arg1": 25, "arg2": 35})
    trig = {
        "trigger_type": "event",
        "event_type": "test_event4b",
        "arg1": 25,
        "arg2": 35,
    }
    ret = await wait_until_done(notify_q)
    print(f"test_event4b ret = {ret}, {type(ret)}")
    print(f"test_event4b ret = {literal_eval(ret)}")
    assert literal_eval(ret) == [
        seq_num,
        trig,
    ]

    seq_num += 1
    # the state_trigger wait_until should succeed immediately, since the expr is true
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        {"trigger_type": "state"},
    ]

    seq_num += 1
    # now try a few other values, then the correct one
    hass.states.async_set("pyscript.f4var2", 4)
    hass.states.async_set("pyscript.f4var2", 4, {"attr5": 10})
    hass.states.async_set("pyscript.f4var2", 2)
    hass.states.async_set("pyscript.f4var2", 3, {"attr5": 21})
    hass.states.async_set("pyscript.f4var2", 10)
    hass.states.async_set("pyscript.f4var2", 2, {"attr5": 21})
    hass.states.async_set("pyscript.f4var2", 10)
    hass.states.async_set("pyscript.f4var2", 10, {"attr5": 22})
    trig = {
        "trigger_type": "state",
        "var_name": "pyscript.f4var2",
        "value": "10",
        "old_value": "2",
        "context": context,
    }
    result = literal_eval(await wait_until_done(notify_q))
    assert result[0] == seq_num
    assert result[1] == trig
    assert result[2:5] == ["2", 456, 987]

    assert hass.states.get("pyscript.setVar1").state == "2"
    assert hass.states.get("pyscript.setVar1").attributes == {
        "attr1": 456,
        "attr2": 987,
    }
    assert hass.states.get("pyscript.setVar2").state == "var2"
    assert literal_eval(hass.states.get("pyscript.setVar3").state) == {"foo": "bar"}

    #
    # check the fall-through attrib triggers
    #
    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "2", 987]
    seq_num += 1
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "2", 456]

    #
    # check for the four time triggers, five timeouts and two none
    #
    for trig_type in ["time"] * 4 + ["timeout"] * 5 + ["none"] * 2:
        seq_num += 1
        if trig_type == "time":
            assert literal_eval(await wait_until_done(notify_q)) == [
                seq_num,
                "time",
                "2020-07-01 12:00:00",
            ]
        else:
            res = {"trigger_type": trig_type}
            assert literal_eval(await wait_until_done(notify_q)) == [
                seq_num,
                res,
            ]

    assert "TypeError: can only concatenate str" in caplog.text


@pytest.mark.asyncio
async def test_trigger_closures(hass, caplog):
    """Test trigger function closures."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        None,
        [dt(2020, 7, 1, 10, 59, 59, 999998), dt(2020, 7, 1, 11, 59, 59, 999998)],
        """

seq_num = 0

@time_trigger("startup")
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    pyscript.done = [seq_num, trigger_type, trigger_time]

def factory(trig_value):

    @state_trigger(f"pyscript.var1 == '{trig_value}'")
    @state_trigger("100 <= int(pyscript.var1) <= 101")
    def func_trig(var_name=None, value=None):
        global seq_num, f
        if value == '100':
            pyscript.done = seq_num + trig_value
        elif value == '101':
            if trig_value == 50:
                del f[-1]
                seq_num += 1
                pyscript.done = seq_num
        else:
            seq_num += 1
            pyscript.done = seq_num

    return func_trig

f = [factory(50), factory(51), factory(52), factory(53), factory(54)]
""",
    )
    seq_num = 0

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "time", "startup"]

    #
    # trigger them one at a time to make sure each is working
    #
    for i in range(5):
        seq_num += 1
        hass.states.async_set("pyscript.var1", 50 + i)
        assert literal_eval(await wait_until_done(notify_q)) == seq_num

    for num_func in range(5, 1, -1):
        #
        # trigger all together; we don't know the order, so just check
        # we got all of the remaining ones
        #
        hass.states.async_set("pyscript.var1", 100)
        seqs = set()
        expect = set()
        for i in range(num_func):
            seqs.add(literal_eval(await wait_until_done(notify_q)))
            expect.add(seq_num + 50 + i)
        assert seqs == expect

        #
        # now trigger all again, but just the first deletes the last
        # trigger function and replies with the next seq number
        #
        seq_num += 1
        hass.states.async_set("pyscript.var1", 101)
        assert literal_eval(await wait_until_done(notify_q)) == seq_num


@pytest.mark.asyncio
async def test_state_trigger_check_now(hass, caplog):
    """Test state trigger."""
    notify_q = asyncio.Queue(0)

    hass.states.async_set("pyscript.fstartup0", 1)
    hass.states.async_set("pyscript.fstartup2", 0)

    await setup_script(
        hass,
        notify_q,
        None,
        [dt(2020, 7, 1, 10, 59, 59, 999998), dt(2020, 7, 1, 11, 59, 59, 999998)],
        """

from math import sqrt
from homeassistant.core import Context

# should trigger immediately
@state_trigger("pyscript.fstartup0 == '1'", state_check_now=True)
def func_startup_sync0(trigger_type=None, var_name=None):
    log.info(f"func_startup_sync0 setting pyscript.done=0, trigger_type={trigger_type}, var_name={var_name}")
    pyscript.done = [0, trigger_type, var_name]

# should trigger immediately
@state_trigger("pyscript.fstartup2 == '0'", state_check_now=True)
def func_startup_sync1(trigger_type=None, var_name=None):
    log.info(f"func_startup_sync1 setting pyscript.done=1, trigger_type={trigger_type}, var_name={var_name}")
    pyscript.done = [1, trigger_type, var_name]

# shouldn't trigger immediately
@state_trigger("pyscript.fstartup2 == '1'", state_check_now=True)
def func_startup_sync2(trigger_type=None, var_name=None):
    log.info(f"func_startup_sync2 setting pyscript.done=2, trigger_type={trigger_type}, var_name={var_name}")
    pyscript.done = [2, trigger_type, var_name]
""",
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    #
    # we should get two results, although they could be in any order
    #
    results = [None, None]
    for _ in range(2):
        res = literal_eval(await wait_until_done(notify_q))
        results[res[0]] = res

    assert results == [[0, "state", None], [1, "state", None]]

    for _ in range(2):
        hass.states.async_set("pyscript.fstartup2", 10)
        hass.states.async_set("pyscript.fstartup2", 1)
        assert literal_eval(await wait_until_done(notify_q)) == [2, "state", "pyscript.fstartup2"]

        hass.states.async_set("pyscript.fstartup0", 0)
        hass.states.async_set("pyscript.fstartup0", 1)
        assert literal_eval(await wait_until_done(notify_q)) == [0, "state", "pyscript.fstartup0"]


@pytest.mark.asyncio
async def test_state_methods(hass, caplog):
    """Test state methods that call services."""
    notify_q = asyncio.Queue(0)

    await setup_script(
        hass,
        notify_q,
        None,
        [dt(2020, 7, 1, 12, 0, 0, 0)],
        """

seq_num = 0

@time_trigger("startup")
def func_startup():
    pyscript.var1 = 10
    pyscript.var1.set(20)
    pyscript.var1.set(value=30)
    pyscript.var1.incr()
    pyscript.var1.incr()
    pyscript.var1.set_add(val1=10, val2=40)
    #
    # this will generate an exception
    #
    pyscript.var1.set_add(10)

@service
def set(entity_id=None, value=None):
    global seq_num

    seq_num += 1
    state.set(entity_id, value)
    pyscript.done = [seq_num, entity_id, state.get(entity_id)]

@service
def incr(entity_id=None):
    global seq_num

    seq_num += 1
    state.set(entity_id, int(state.get(entity_id)) + 1)
    pyscript.done = [seq_num, entity_id, state.get(entity_id)]

@service
def set_add(entity_id=None, val1=None, val2=None):
    global seq_num

    seq_num += 1
    state.set(entity_id, val1 + val2)
    pyscript.done = [seq_num, entity_id, state.get(entity_id), type(hass).__name__]

@time_trigger("startup")
def service_call_exception():
    pyscript.set_add("foo")

""",
        config={DOMAIN: {CONF_ALLOW_ALL_IMPORTS: True, CONF_HASS_IS_GLOBAL: True}},
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == [1, "pyscript.var1", "20"]
    assert literal_eval(await wait_until_done(notify_q)) == [2, "pyscript.var1", "30"]
    assert literal_eval(await wait_until_done(notify_q)) == [3, "pyscript.var1", "31"]
    assert literal_eval(await wait_until_done(notify_q)) == [4, "pyscript.var1", "32"]
    assert literal_eval(await wait_until_done(notify_q)) == [5, "pyscript.var1", "50", "HomeAssistant"]
    assert "TypeError: service pyscript.set_add takes no positional arguments" in caplog.text
    assert "TypeError: service pyscript.set_add takes only keyword arguments" in caplog.text


@pytest.mark.asyncio
async def test_service_call_params(hass):
    """Test that hass params get set properly on service calls."""
    with patch.object(hass.services, "async_call") as call, patch.object(
        Function, "service_has_service", return_value=True
    ):
        Function.init(hass)
        await Function.service_call(
            "test", "test", context=Context(id="test"), blocking=True, limit=1, other_service_data="test"
        )
        assert call.called
        assert call.call_args[0] == ("test", "test", {"other_service_data": "test"})
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": True, "limit": 1}
        call.reset_mock()

        await Function.service_call(
            "test", "test", context=Context(id="test"), blocking=False, other_service_data="test"
        )
        assert call.called
        assert call.call_args[0] == ("test", "test", {"other_service_data": "test"})
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": False}
        call.reset_mock()

        await Function.get("test.test")(
            context=Context(id="test"), blocking=True, limit=1, other_service_data="test"
        )
        assert call.called
        assert call.call_args[0] == ("test", "test", {"other_service_data": "test"})
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": True, "limit": 1}
        call.reset_mock()

        await Function.get("test.test")(
            context=Context(id="test"), blocking=False, other_service_data="test"
        )
        assert call.called
        assert call.call_args[0] == ("test", "test", {"other_service_data": "test"})
        assert call.call_args[1] == {"context": Context(id="test"), "blocking": False}

    # Stop all tasks to avoid conflicts with other tests
    await Function.waiter_stop()
    await Function.reaper_stop()


@pytest.mark.asyncio
async def test_service_call_blocking(hass, caplog):
    """Test that service calls with blocking=True actually block."""
    notify_q = asyncio.Queue(0)

    await setup_script(
        hass,
        notify_q,
        None,
        [dt(2020, 7, 1, 12, 0, 0, 0)],
        """
seq_num = 0

@time_trigger("startup")
def func_startup():
    global seq_num

    seq_num += 1
    pyscript.var1 = 1
    pyscript.service1(blocking=True)
    pyscript.done = [seq_num, pyscript.var1]

    seq_num += 1
    pyscript.service1(blocking=True)
    pyscript.done = [seq_num, pyscript.var1]

    seq_num += 1
    service.call("pyscript", "service1", blocking=True)
    pyscript.done = [seq_num, pyscript.var1]

    seq_num += 1
    pyscript.var1 = int(pyscript.var1) + 1
    service.call("pyscript", "long_sleep", blocking=True, limit=1e-6)
    pyscript.done = [seq_num, pyscript.var1]

    seq_num += 1
    pyscript.done = [seq_num]

@service
def long_sleep():
    task.sleep(10000)

@service
def service1():
    pyscript.var1 = int(pyscript.var1) + 1

""",
        config={DOMAIN: {CONF_ALLOW_ALL_IMPORTS: True, CONF_HASS_IS_GLOBAL: True}},
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == [1, "2"]
    assert literal_eval(await wait_until_done(notify_q)) == [2, "3"]
    assert literal_eval(await wait_until_done(notify_q)) == [3, "4"]
    assert literal_eval(await wait_until_done(notify_q)) == [4, "5"]
    assert literal_eval(await wait_until_done(notify_q)) == [5]
