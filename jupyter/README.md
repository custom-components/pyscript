# Jupyter kernel for Pyscript

Pyscript provides a kernel that interfaces with the Jupyter front-ends (eg, notebook, console and
lab). That allows you to develop and test pyscript triggers, functions and automation logic
interactively. Plus you can interact with much of HASS by looking at state variables and calling
services as you experiment and develop your own logic and automations.

Jupyter auto-completion (with `<TAB>`) is supported in Jupyter notebook and console. It should
work after you have typed at least the first character. After you hit `<TAB>` you should see a
list of potential completions from which you can select.  It's a great way to easily see available
state variables, functions or services.

## Installation

Here are the steps to install a pyscript kernel for Jupyter:
* Find the directory where Jupyter kernels are stored:
```
jupyter kernelspec list
```
* Create a new directory called `pyscript` alongside the other kernels:
```
cd KERNEL_DIRECTORY
mkdir pyscript
cd pyscript
* Download and extract the latest `pyscript-jupyter-X.XX.zip` file from githib releases.
Alternatively, you can download the current master version of the files (`kernel.json`,
`hass_pyscript_kernel.py` and the two logo files) in this directory.
* Edit `kernel.json`:
    - replace KERNEL_DIRECTORY by the directory determined above.
    - check the `python` entry in `argv` to make sure it is the latest version of python
      on your system (eg, you might need to replace with `python3`)
* Edit `hass_pyscript_kernel.py` and replace the settings of:
    - `HASS_URL` with the URL of your HASS httpd service
    - `HASS_TOKEN` with a long-lived access token created via the button at the bottom of
       your user profile page in HASS.
* Ensure that zmq is installed:
```
pip install zmq
```
* Confirm that Jupyter now recognizes the new pyscript kernel:
```
jupyter kernelspec list
```
Please tell me if you have feedback on how to automate or improve these steps.

## Running Jupyter

If you download the `pyscript_tutorial.ipynb` notebook, you can open it with:
```
jupyter notebook pyscript_tutorial.ipynb
```

You can open the web-based Jupyter clients (eg, notebook and lab) as usual, eg:
```
jupyter notebook
```
and use the Jupyter menus to start a new `hass pyscript` kernel.

For the Jupyter command-line console, you can run:
```
jupyter console --kernel=pyscript
```

## Tutorial

There is a Jupyter notebook [tutorial](https://github.com/custom-components/pyscript/jupyter/blob/master/pyscript_tutorial.ipynb)
that covers many pyscript features.  It can be downlaoded and run interactively in Jupyter
notebook connected to your live HASS with pyscript.  After you download it, run it with:
```
jupyter notebook pyscript_tutorial.ipynb
```
You can step through each command by hitting `<Shift>Enter`.  There are various
ways to navigate and run cells in Jupyter that you can read in the Jupyter
documentation.

## Work Flow

Using the tutorial as examples, you can use a Jupyter client to interactively develop and test
functions, triggers and services.

In a Jupyter session, one or more functions can be defined in each code cell. Every time that cell
is executed (eg, `<Shift>Return`), those functions are redefined, and any existing trigger
decorators with the same function name are canceled and replaced by the new definition. You might
have other function and trigger definitions in another cell - they won't be affected if their
names are different, and they will only be replaced when you re-execute that other cell.

When the Jupyter session is terminated, its global context is deleted, which means any trigger
rules, functions, services and variables you created are deleted.  The pyscript Jupyter kernel
is intended as an interactive sandbox. As you finalize specific functions, triggers and automation
logic, you should copy them to a pyscript script file, and then use the `pyscript.reload` service
to load them. That ensures they will be loaded and run each time you re-start HASS.

If a function you define has been triggered and is currently executing Python code, then re-running
the cell in which the function is defined or exiting the Jupyter session will not stop or cancel the
already running function. This is same behavior as `reload`. In pyscript, each triggered function
(ie, a trigger has occurred and the trigger conditions are met, and the function is actually
executing Python code) runs as an independent task until it finishes. So if you are testing triggers
of a long-running function (eg, one that uses `task.sleep() or `task.wait_until()`) you could end up
with many running instances. It's strongly recommended that you use `task.unique()` to make sure old
running function tasks are terminated when a new one is triggered. Then you can manually call
`task.unique()` to terminate that last running function before exiting the Jupyter session.

If you switch global contexts to a script file's context, and create some new variables,
triggers, functions or services there, then those objects will survive the termination
of your Jupyter session.  However, if you `reload` the scripts, then those newly-created
objects will be removed.  To make any additions or changes permanent (meaning they will
be re-created on each `reload` or each time your restart HASS) then you shoud copy the
changes or additions to one of you pyscript script files.

## Global Contexts

Each Jupyter session has its own separate global context, so functions and variables defined in each
interactive session are isolated from the script files and other Jupyter sessions.  Pyscript
provides some utility functions to switch global contexts, which allows an interactive Jupyter
session to interact directly with functions and global variables created by a script file, or even
another Jupyter session.

See the [documentation on global contexts](https://github.com/custom-components/pyscript#global-context-functions).

## Caveats

The pyscript Jupyter kernel is an experimental feature and it will probably evolve with new features
and capabilities (and no doubt there are bugs that will need to be fixed).  Here are some caveats
about using it.

For Jupyter console:
* Jupyter console allows multi-line input (eg, a function definition) and delays excution by the
kernel until it is syntactically correct (ie, complete) and the indent on the last line is 0.  So if
you define a multi-line function or statement with indenting, you will need to hit `Enter` one more
time so there is an empty line indicating your code block is complete.

* Jupyter generally assumes the kernel operates in a half-duplex manner - it sends a snippet of code
to the kernel to be executed, and the result (if any) and output (if any) are then displayed.  In
pyscript, a trigger function runs asynchonously, so it can generate output some future time.  In
Jupyter notebook, the right thing happens - whenever the output messages are generated, they appear
below the last cell that was executed. Jupyter notebook displays the running list of message output.
However, in Jupter console, it doesn't appear to check for any pending output from the kernel until
you hit `Enter` to execute the next command.  So the display of output in the console is delayed
until you hit `Enter`.

For Jupyter lab:
* In Jupyter lab, each tab starts a new session (which is same behavior with iPython - each tab will
have a different iPython instance), so each tab (eg, a notebook in one and a console in another)
will have different global contexts. If you wish, you can use the function `pyscript.set_global_ctx()`
to set the context in the other tabs to be the same as the first.

* Jupyter auto-completion it doesn't yet work in Jupyter lab - that's an open bug I need to fix.
