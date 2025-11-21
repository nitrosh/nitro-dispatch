"""
Nitro Dispatch - A framework-agnostic plugin system for Python.

Nitro Dispatch provides a simple yet powerful way to add plugin functionality
to your Python applications with custom hooks, events, and data filtering.

Features:
- Plugin registration and loading
- Hook system with @hook decorator
- Data filtering and transformation
- Async/await support
- Priority-based hook execution
- Timeout protection
- Event namespacing with wildcards
- Plugin discovery from directories
- Hot reloading
- Error isolation with multiple strategies
- Built-in lifecycle events

Example:
    from nitro_dispatch import PluginManager, PluginBase, hook

    class MyPlugin(PluginBase):
        name = "my_plugin"

        @hook('before_save', priority=100)
        def validate_data(self, data):
            data['validated'] = True
            return data

    manager = PluginManager()
    manager.register(MyPlugin)
    manager.load_all()
    result = manager.trigger('before_save', {'key': 'value'})
"""

__version__ = "1.0.0"
__author__ = "Sean Nieuwoudt"
__license__ = "MIT"

from .core import (
    PluginBase,
    PluginManager,
    HookRegistry,
    NitroPluginError,
    PluginLoadError,
    PluginRegistrationError,
    HookError,
    PluginNotFoundError,
    DependencyError,
    StopPropagation,
    HookTimeoutError,
    ValidationError,
    PluginDiscoveryError,
)
from .utils import hook

__all__ = [
    "PluginManager",
    "PluginBase",
    "HookRegistry",
    "hook",
    "NitroPluginError",
    "PluginLoadError",
    "PluginRegistrationError",
    "HookError",
    "PluginNotFoundError",
    "DependencyError",
    "StopPropagation",
    "HookTimeoutError",
    "ValidationError",
    "PluginDiscoveryError",
]
