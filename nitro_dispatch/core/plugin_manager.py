"""Plugin manager that orchestrates plugin lifecycle and hook dispatch."""

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
    """Central orchestrator for plugin lifecycle and event dispatch.

    The manager is the primary entry point for application code. It
    registers plugin classes, instantiates them in dependency order,
    forwards their hooks into an internal :class:`HookRegistry`, and
    exposes :meth:`trigger` / :meth:`trigger_async` to dispatch events.

    Typical workflow: ``register(cls)`` → ``load(name)`` (or
    ``load_all()``) → ``trigger(event, data)``. Plugins can also be
    auto-discovered from a directory with :meth:`discover_plugins` and
    hot-swapped during development with :meth:`reload`.

    Built-in lifecycle events (exposed as class constants) fire at
    registration, load, unload, and error, plus application-level
    ``EVENT_APP_STARTUP`` / ``EVENT_APP_SHUTDOWN`` that callers can
    trigger themselves.

    Example:
        >>> from nitro_dispatch import PluginManager, PluginBase, hook
        >>> class Greeter(PluginBase):
        ...     name = "greeter"
        ...     @hook("user.login")
        ...     def greet(self, data):
        ...         data["greeted"] = True
        ...         return data
        >>> mgr = PluginManager()
        >>> mgr.register(Greeter)
        >>> mgr.load_all()
        ['greeter']
        >>> mgr.trigger("user.login", {"user": "alice"})
        {'user': 'alice', 'greeted': True}
    """

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
    ) -> None:
        """Initialize the manager.

        Args:
            config: Optional per-plugin configuration, keyed by plugin
                name. Exposed to plugins via
                :meth:`PluginBase.get_config`.
            log_level: Root logging level applied via
                ``logging.basicConfig``. Accepts the usual names
                (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
            validate_metadata: When True (default), every registered
                plugin has its ``name``, ``version``, and
                ``dependencies`` attributes validated. Disable for
                prototyping or when registering dynamically generated
                classes.
        """
        self._registry = HookRegistry()
        self._plugins: Dict[str, PluginBase] = {}
        self._plugin_classes: Dict[str, Type[PluginBase]] = {}
        self._config: Dict[str, Any] = config or {}
        self._loaded: bool = False
        self._validate_metadata: bool = validate_metadata

        logging.basicConfig(level=getattr(logging, log_level.upper()))

    def register(self, plugin_class: Type[PluginBase], validate: bool = True) -> None:
        """Register a plugin class so it can later be loaded.

        Registration stores the class — no instance is kept. The class is
        instantiated once temporarily to read its ``name`` and validate
        metadata. Registering a name that already exists overwrites the
        previous registration and logs a warning.

        Triggers ``EVENT_PLUGIN_REGISTERED`` on success.

        Args:
            plugin_class: A subclass of :class:`PluginBase`.
            validate: Per-call override for metadata validation. When
                False, skips validation even if the manager was created
                with ``validate_metadata=True``.

        Raises:
            PluginRegistrationError: If ``plugin_class`` does not inherit
                from :class:`PluginBase`.
            ValidationError: If metadata validation is enabled and the
                plugin's ``name``, ``version``, or ``dependencies`` are
                invalid.

        Example:
            >>> mgr = PluginManager()
            >>> mgr.register(MyPlugin)
        """
        if not issubclass(plugin_class, PluginBase):
            raise PluginRegistrationError(f"{plugin_class.__name__} must inherit from PluginBase")

        temp_instance = plugin_class()
        plugin_name = temp_instance.name

        if validate and self._validate_metadata:
            self._validate_plugin_metadata(temp_instance)

        if plugin_name in self._plugin_classes:
            logger.warning(f"Plugin '{plugin_name}' is already registered. Overwriting.")

        self._plugin_classes[plugin_name] = plugin_class
        logger.info(f"Registered plugin class '{plugin_name}' v{temp_instance.version}")

        self.trigger(
            self.EVENT_PLUGIN_REGISTERED,
            {"plugin_name": plugin_name, "version": temp_instance.version},
        )

    def _validate_plugin_metadata(self, plugin: PluginBase) -> None:
        """Validate ``name``, ``version``, and ``dependencies`` on an instance."""
        if not plugin.name or not isinstance(plugin.name, str):
            raise ValidationError("Plugin must have a valid 'name' attribute")

        if not plugin.version or not isinstance(plugin.version, str):
            raise ValidationError(f"Plugin '{plugin.name}' must have a valid 'version' attribute")

        if not isinstance(plugin.dependencies, list):
            raise ValidationError(f"Plugin '{plugin.name}' dependencies must be a list")

        logger.debug(f"Plugin '{plugin.name}' metadata validated successfully")

    def unregister(self, plugin_name: str) -> None:
        """Remove a plugin's registration, unloading it first if needed.

        Args:
            plugin_name: Name of the plugin to remove.

        Raises:
            PluginNotFoundError: If the plugin is not registered.
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not found")

        if plugin_name in self._plugins:
            self.unload(plugin_name)

        del self._plugin_classes[plugin_name]
        logger.info(f"Unregistered plugin '{plugin_name}'")

    def load(self, plugin_name: str) -> PluginBase:
        """Instantiate a registered plugin and attach its hooks.

        Loading recursively loads every name in the plugin's
        ``dependencies`` first, then instantiates the target class, wires
        up its decorated hooks, and calls :meth:`PluginBase.on_load`.
        If the plugin is already loaded, the existing instance is
        returned and a warning is logged.

        Triggers ``EVENT_PLUGIN_LOADED`` on success and
        ``EVENT_PLUGIN_ERROR`` on failure.

        Args:
            plugin_name: Name of a registered plugin.

        Returns:
            The loaded plugin instance.

        Raises:
            PluginNotFoundError: If the plugin is not registered.
            PluginLoadError: If instantiation, hook registration, or
                ``on_load`` raises.
            DependencyError: If any dependency fails to load.
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not registered")

        if plugin_name in self._plugins:
            logger.warning(f"Plugin '{plugin_name}' already loaded")
            return self._plugins[plugin_name]

        plugin_class = self._plugin_classes[plugin_name]

        try:
            plugin = plugin_class()
            plugin._manager = self

            for dep_name in plugin.dependencies:
                if dep_name not in self._plugins:
                    logger.info(f"Loading dependency '{dep_name}' for '{plugin_name}'")
                    try:
                        self.load(dep_name)
                    except Exception as e:
                        raise DependencyError(
                            f"Failed to load dependency '{dep_name}' for " f"'{plugin_name}': {e}"
                        ) from e

            for event_name, hook_list in plugin._hooks.items():
                for hook_data in hook_list:
                    if isinstance(hook_data, dict):
                        self.register_hook(
                            event_name,
                            hook_data["callback"],
                            plugin,
                            hook_data.get("priority", 50),
                            hook_data.get("timeout"),
                        )
                    else:
                        # Legacy format: bare callable stored without metadata.
                        self.register_hook(event_name, hook_data, plugin)

            plugin.on_load()
            plugin.enabled = True

            self._plugins[plugin_name] = plugin
            logger.info(f"Loaded plugin '{plugin_name}' v{plugin.version}")

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
        """Load every registered plugin, respecting dependencies.

        Iterates registered plugins and calls :meth:`load` on each.
        Individual load failures are logged (not raised) so one broken
        plugin does not block the rest; the returned list contains only
        the names that loaded successfully.

        Returns:
            Names of plugins that loaded successfully, in load order.

        Example:
            >>> mgr = PluginManager()
            >>> mgr.register(PluginA)
            >>> mgr.register(PluginB)
            >>> loaded = mgr.load_all()
        """
        loaded_plugins: List[str] = []
        failed_plugins: List[str] = []

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
        """Unload a single plugin and detach its hooks.

        Calls :meth:`PluginBase.on_unload` before removing hooks from
        the registry. Triggers ``EVENT_PLUGIN_UNLOADED``.

        Args:
            plugin_name: Name of a currently-loaded plugin.

        Raises:
            PluginNotFoundError: If the plugin is not currently loaded.
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        plugin = self._plugins[plugin_name]

        try:
            plugin.on_unload()
            plugin.enabled = False

            for event_name in self._registry.get_all_events():
                hooks = self._registry.get_hooks(event_name)
                for hook_info in hooks:
                    if hook_info["plugin"] == plugin:
                        self._registry.unregister(event_name, hook_info["callback"], plugin)

            del self._plugins[plugin_name]
            logger.info(f"Unloaded plugin '{plugin_name}'")

            self.trigger(self.EVENT_PLUGIN_UNLOADED, {"plugin_name": plugin_name})

        except Exception as e:
            logger.error(f"Error unloading plugin '{plugin_name}': {e}")
            raise

    def unload_all(self) -> None:
        """Unload every currently-loaded plugin.

        Errors from individual unloads are logged and do not halt the
        sweep; the manager's loaded-state flag is cleared on completion.
        """
        plugin_names = list(self._plugins.keys())
        for plugin_name in plugin_names:
            try:
                self.unload(plugin_name)
            except Exception as e:
                logger.error(f"Error unloading plugin '{plugin_name}': {e}")

        self._loaded = False
        logger.info("Unloaded all plugins")

    def reload(self, plugin_name: str) -> PluginBase:
        """Hot-reload a plugin, picking up source changes on disk.

        Unloads the plugin (if loaded), reloads its defining module via
        :func:`importlib.reload`, refreshes the stored class reference
        so the new definition is used, then calls :meth:`load`. Useful
        during development for editing a plugin without restarting the
        host process.

        Args:
            plugin_name: Name of a registered plugin.

        Returns:
            The freshly-loaded plugin instance.

        Raises:
            PluginNotFoundError: If the plugin is not registered.
            PluginLoadError: If the post-reload load fails.
        """
        if plugin_name not in self._plugin_classes:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not registered")

        logger.info(f"Reloading plugin '{plugin_name}'")

        if plugin_name in self._plugins:
            self.unload(plugin_name)

        plugin_class = self._plugin_classes[plugin_name]
        if hasattr(plugin_class, "__module__"):
            module_name = plugin_class.__module__
            if module_name in sys.modules:
                logger.debug(f"Reloading module '{module_name}'")
                reloaded_module = importlib.reload(sys.modules[module_name])

                # importlib.reload replaces the module's classes with new
                # objects. Refresh our stored class reference so the subsequent
                # load() instantiates the new code, not the pre-reload class.
                for _, obj in inspect.getmembers(reloaded_module, inspect.isclass):
                    if (
                        issubclass(obj, PluginBase)
                        and obj is not PluginBase
                        and obj().name == plugin_name
                    ):
                        self._plugin_classes[plugin_name] = obj
                        break

        return self.load(plugin_name)

    def discover_plugins(
        self,
        directory: Union[str, Path],
        pattern: str = "*_plugin.py",
        recursive: bool = False,
    ) -> List[str]:
        """Auto-register every plugin class found in a directory.

        Walks ``directory`` (optionally recursively), imports each file
        matching ``pattern`` by file path, and registers every
        :class:`PluginBase` subclass defined directly in that module.
        Errors from individual files are logged and skipped so one
        broken plugin does not halt discovery.

        Args:
            directory: Directory to search. Expanded and resolved to an
                absolute path.
            pattern: Glob pattern for plugin files. Defaults to
                ``"*_plugin.py"`` — convention, not enforcement.
            recursive: If True, descend into subdirectories.

        Returns:
            Names of plugins successfully registered during this call.

        Raises:
            PluginDiscoveryError: If the directory does not exist, is
                not a directory, or the traversal itself errors.

        Example:
            >>> mgr = PluginManager()
            >>> mgr.discover_plugins("./plugins", recursive=True)
            ['welcome', 'logger']
        """
        directory = Path(directory).expanduser().resolve()

        if not directory.exists() or not directory.is_dir():
            raise PluginDiscoveryError(f"Directory not found: {directory}")

        logger.info(f"Discovering plugins in '{directory}' (pattern: {pattern})")

        discovered: List[str] = []

        try:
            if recursive:
                plugin_files = directory.rglob(pattern)
            else:
                plugin_files = directory.glob(pattern)

            for plugin_file in plugin_files:
                if not plugin_file.is_file():
                    continue

                try:
                    module_name = plugin_file.stem
                    spec = importlib.util.spec_from_file_location(module_name, plugin_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)

                        for name, obj in inspect.getmembers(module, inspect.isclass):
                            if (
                                issubclass(obj, PluginBase)
                                and obj is not PluginBase
                                and obj.__module__ == module_name
                            ):

                                self.register(obj)
                                plugin_name = obj().name
                                discovered.append(plugin_name)
                                logger.debug(
                                    f"Discovered plugin '{plugin_name}' from {plugin_file}"
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
        """Register a callback for an event on the underlying registry.

        Usually called indirectly via :meth:`PluginBase.register_hook`
        or the :func:`nitro_dispatch.hook` decorator. Call this directly
        to attach hooks that don't belong to a plugin (``plugin=None``).

        Args:
            event_name: Event name to subscribe to. Supports wildcards
                like ``"user.*"`` or ``"db.before_*"``.
            callback: Callable invoked when the event fires.
            plugin: Owning plugin instance, or ``None`` for anonymous
                hooks. Disabled plugins have their hooks skipped.
            priority: Execution order — higher runs first.
            timeout: Maximum execution time in seconds, or ``None``.
        """
        self._registry.register(event_name, callback, plugin, priority, timeout)

    def unregister_hook(
        self,
        event_name: str,
        callback: Callable,
        plugin: Optional[PluginBase] = None,
    ) -> None:
        """Detach a previously registered hook from the registry.

        Args:
            event_name: Event name the callback was registered under.
            callback: The exact callable previously passed to
                :meth:`register_hook`.
            plugin: The same owning plugin (or ``None``) used at
                registration; the pair must match exactly.
        """
        self._registry.unregister(event_name, callback, plugin)

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """Fire an event and run matching hooks synchronously.

        Async hooks are skipped with a warning — use
        :meth:`trigger_async` when any listener is ``async def``. Each
        hook that returns a non-``None`` value replaces ``data`` for
        the next hook in the chain.

        Args:
            event_name: Event name to fire. Matched literally plus by
                any wildcard patterns registered against it.
            data: Payload threaded through the hook chain.

        Returns:
            The payload after the last hook returned.

        Raises:
            HookError: If the error strategy is ``fail_fast`` and a
                hook raises.

        Example:
            >>> mgr.trigger("user.login", {"user": "alice"})
            {'user': 'alice', 'greeted': True}
        """
        return self._registry.trigger(event_name, data)

    async def trigger_async(self, event_name: str, data: Any = None) -> Any:
        """Fire an event and run matching hooks asynchronously.

        Handles both sync and async hooks. Sync hooks are dispatched to
        a thread-pool executor so they don't block the event loop; this
        means sync hooks must be thread-safe when invoked this way.

        Args:
            event_name: Event name to fire.
            data: Payload threaded through the hook chain.

        Returns:
            The payload after the last hook returned.

        Raises:
            HookError: If the error strategy is ``fail_fast`` and a
                hook raises.

        Example:
            >>> await mgr.trigger_async("fetch_data", {"id": 42})
        """
        return await self._registry.trigger_async(event_name, data)

    def get_plugin(self, plugin_name: str) -> Optional[PluginBase]:
        """Return a loaded plugin by name, or ``None`` if not loaded.

        Args:
            plugin_name: Name of the plugin to retrieve.

        Returns:
            The plugin instance, or ``None`` if no plugin with that
            name is currently loaded.
        """
        return self._plugins.get(plugin_name)

    def get_all_plugins(self) -> Dict[str, PluginBase]:
        """Return a shallow copy of the loaded-plugins map.

        Returns:
            A new dict mapping plugin name to plugin instance. Mutating
            the returned dict does not affect the manager's state.
        """
        return self._plugins.copy()

    def get_registered_plugins(self) -> List[str]:
        """Return names of every registered plugin class.

        Returns:
            Plugin names, including those not yet loaded.
        """
        return list(self._plugin_classes.keys())

    def get_loaded_plugins(self) -> List[str]:
        """Return names of every currently-loaded plugin.

        Returns:
            Plugin names for loaded instances only.
        """
        return list(self._plugins.keys())

    def is_loaded(self, plugin_name: str) -> bool:
        """Report whether a plugin is currently loaded.

        Args:
            plugin_name: Name of the plugin to check.

        Returns:
            True if a live instance exists; False otherwise.
        """
        return plugin_name in self._plugins

    def enable_plugin(self, plugin_name: str) -> None:
        """Enable a loaded plugin so its hooks execute again.

        Args:
            plugin_name: Name of a loaded plugin.

        Raises:
            PluginNotFoundError: If the plugin is not loaded.
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        self._plugins[plugin_name].enabled = True
        logger.info(f"Enabled plugin '{plugin_name}'")

    def disable_plugin(self, plugin_name: str) -> None:
        """Disable a loaded plugin without unloading it.

        The plugin instance stays loaded, its hooks stay registered,
        but the registry skips them on dispatch. Re-enable with
        :meth:`enable_plugin`.

        Args:
            plugin_name: Name of a loaded plugin.

        Raises:
            PluginNotFoundError: If the plugin is not loaded.
        """
        if plugin_name not in self._plugins:
            raise PluginNotFoundError(f"Plugin '{plugin_name}' not loaded")

        self._plugins[plugin_name].enabled = False
        logger.info(f"Disabled plugin '{plugin_name}'")

    def get_plugin_config(self, plugin_name: str, key: str, default: Any = None) -> Any:
        """Read a config value for a specific plugin.

        Args:
            plugin_name: Plugin namespace (top-level config key).
            key: Key within that plugin's config dict.
            default: Value to return when either the plugin or the key
                is missing.

        Returns:
            The configured value, or ``default`` if unset.
        """
        plugin_config = self._config.get(plugin_name, {})
        return plugin_config.get(key, default)

    def set_error_strategy(self, strategy: str) -> None:
        """Choose how hook exceptions are handled during dispatch.

        Strategies:
            - ``"log_and_continue"`` (default): log the error and run
              the next hook.
            - ``"fail_fast"``: raise :class:`HookError` and abort the
              chain.
            - ``"collect_all"``: run every hook, then log a summary.

        Args:
            strategy: One of the values above.

        Raises:
            ValueError: If ``strategy`` is not one of the listed names.
        """
        self._registry.set_error_strategy(strategy)

    def enable_hook_tracing(self, enabled: bool = True) -> None:
        """Toggle per-hook timing logs for debugging.

        When enabled, every dispatch logs the elapsed time of each
        hook at DEBUG level. Combine with ``log_level="DEBUG"`` on
        the manager to actually see the output.

        Args:
            enabled: True to turn tracing on, False to turn it off.
        """
        self._registry.enable_hook_tracing(enabled)

    def get_events(self) -> List[str]:
        """Return every event name with at least one registered hook.

        Returns:
            Event names — includes wildcard patterns (e.g. ``"user.*"``)
            as they were registered.
        """
        return self._registry.get_all_events()
