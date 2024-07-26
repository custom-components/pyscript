"""Requirements helpers for pyscript."""
import glob
import logging
import os
import sys

from homeassistant.loader import bind_hass
from homeassistant.requirements import async_process_requirements

from .const import (
    ATTR_INSTALLED_VERSION,
    ATTR_SOURCES,
    ATTR_VERSION,
    CONF_ALLOW_ALL_IMPORTS,
    CONF_INSTALLED_PACKAGES,
    DOMAIN,
    LOGGER_PATH,
    REQUIREMENTS_FILE,
    REQUIREMENTS_PATHS,
    UNPINNED_VERSION,
)

if sys.version_info[:2] >= (3, 8):
    from importlib.metadata import (  # pylint: disable=no-name-in-module,import-error
        PackageNotFoundError,
        version as installed_version,
    )
else:
    from importlib_metadata import (  # pylint: disable=import-error
        PackageNotFoundError,
        version as installed_version,
    )

_LOGGER = logging.getLogger(LOGGER_PATH)


def get_installed_version(pkg_name):
    """Get installed version of package. Returns None if not found."""
    try:
        return installed_version(pkg_name)
    except PackageNotFoundError:
        return None


def update_unpinned_versions(package_dict):
    """Check for current installed version of each unpinned package."""
    requirements_to_pop = []
    for package in package_dict:
        if package_dict[package] != UNPINNED_VERSION:
            continue

        package_dict[package] = get_installed_version(package)
        if not package_dict[package]:
            _LOGGER.error("%s wasn't able to be installed", package)
            requirements_to_pop.append(package)

    for package in requirements_to_pop:
        package_dict.pop(package)

    return package_dict


@bind_hass
def process_all_requirements(pyscript_folder, requirements_paths, requirements_file):
    """
    Load all lines from requirements_file located in requirements_paths.

    Returns files and a list of packages, if any, that need to be installed.
    """

    # Re-import Version to avoid dealing with multiple flake and pylint errors
    from packaging.version import Version  # pylint: disable=import-outside-toplevel

    all_requirements_to_process = {}
    for root in requirements_paths:
        for requirements_path in glob.glob(os.path.join(pyscript_folder, root, requirements_file)):
            with open(requirements_path, "r", encoding="utf-8") as requirements_fp:
                all_requirements_to_process[requirements_path] = requirements_fp.readlines()

    all_requirements_to_install = {}
    for requirements_path, pkg_lines in all_requirements_to_process.items():
        for pkg in pkg_lines:
            # Remove inline comments which are accepted by pip but not by Home
            # Assistant's installation method.
            # https://rosettacode.org/wiki/Strip_comments_from_a_string#Python
            i = pkg.find("#")
            if i >= 0:
                pkg = pkg[:i]
            pkg = pkg.strip()

            if not pkg or len(pkg) == 0:
                continue

            try:
                # Attempt to get version of package. Do nothing if it's found since
                # we want to use the version that's already installed to be safe
                parts = pkg.split("==")
                if len(parts) > 2 or "," in pkg or ">" in pkg or "<" in pkg:
                    _LOGGER.error(
                        (
                            "Ignoring invalid requirement '%s' specified in '%s'; if a specific version"
                            "is required, the requirement must use the format 'pkg==version'"
                        ),
                        requirements_path,
                        pkg,
                    )
                    continue
                if len(parts) == 1:
                    new_version = UNPINNED_VERSION
                else:
                    new_version = parts[1]
                pkg_name = parts[0]

                current_pinned_version = all_requirements_to_install.get(pkg_name, {}).get(ATTR_VERSION)
                current_sources = all_requirements_to_install.get(pkg_name, {}).get(ATTR_SOURCES, [])
                # If a version hasn't already been recorded, record this one
                if not current_pinned_version:
                    all_requirements_to_install[pkg_name] = {
                        ATTR_VERSION: new_version,
                        ATTR_SOURCES: [requirements_path],
                        ATTR_INSTALLED_VERSION: get_installed_version(pkg_name),
                    }

                # If the new version is unpinned and there is an existing pinned version, use existing
                # pinned version
                elif new_version == UNPINNED_VERSION and current_pinned_version != UNPINNED_VERSION:
                    _LOGGER.warning(
                        (
                            "Unpinned requirement for package '%s' detected in '%s' will be ignored in "
                            "favor of the pinned version '%s' detected in '%s'"
                        ),
                        pkg_name,
                        requirements_path,
                        current_pinned_version,
                        str(current_sources),
                    )
                # If the new version is pinned and the existing version is unpinned, use the new pinned
                # version
                elif new_version != UNPINNED_VERSION and current_pinned_version == UNPINNED_VERSION:
                    _LOGGER.warning(
                        (
                            "Unpinned requirement for package '%s' detected in '%s will be ignored in "
                            "favor of the pinned version '%s' detected in '%s'"
                        ),
                        pkg_name,
                        str(current_sources),
                        new_version,
                        requirements_path,
                    )
                    all_requirements_to_install[pkg_name] = {
                        ATTR_VERSION: new_version,
                        ATTR_SOURCES: [requirements_path],
                        ATTR_INSTALLED_VERSION: get_installed_version(pkg_name),
                    }
                # If the already recorded version is the same as the new version, append the current
                # path so we can show sources
                elif (
                    new_version == UNPINNED_VERSION and current_pinned_version == UNPINNED_VERSION
                ) or Version(current_pinned_version) == Version(new_version):
                    all_requirements_to_install[pkg_name][ATTR_SOURCES].append(requirements_path)
                # If the already recorded version is lower than the new version, use the new one
                elif Version(current_pinned_version) < Version(new_version):
                    _LOGGER.warning(
                        (
                            "Version '%s' for package '%s' detected in '%s' will be ignored in "
                            "favor of the higher version '%s' detected in '%s'"
                        ),
                        current_pinned_version,
                        pkg_name,
                        str(current_sources),
                        new_version,
                        requirements_path,
                    )
                    all_requirements_to_install[pkg_name].update(
                        {ATTR_VERSION: new_version, ATTR_SOURCES: [requirements_path]}
                    )
                # If the already recorded version is higher than the new version, ignore the new one
                elif Version(current_pinned_version) > Version(new_version):
                    _LOGGER.warning(
                        (
                            "Version '%s' for package '%s' detected in '%s' will be ignored in "
                            "favor of the higher version '%s' detected in '%s'"
                        ),
                        new_version,
                        pkg_name,
                        requirements_path,
                        current_pinned_version,
                        str(current_sources),
                    )
            except ValueError:
                # Not valid requirements line so it can be skipped
                _LOGGER.debug("Ignoring '%s' because it is not a valid package", pkg)

    return all_requirements_to_install


@bind_hass
async def install_requirements(hass, config_entry, pyscript_folder):
    """Install missing requirements from requirements.txt."""

    pyscript_installed_packages = config_entry.data.get(CONF_INSTALLED_PACKAGES, {}).copy()

    # Import packaging inside install_requirements so that we can use Home Assistant to install it
    # if it can't been found
    try:
        from packaging.version import Version  # pylint: disable=import-outside-toplevel
    except ModuleNotFoundError:
        await async_process_requirements(hass, DOMAIN, ["packaging"])
        from packaging.version import Version  # pylint: disable=import-outside-toplevel

    all_requirements = await hass.async_add_executor_job(
        process_all_requirements, pyscript_folder, REQUIREMENTS_PATHS, REQUIREMENTS_FILE
    )

    requirements_to_install = {}

    if all_requirements and not config_entry.data.get(CONF_ALLOW_ALL_IMPORTS, False):
        _LOGGER.error(
            (
                "Requirements detected but 'allow_all_imports' is set to False, set "
                "'allow_all_imports' to True if you want packages to be installed"
            )
        )
        return

    for package in all_requirements:
        pkg_installed_version = all_requirements[package].get(ATTR_INSTALLED_VERSION)
        version_to_install = all_requirements[package][ATTR_VERSION]
        sources = all_requirements[package][ATTR_SOURCES]
        # If package is already installed, we need to run some checks
        if pkg_installed_version:
            # If the version to install is unpinned and there is already something installed,
            # defer to what is installed
            if version_to_install == UNPINNED_VERSION:
                _LOGGER.debug(
                    (
                        "Skipping unpinned version of package '%s' because version '%s' is "
                        "already installed"
                    ),
                    package,
                    pkg_installed_version,
                )
                # If installed package is not the same version as the one we last installed,
                # that means that the package is externally managed now so we shouldn't touch it
                # and should remove it from our internal tracker
                if (
                    package in pyscript_installed_packages
                    and pyscript_installed_packages[package] != pkg_installed_version
                ):
                    pyscript_installed_packages.pop(package)
                continue

            # If installed package is not the same version as the one we last installed,
            # that means that the package is externally managed now so we shouldn't touch it
            # and should remove it from our internal tracker
            if package in pyscript_installed_packages and Version(
                pyscript_installed_packages[package]
            ) != Version(pkg_installed_version):
                _LOGGER.warning(
                    (
                        "Version '%s' for package '%s' detected in '%s' will be ignored in favor of"
                        " the version '%s' which was installed outside of pyscript"
                    ),
                    version_to_install,
                    package,
                    str(sources),
                    pkg_installed_version,
                )
                pyscript_installed_packages.pop(package)
            # If there is a version mismatch between what we want and what is installed, we
            # can overwrite it since we know it was last installed by us
            elif package in pyscript_installed_packages and Version(version_to_install) != Version(
                pkg_installed_version
            ):
                requirements_to_install[package] = all_requirements[package]
            # If there is an installed version that we have not previously installed, we
            # should not install it
            else:
                _LOGGER.debug(
                    (
                        "Version '%s' for package '%s' detected in '%s' will be ignored because it"
                        " is already installed"
                    ),
                    version_to_install,
                    package,
                    str(sources),
                )
        # Anything not already installed in the environment can be installed
        else:
            requirements_to_install[package] = all_requirements[package]

    if requirements_to_install:
        _LOGGER.info(
            "Installing the following packages: %s",
            str(requirements_to_install),
        )
        await async_process_requirements(
            hass,
            DOMAIN,
            [
                f"{package}=={pkg_info[ATTR_VERSION]}"
                if pkg_info[ATTR_VERSION] != UNPINNED_VERSION
                else package
                for package, pkg_info in requirements_to_install.items()
            ],
        )
    else:
        _LOGGER.debug("No new packages to install")

    # Update package tracker in config entry for next time
    pyscript_installed_packages.update(
        {package: pkg_info[ATTR_VERSION] for package, pkg_info in requirements_to_install.items()}
    )

    # If any requirements were unpinned, get their version now so they can be pinned later
    if any(version == UNPINNED_VERSION for version in pyscript_installed_packages.values()):
        pyscript_installed_packages = await hass.async_add_executor_job(
            update_unpinned_versions, pyscript_installed_packages
        )
    if pyscript_installed_packages != config_entry.data.get(CONF_INSTALLED_PACKAGES, {}):
        new_data = config_entry.data.copy()
        new_data[CONF_INSTALLED_PACKAGES] = pyscript_installed_packages
        hass.config_entries.async_update_entry(entry=config_entry, data=new_data)
