"""Test the pyscript apps, modules and import features."""

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


async def test_tasks(hass, caplog):
    """Test starting tasks."""

    conf_dir = hass.config.path(FOLDER)

    file_contents = {
        f"{conf_dir}/hello.py": """

#
# check starting multiple tasks, each stopping the prior one
#
def task1(cnt, last):
    task.unique('task1')
    if not last:
        task.sleep(10)
    log.info(f"finished task1, cnt={cnt}")

for cnt in range(10):
    task.create(task1, cnt, cnt == 9)

#
# check the return value after wait
#
def task2(arg):
    return 2 * arg

t2a = task.create(task2, 21)
t2b = task.create(task2, 51)
done, pending = task.wait({t2a, t2b})
log.info(f"task2() results = {[t2a.result(), t2b.result()]}, len(done) = {len(done)};")

#
# check the return value with a regular function
#
@pyscript_compile
def task3(arg):
    return 2 * arg

t3a = task.create(task3, 22)
t3b = task.create(task3, 52)
done, pending = task.wait({t3a, t3b})
log.info(f"task3() results = {[t3a.result(), t3b.result()]}, len(done) = {len(done)};")


#
# check that we can do a done callback
#
def task4(arg):
    task.wait_until(state_trigger="pyscript.var4 == '1'")
    return 2 * arg

def callback4a(arg):
    log.info(f"callback4a arg = {arg}")

def callback4b(arg):
    log.info(f"callback4b arg = {arg}")

def callback4c(arg):
    log.info(f"callback4c arg = {arg}")

t4 = task.create(task4, 23)
task.add_done_callback(t4, callback4a, 26)
task.add_done_callback(t4, callback4b, 101)
task.add_done_callback(t4, callback4c, 200)
task.add_done_callback(t4, callback4a, 25)
task.add_done_callback(t4, callback4c, 201)
task.add_done_callback(t4, callback4b, 100)
task.add_done_callback(t4, callback4a, 24)
task.remove_done_callback(t4, callback4c)
task.remove_done_callback(t4, task4)
pyscript.var4 = 1
done, pending = task.wait({t4})
log.info(f"task4() result = {t4.result()}, len(done) = {len(done)};")

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

    conf = {"apps": {"world": {}}}
    with patch("custom_components.pyscript.os.path.isdir", return_value=True), patch(
        "custom_components.pyscript.glob.iglob"
    ) as mock_glob, patch("custom_components.pyscript.global_ctx.open", mock_open), patch(
        "custom_components.pyscript.open", mock_open
    ), patch(
        "homeassistant.config.load_yaml_config_file", return_value={"pyscript": conf}
    ), patch(
        "custom_components.pyscript.os.path.getmtime", return_value=1000
    ), patch(
        "custom_components.pyscript.watchdog_start", return_value=None
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

    assert caplog.text.count("finished task1, cnt=9") == 1
    assert "task2() results = [42, 102], len(done) = 2;" in caplog.text
    assert "task3() results = [44, 104], len(done) = 2;" in caplog.text
    assert "task4() result = 46, len(done) = 1;" in caplog.text
    assert caplog.text.count("callback4a arg =") == 1
    assert "callback4a arg = 24" in caplog.text
    assert caplog.text.count("callback4b arg =") == 1
    assert "callback4b arg = 100" in caplog.text
    assert "callback4c arg =" not in caplog.text
