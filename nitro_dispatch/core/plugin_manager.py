"""
Plugin manager for orchestrating plugin lifecycle and hooks.
"""

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union
import logging

from .plugin_base import PluginBase
from .hook_registry import HookRegistry
from .exceptions import (
    PluginLoadError,
    PluginRegistrationError,
    PluginNotFoundError,
    DependencyError,
    ValidationError,
    PluginDiscoveryError,
)

logger = logging.getLogger(__name__)


class PluginManager:
    """
    Central orchestrator for managing plugins and their lifecycle.

    The PluginManager handles:
    - Plugin registration and loading
    - Hook management and event triggering (sync and async)
    - Dependency resolution
    - Plugin configuration
    - Error handling and isolation
    - Plugin discovery from directories
    - Hot reloading
    - Metadata validation
    """

    # Built-in lifecycle events
    EVENT_PLUGIN_REGISTERED = "nitro.plugin.registered"
    EVENT_PLUGIN_LOADED = "nitro.plugin.loaded"
    EVENT_PLUGIN_UNLOADED = "nitro.plugin.unloaded"
    EVENT_PLUGIN_ERROR = "nitro.plugin.error"
    EVENT_APP_STARTUP = "nitro.app.startup"
    EVENT_APP_SHUTDOWN = "nitro.app.shutdown"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        log_level: str = "INFO",
        validate_metadata: bool = True,
    ):
        """
        Initialize the plugin manager.

        Args:
            config: Optional configuration dictionary for plugins
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
            validate_metadata: Whether to validate plugin metadata on
                registration
        """
        self._registry = HookRegistry()
        self._plugins: Dict[str, PluginBase] = {}
        self._plugin_classes: Dict[str, Type[PluginBase]] = {}
        self._config: Dict[str, Any] = config or {}
        self._loaded: bool = False
        self._validate_metadata: bool = validate_metadata

        # Configure logging
        logging.basicConfig(level=getattr(logging, log_level.upper()))

    def register(self, plugin_class: Type[PluginBase], validate: bool = True) -> None:
        """
        Register a plugin class.

        Args:
            plugin_class: Plugin class that inherits from PluginBase
            validate: Whether to validate plugin metadata

        Raises:
            PluginRegistrationError: If registration fails
            ValidationError: If metadata validation fails
        """
        if not issubclass(plugin_class, PluginBase):
            raise PluginRegistrationError(f"{plugin_class.__name__} must inherit from PluginBase")

        # Create temporary instance to get name and validate
        temp_instance = plugin_class()
        plugin_name = temp_instance.name

        # Validate metadata if enabled
        if validate and self._validate_metadata:
            self._validate_plugin_metadata(temp_instance)

        if plugin_name in self._plugin_classes:
            logger.warning(f"Plugin '{plugin_name}' is already registered. Overwriting.")

        self._plugin_classes[plugin_name] = plugin_class
        logger.info(f"Registered plugin class '{plugin_name}' v{temp_instance.version}")

        # Trigger lifecycle event
        self.trigger(
            self.EVENT_PLUGIN_REGISTERED,
            {"plugin_name": plugin_name, "version": temp_instance.version},
        )

    def _validate_plugin_metadata(self, plugin: PluginBase) -> None:
        """
        Validate plugin metadata.

        Args:
            plugin: Plugin instance to validate

        Raises:
            ValidationError: If validation fails
        """
        if not plugin.name or not isinstance(plugin.name, str):
            raise ValidationError("Plugin must have a valid 'name' attribute")

        if not plugin.version or not isinstance(plugin.version, str):
            raise ValidationError(f"Plugin '{plugin.name}' must have a valid 'version' attribute")

        if not isinstance(plugin.dependencies, list):
            raise ValidationError(f"Plugin '{plugin.name}' dependencies must be a list")

        logger.debug(f"Plugin '{plugin.name}' metadata validated successfully")

    def unregister(self, plugin_name: str) -> None:
        """
        Unregister and unload a plugin.

        Args:
            plugin_name: Name of the plugin to unregister

        Raises:
            PluginNotFoundError: If plugin is not found
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not found")

        # Unload if loaded
        if plugin_name in self._plugins:
            self.unload(plugin_name)

        del self._plugin_classes[plugin_name]
        logger.info(f"Unregistered plugin '{plugin_name}'")

    def load(self, plugin_name: str) -> PluginBase:
        """
        Load a specific plugin.

        Args:
            plugin_name: Name of the plugin to load

        Returns:
            Loaded plugin instance

        Raises:
            PluginNotFoundError: If plugin is not registered
            PluginLoadError: If loading fails
            DependencyError: If dependencies cannot be resolved
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not registered")

        if plugin_name in self._plugins:
            logger.warning(f"Plugin '{plugin_name}' already loaded")
            return self._plugins[plugin_name]

        plugin_class = self._plugin_classes[plugin_name]

        try:
            # Create plugin instance
            plugin = plugin_class()
            plugin._manager = self

            # Check and load dependencies
            for dep_name in plugin.dependencies:
                if dep_name not in self._plugins:
                    logger.info(f"Loading dependency '{dep_name}' for '{plugin_name}'")
                    try:
                        self.load(dep_name)
                    except Exception as e:
                        raise DependencyError(
                            f"Failed to load dependency '{dep_name}' for " f"'{plugin_name}': {e}"
                        ) from e

            # Register hooks that were stored during initialization
            for event_name, hook_list in plugin._hooks.items():
                for hook_data in hook_list:
                    if isinstance(hook_data, dict):
                        # New format with metadata
                        self.register_hook(
                            event_name,
                            hook_data["callback"],
                            plugin,
                            hook_data.get("priority", 50),
                            hook_data.get("timeout"),
                        )
                    else:
                        # Old format (just callback)
                        self.register_hook(event_name, hook_data, plugin)

            # Call on_load hook
            plugin.on_load()
            plugin.enabled = True

            self._plugins[plugin_name] = plugin
            logger.info(f"Loaded plugin '{plugin_name}' v{plugin.version}")

            # Trigger lifecycle event
            self.trigger(
                self.EVENT_PLUGIN_LOADED,
                {"plugin_name": plugin_name, "version": plugin.version},
            )

            return plugin

        except Exception as e:
            error_data = {
                "plugin_name": plugin_name,
                "error": str(e),
                "type": type(e).__name__,
            }
            self.trigger(self.EVENT_PLUGIN_ERROR, error_data)
            raise PluginLoadError(f"Failed to load plugin '{plugin_name}': {e}") from e

    def load_all(self) -> List[str]:
        """
        Load all registered plugins in dependency order.

        Returns:
            List of loaded plugin names

        Raises:
            PluginLoadError: If any plugin fails to load
        """
        loaded_plugins = []
        failed_plugins = []

        # Try to load all plugins, dependencies will be loaded automatically
        for plugin_name in self._plugin_classes.keys():
            if plugin_name not in self._plugins:
                try:
                    self.load(plugin_name)
                    loaded_plugins.append(plugin_name)
                except Exception as e:
                    logger.error(f"Failed to load plugin '{plugin_name}': {e}")
                    failed_plugins.append(plugin_name)

        self._loaded = True
        logger.info(f"Loaded {len(loaded_plugins)} plugins. " f"Failed: {len(failed_plugins)}")

        if failed_plugins:
            logger.warning(f"Failed plugins: {', '.join(failed_plugins)}")

        return loaded_plugins

    def unload(self, plugin_name: str) -> None:
        """
        Unload a specific plugin.

        Args:
            plugin_name: Name of the plugin to unload

        Raises:
            PluginNotFoundError: If plugin is not loaded
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        plugin = self._plugins[plugin_name]

        try:
            # Call on_unload hook
            plugin.on_unload()
            plugin.enabled = False

            # Remove all hooks from this plugin
            for event_name in self._registry.get_all_events():
                hooks = self._registry.get_hooks(event_name)
                for hook_info in hooks:
                    if hook_info["plugin"] == plugin:
                        self._registry.unregister(event_name, hook_info["callback"], plugin)

            del self._plugins[plugin_name]
            logger.info(f"Unloaded plugin '{plugin_name}'")

            # Trigger lifecycle event
            self.trigger(self.EVENT_PLUGIN_UNLOADED, {"plugin_name": plugin_name})

        except Exception as e:
            logger.error(f"Error unloading plugin '{plugin_name}': {e}")
            raise

    def unload_all(self) -> None:
        """Unload all loaded plugins."""
        plugin_names = list(self._plugins.keys())
        for plugin_name in plugin_names:
            try:
                self.unload(plugin_name)
            except Exception as e:
                logger.error(f"Error unloading plugin '{plugin_name}': {e}")

        self._loaded = False
        logger.info("Unloaded all plugins")

    def reload(self, plugin_name: str) -> PluginBase:
        """
        Hot reload a plugin (unload and load again).

        Args:
            plugin_name: Name of the plugin to reload

        Returns:
            Reloaded plugin instance

        Raises:
            PluginNotFoundError: If plugin is not found
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not registered")

        logger.info(f"Reloading plugin '{plugin_name}'")

        # Unload if currently loaded
        if plugin_name in self._plugins:
            self.unload(plugin_name)

        # Reload the plugin module if it's a module-based plugin
        plugin_class = self._plugin_classes[plugin_name]
        if hasattr(plugin_class, "__module__"):
            module_name = plugin_class.__module__
            if module_name in sys.modules:
                logger.debug(f"Reloading module '{module_name}'")
                importlib.reload(sys.modules[module_name])

        # Load the plugin
        return self.load(plugin_name)

    def discover_plugins(
        self,
        directory: Union[str, Path],
        pattern: str = "*_plugin.py",
        recursive: bool = False,
    ) -> List[str]:
        """
        Discover and register plugins from a directory.

        Args:
            directory: Directory path to search for plugins
            pattern: File pattern to match (supports glob patterns)
            recursive: Whether to search recursively

        Returns:
            List of discovered plugin names

        Raises:
            PluginDiscoveryError: If discovery fails
        """
        directory = Path(directory).expanduser().resolve()

        if not directory.exists() or not directory.is_dir():
            raise PluginDiscoveryError(f"Directory not found: {directory}")

        logger.info(f"Discovering plugins in '{directory}' (pattern: {pattern})")

        discovered = []

        try:
            # Find matching files
            if recursive:
                plugin_files = directory.rglob(pattern)
            else:
                plugin_files = directory.glob(pattern)

            for plugin_file in plugin_files:
                if not plugin_file.is_file():
                    continue

                try:
                    # Load the module
                    module_name = plugin_file.stem
                    spec = importlib.util.spec_from_file_location(module_name, plugin_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)

                        # Find PluginBase subclasses in the module
                        for name, obj in inspect.getmembers(module, inspect.isclass):
                            if (
                                issubclass(obj, PluginBase)
                                and obj is not PluginBase
                                and obj.__module__ == module_name
                            ):

                                self.register(obj)
                                discovered.append(obj().name)
                                logger.debug(
                                    f"Discovered plugin '{obj().name}' from " f"{plugin_file}"
                                )

                except Exception as e:
                    logger.error(f"Error loading plugin from {plugin_file}: {e}")
                    continue

            logger.info(f"Discovered {len(discovered)} plugins")
            return discovered

        except Exception as e:
            raise PluginDiscoveryError(f"Plugin discovery failed: {e}") from e

    def register_hook(
        self,
        event_name: str,
        callback: Callable,
        plugin: Optional[PluginBase] = None,
        priority: int = 50,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Register a hook for an event.

        Args:
            event_name: Name of the event
            callback: Function to call when event is triggered
            plugin: Plugin instance (optional)
            priority: Execution priority (higher = earlier)
            timeout: Maximum execution time in seconds
        """
        self._registry.register(event_name, callback, plugin, priority, timeout)

    def unregister_hook(
        self,
        event_name: str,
        callback: Callable,
        plugin: Optional[PluginBase] = None,
    ) -> None:
        """
        Unregister a hook for an event.

        Args:
            event_name: Name of the event
            callback: Function to unregister
            plugin: Plugin instance (optional)
        """
        self._registry.unregister(event_name, callback, plugin)

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """
        Trigger an event and execute all registered hooks synchronously.

        Args:
            event_name: Name of the event to trigger
            data: Data to pass to hooks

        Returns:
            Modified data after passing through all hooks
        """
        return self._registry.trigger(event_name, data)

    async def trigger_async(self, event_name: str, data: Any = None) -> Any:
        """
        Trigger an event and execute all registered hooks asynchronously.

        Args:
            event_name: Name of the event to trigger
            data: Data to pass to hooks

        Returns:
            Modified data after passing through all hooks
        """
        return await self._registry.trigger_async(event_name, data)

    def get_plugin(self, plugin_name: str) -> Optional[PluginBase]:
        """
        Get a loaded plugin by name.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Plugin instance or None if not loaded
        """
        return self._plugins.get(plugin_name)

    def get_all_plugins(self) -> Dict[str, PluginBase]:
        """
        Get all loaded plugins.

        Returns:
            Dictionary of plugin name to plugin instance
        """
        return self._plugins.copy()

    def get_registered_plugins(self) -> List[str]:
        """
        Get names of all registered plugin classes.

        Returns:
            List of plugin names
        """
        return list(self._plugin_classes.keys())

    def get_loaded_plugins(self) -> List[str]:
        """
        Get names of all loaded plugins.

        Returns:
            List of plugin names
        """
        return list(self._plugins.keys())

    def is_loaded(self, plugin_name: str) -> bool:
        """
        Check if a plugin is loaded.

        Args:
            plugin_name: Name of the plugin

        Returns:
            True if plugin is loaded, False otherwise
        """
        return plugin_name in self._plugins

    def enable_plugin(self, plugin_name: str) -> None:
        """
        Enable a loaded plugin.

        Args:
            plugin_name: Name of the plugin

        Raises:
            PluginNotFoundError: If plugin is not loaded
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        self._plugins[plugin_name].enabled = True
        logger.info(f"Enabled plugin '{plugin_name}'")

    def disable_plugin(self, plugin_name: str) -> None:
        """
        Disable a loaded plugin (keeps it loaded but hooks won't execute).

        Args:
            plugin_name: Name of the plugin

        Raises:
            PluginNotFoundError: If plugin is not loaded
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        self._plugins[plugin_name].enabled = False
        logger.info(f"Disabled plugin '{plugin_name}'")

    def get_plugin_config(self, plugin_name: str, key: str, default: Any = None) -> Any:
        """
        Get configuration value for a plugin.

        Args:
            plugin_name: Name of the plugin
            key: Configuration key
            default: Default value if not found

        Returns:
            Configuration value or default
        """
        plugin_config = self._config.get(plugin_name, {})
        return plugin_config.get(key, default)

    def set_error_strategy(self, strategy: str) -> None:
        """
        Set the error handling strategy for hooks.

        Args:
            strategy: One of 'log_and_continue', 'fail_fast', 'collect_all'
        """
        self._registry.set_error_strategy(strategy)

    def enable_hook_tracing(self, enabled: bool = True) -> None:
        """
        Enable or disable hook execution tracing for debugging.

        Args:
            enabled: Whether to enable tracing
        """
        self._registry.enable_hook_tracing(enabled)

    def get_events(self) -> List[str]:
        """
        Get all registered event names.

        Returns:
            List of event names
        """
        return self._registry.get_all_events()
