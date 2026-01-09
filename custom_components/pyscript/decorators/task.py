"""Task decorators."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.helpers import config_validation as cv

from ..decorator_abc import CallHandlerDecorator, DispatchData
from ..function import Function
from .base import AutoKwargsDecorator

_LOGGER = logging.getLogger(__name__)


class TaskUniqueDecorator(CallHandlerDecorator, AutoKwargsDecorator):
    """Implementation for @task_unique."""

    name = "task_unique"
    args_schema = vol.Schema(vol.All([str], vol.Length(min=1, max=1)))
    kwargs_schema = vol.Schema({vol.Optional("kill_me", default=False): cv.boolean})

    kill_me: bool

    async def handle_call(self, data: DispatchData) -> bool:
        """Handle call."""
        if self.kill_me:
            if Function.unique_name_used(data.call_ast_ctx, self.args[0]):
                _LOGGER.debug(
                    "trigger %s got %s trigger, @task_unique kill_me=True prevented new action",
                    "notify_type",
                    self.name,
                )
                return False

        task_unique_func = Function.task_unique_factory(data.call_ast_ctx)
        await task_unique_func(self.args[0])
        return True
