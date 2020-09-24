# Pyscript Development Setup

These setup commands only need to be executed once.  First, clone the repository:
```bash
git clone https://github.com/custom-components/pyscript.git
cd pyscript
```

Next, create a virtual environment (make sure `python` is at least `v3.7`; you might need to use
`python3` instead):
```bash
python -m venv venv
source venv/bin/activate
```

Finally, install the requirements and the pre-commit hooks:
```bash
python -m pip install -r tests/requirements_test.txt
pre-commit install
```

To submit PRs you will want to fork the repository, and follow the steps above on your
forked repository. Once you have pushed your changes to the fork (which should run the
pre-commit hooks), you can go ahead and submit a PR.

# Pyscript Tests

This directory contains various tests for pyscript.

After completing the above setup steps, you need to activate the virtual environment
every time you start a new shell:
```bash
source venv/bin/activate
```

Now you can run the tests using this command:
```bash
pytest
```
or run a specific test file with:
```bash
pytest tests/test_function.py
```

You can check coverage and list specific missing lines with:
```
pytest --cov=custom_components/pyscript --cov-report term-missing
```
