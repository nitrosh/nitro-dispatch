"""
Custom exceptions for Nitro Plugins.
"""


class NitroPluginError(Exception):
    """Base exception for all Nitro Plugin errors."""

    pass


class PluginLoadError(NitroPluginError):
    """Raised when a plugin fails to load."""

    pass


class PluginRegistrationError(NitroPluginError):
    """Raised when plugin registration fails."""

    pass


class HookError(NitroPluginError):
    """Raised when hook execution fails."""

    pass


class PluginNotFoundError(NitroPluginError):
    """Raised when a requested plugin is not found."""

    pass


class DependencyError(NitroPluginError):
    """Raised when plugin dependencies cannot be resolved."""

    pass


class StopPropagation(NitroPluginError):
    """Raised to stop hook propagation in the event chain."""

    pass


class HookTimeoutError(NitroPluginError):
    """Raised when a hook exceeds its timeout."""

    pass


class ValidationError(NitroPluginError):
    """Raised when plugin metadata validation fails."""

    pass


class PluginDiscoveryError(NitroPluginError):
    """Raised when plugin discovery fails."""

    pass
