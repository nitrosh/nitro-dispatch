"""
Base class for all Nitro plugins.
"""

from typing import Any, Dict, List, Optional


class PluginBase:
    """
    Base class that all plugins must inherit from.

    Attributes:
        name: Unique identifier for the plugin
        version: Plugin version string
        description: Human-readable description
        author: Plugin author
        dependencies: List of plugin names this plugin depends on
        enabled: Whether the plugin is currently enabled
    """

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = []

    def __init__(self):
        """Initialize the plugin."""
        self.enabled: bool = False
        self._manager: Optional[Any] = None
        self._hooks: Dict[str, List[callable]] = {}

        # Only auto-name if name was not explicitly defined in the plugin class
        # Check if 'name' is in the class's own __dict__ (not inherited from
        # PluginBase)
        if "name" not in self.__class__.__dict__ and not self.name:
            self.name = self.__class__.__name__

        # Auto-collect decorated hooks
        self._collect_decorated_hooks()

    def on_load(self) -> None:
        """
        Called when the plugin is loaded.
        Override this method to register hooks and initialize resources.
        """
        pass

    def on_unload(self) -> None:
        """
        Called when the plugin is unloaded.
        Override this method to cleanup resources.
        """
        pass

    def on_error(self, error: Exception) -> None:
        """
        Called when an error occurs during hook execution.

        Args:
            error: The exception that was raised
        """
        pass

    def register_hook(
        self,
        event_name: str,
        callback: callable,
        priority: int = 50,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Register a callback for a specific event.

        Args:
            event_name: Name of the event to listen for
            callback: Function to call when event is triggered
            priority: Execution priority (higher = earlier). Default: 50
            timeout: Maximum execution time in seconds. None = no timeout
        """
        if self._manager:
            self._manager.register_hook(event_name, callback, self, priority, timeout)
        else:
            # Store for later registration with metadata
            if event_name not in self._hooks:
                self._hooks[event_name] = []
            self._hooks[event_name].append(
                {
                    "callback": callback,
                    "priority": priority,
                    "timeout": timeout,
                }
            )

    def unregister_hook(self, event_name: str, callback: callable) -> None:
        """
        Unregister a callback for a specific event.

        Args:
            event_name: Name of the event
            callback: Function to unregister
        """
        if self._manager:
            self._manager.unregister_hook(event_name, callback, self)

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """
        Trigger an event from within the plugin.

        Args:
            event_name: Name of the event to trigger
            data: Data to pass to event handlers

        Returns:
            Modified data after passing through all hooks
        """
        if self._manager:
            return self._manager.trigger(event_name, data)
        return data

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value for this plugin.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        if self._manager:
            return self._manager.get_plugin_config(self.name, key, default)
        return default

    def _collect_decorated_hooks(self) -> None:
        """
        Collect all methods decorated with @hook and store them.
        They will be registered when the plugin is loaded.
        """
        for attr_name in dir(self):
            # Skip private/magic methods
            if attr_name.startswith("_"):
                continue

            try:
                attr = getattr(self, attr_name)
            except AttributeError:
                continue

            # Check if it's a hook-decorated method
            if callable(attr) and hasattr(attr, "_is_hook") and attr._is_hook:
                event_name = attr._event_name
                priority = getattr(attr, "_priority", 50)
                timeout = getattr(attr, "_timeout", None)

                if event_name not in self._hooks:
                    self._hooks[event_name] = []
                self._hooks[event_name].append(
                    {
                        "callback": attr,
                        "priority": priority,
                        "timeout": timeout,
                    }
                )

    def __repr__(self) -> str:
        """String representation of the plugin."""
        return f"<{self.__class__.__name__} name='{self.name}' " f"version='{self.version}'>"
