"""Test configuration for pyscript."""
from pytest import fixture
from pytest_homeassistant_custom_component.async_mock import patch


@fixture(autouse=True)
def bypass_package_install_fixture():
    """Bypass package installation."""
    with patch("custom_components.pyscript.async_process_requirements"):
        yield
