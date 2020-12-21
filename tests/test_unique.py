"""Test the pyscript component."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt

from custom_components.pyscript.const import DOMAIN
import custom_components.pyscript.trigger as trigger
from pytest_homeassistant_custom_component.async_mock import mock_open, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/some/config/dir/pyscripts/hello.py",
    ]

    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob", return_value=scripts
    ), patch("custom_components.pyscript.global_ctx.open", mock_open(read_data=source)), patch(
        "custom_components.pyscript.open", mock_open(read_data=source)
    ), patch(
        "custom_components.pyscript.trigger.dt_now", return_value=now
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value={}
    ), patch(
        "custom_components.pyscript.watchdog_start", return_value=None
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.global_ctx.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.install_requirements", return_value=None,
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: {}})

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the fixed time, now.
    #
    trigger.__dict__["dt_now"] = lambda: now

    if notify_q:

        async def state_changed(event):
            var_name = event.data["entity_id"]
            if var_name != "pyscript.done":
                return
            value = event.data["new_state"].state
            await notify_q.put(value)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


async def test_task_unique(hass, caplog):
    """Test task.unique ."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """

seq_num = 0

@time_trigger("startup")
def funcStartupSync():
    global seq_num

    seq_num += 1
    log.info(f"funcStartupSync setting pyscript.done = {seq_num}")
    pyscript.done = seq_num
    #
    # stick around so the task.unique() still applies
    #
    task.unique("func6")
    task.sleep(10000)

@state_trigger("pyscript.f0var1 == '1'")
def func0(var_name=None, value=None):
    global seq_num

    seq_num += 1
    pyscript.done = [seq_num, var_name]
    result = task.wait_until(state_trigger=["pyscript.f0var2"])
    seq_num += 1
    result["context"] = {"user_id": result["context"].user_id, "parent_id": result["context"].parent_id, "id": "1234"}
    pyscript.done = [seq_num, var_name, result]

@state_trigger("pyscript.f1var1 == '1'")
def func1(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func1 var = {var_name}, value = {value}")
    task.unique("func1")
    pyscript.done = [seq_num, var_name]
    # this should terminate our task, so the 2nd done won't happen
    # if it did, we would get out of sequence in the assert
    task.unique("func1")
    pyscript.done = [seq_num, var_name]

@state_trigger("pyscript.f2var1 == '1'")
def func2(var_name=None, value=None):
    global seq_num

    seq_num += 1
    mySeqNum = seq_num
    log.info(f"func2 var = {var_name}, value = {value}")
    task.unique("func2")
    while 1:
        task.wait_until(state_trigger=["pyscript.f2var1 == '2'", "pyscript.no_such_var == '5'", "pyscript.no_such_var2"])
        pyscript.f2var1 = 0
        pyscript.done = [mySeqNum, var_name]

@state_trigger("pyscript.f3var1 == '1'")
def func3(var_name=None, value=None):
    global seq_num

    seq_num += 1
    log.info(f"func3 var = {var_name}, value = {value}")
    task.unique("func2")
    pyscript.done = [seq_num, var_name]

@state_trigger("pyscript.f4var1 == '1'")
@task_unique("func4")
def func4():
    global seq_num

    seq_num += 1
    pyscript.done = [seq_num, "pyscript.f4var1"]
    res = task.wait_until(state_trigger=["False", "False", "pyscript.f4var2 == '1'"])
    pyscript.done = [seq_num, "pyscript.f4var2"]

@state_trigger("pyscript.f5var1 == '1'")
@task_unique("func5", kill_me=True)
def func5():
    global seq_num

    seq_num += 1
    pyscript.done = [seq_num, "pyscript.f5var1"]
    res = task.wait_until(state_trigger="pyscript.f5var2 == '1'")
    pyscript.done = [seq_num, "pyscript.f5var2"]


@state_trigger("pyscript.f4var1 == '1'")
def func6():
    task.unique("func6", kill_me=True)
    # mess up the sequence numbers if task.unique fails to kill us
    pyscript.done = [999]

""",
    )

    seq_num = 0

    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f2var1", 0)
    hass.states.async_set("pyscript.f3var1", 0)

    seq_num += 1
    # fire event to startup triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    seq_num += 1
    hass.states.async_set("pyscript.f0var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f0var1"]

    seq_num += 1
    hass.states.async_set("pyscript.f1var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f1var1"]

    for _ in range(5):
        #
        # repeat this test 5 times
        #
        seq_num += 1
        hass.states.async_set("pyscript.f1var1", 0)
        hass.states.async_set("pyscript.f1var1", 1)
        assert literal_eval(await wait_until_done(notify_q)) == [
            seq_num,
            "pyscript.f1var1",
        ]

    # get func2() through wait_notify and get reply; should be in wait_notify()
    seq_num += 1
    hass.states.async_set("pyscript.f2var1", 1)
    hass.states.async_set("pyscript.f2var1", 2)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f2var1"]

    # now run func3() which will kill func2()
    seq_num += 1
    hass.states.async_set("pyscript.f3var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [seq_num, "pyscript.f3var1"]

    # now run func3() a few more times, and also try to re-trigger func2()
    # should be no more acks from func2()
    for _ in range(10):
        #
        # repeat this test 10 times
        #
        seq_num += 1
        hass.states.async_set("pyscript.f2var1", 2)
        hass.states.async_set("pyscript.f2var1", 0)
        hass.states.async_set("pyscript.f3var1", 0)
        hass.states.async_set("pyscript.f3var1", 1)
        assert literal_eval(await wait_until_done(notify_q)) == [
            seq_num,
            "pyscript.f3var1",
        ]

    #
    # now run func4() a few times; each one should stop the last one
    #
    hass.states.async_set("pyscript.f4var2", 0)
    for _ in range(10):
        seq_num += 1
        hass.states.async_set("pyscript.f4var1", 0)
        hass.states.async_set("pyscript.f4var1", 1)
        assert literal_eval(await wait_until_done(notify_q)) == [
            seq_num,
            "pyscript.f4var1",
        ]
    #
    # now let the last one complete, and check the seq number
    #
    hass.states.async_set("pyscript.f4var2", 0)
    hass.states.async_set("pyscript.f4var2", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f4var2",
    ]

    #
    # now run func5() a few times; only the first one should
    # start and the rest will not
    #
    seq_num += 1
    hass.states.async_set("pyscript.f5var2", 0)
    hass.states.async_set("pyscript.f5var1", 0)
    hass.states.async_set("pyscript.f5var1", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f5var1",
    ]
    for _ in range(10):
        hass.states.async_set("pyscript.f5var1", 0)
        hass.states.async_set("pyscript.f5var1", 1)
    #
    # now let the first one complete, and check the seq number
    #
    hass.states.async_set("pyscript.f5var2", 0)
    hass.states.async_set("pyscript.f5var2", 1)
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f5var2",
    ]

    #
    # now go back to func0, which is waiting on any change to pyscript.f0var2
    #
    seq_num += 1
    hass.states.async_set("pyscript.f0var2", 1)
    context = {"user_id": None, "parent_id": None, "id": "1234"}
    assert literal_eval(await wait_until_done(notify_q)) == [
        seq_num,
        "pyscript.f0var1",
        {
            "old_value": None,
            "trigger_type": "state",
            "value": "1",
            "var_name": "pyscript.f0var2",
            "context": context,
        },
    ]
