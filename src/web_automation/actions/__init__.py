"""Importing this package registers every built-in action."""

from web_automation.actions import (  # noqa: F401
    database,
    downloads,
    extraction,
    interaction,
    navigation,
    wait,
)
from web_automation.actions.base import Action, Step, get_action, register_action, registered_actions

__all__ = ["Action", "Step", "get_action", "register_action", "registered_actions"]
