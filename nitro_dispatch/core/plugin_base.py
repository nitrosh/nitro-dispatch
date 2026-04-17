"""Base class every Nitro Dispatch plugin must inherit from."""

from typing import Any, Callable, Dict, List, Optional


class PluginBase:
    """Base class all plugins must inherit from.

    Subclass this and set class-level metadata (``name``, ``version``, ...).
    Decorate methods with :func:`nitro_dispatch.hook` to register them as
    event handlers, or override :meth:`on_load` and register them manually.
    The :class:`PluginManager` instantiates the subclass, collects decorated
    hooks, resolves dependencies, and calls :meth:`on_load`.

    Attributes:
        name: Unique plugin identifier. Defaults to the class name if left
            empty. Used by the manager to look the plugin up.
        version: Semantic version string for the plugin.
        description: Short human-readable summary of what the plugin does.
        author: Plugin author or team name.
        dependencies: Names of other plugins that must load first. The
            manager loads them recursively before this plugin.
        enabled: Whether hooks from this plugin currently execute. Toggled
            by :meth:`PluginManager.enable_plugin` /
            :meth:`PluginManager.disable_plugin`.

    Example:
        >>> from nitro_dispatch import PluginBase, hook
        >>> class WelcomePlugin(PluginBase):
        ...     name = "welcome"
        ...     version = "1.0.0"
        ...
        ...     @hook("user.login", priority=100)
        ...     def greet(self, data):
        ...         data["greeting"] = f"Hi, {data['user']}!"
        ...         return data
    """

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = []

    def __init__(self) -> None:
        """Initialize the plugin instance and collect decorated hooks."""
        self.enabled: bool = False
        self._manager: Optional[Any] = None
        self._hooks: Dict[str, List[Any]] = {}

        # Shadow the class-level mutable list with a per-instance copy so
        # subclasses that mutate self.dependencies don't leak into siblings.
        # Only shadow when the class value is actually a list — otherwise
        # leave the invalid type intact for metadata validation to surface.
        if isinstance(self.__class__.dependencies, list):
            self.dependencies = list(self.__class__.dependencies)

        # Only auto-name if name was not explicitly defined in the plugin class
        # Check if 'name' is in the class's own __dict__ (not inherited from
        # PluginBase)
        if "name" not in self.__class__.__dict__ and not self.name:
            self.name = self.__class__.__name__

        self._collect_decorated_hooks()

    def on_load(self) -> None:
        """Run once when the plugin is loaded by the manager.

        Override to acquire resources, open connections, or register hooks
        manually via :meth:`register_hook`. The default implementation is a
        no-op, so subclasses that only use the ``@hook`` decorator do not
        need to override this.

        Example:
            >>> class LoggerPlugin(PluginBase):
            ...     name = "logger"
            ...
            ...     def on_load(self):
            ...         self.register_hook("before_save", self._log, priority=10)
        """
        pass

    def on_unload(self) -> None:
        """Run once when the plugin is unloaded by the manager.

        Override to release resources acquired in :meth:`on_load`. Called
        from :meth:`PluginManager.unload` and :meth:`PluginManager.reload`
        before the plugin's hooks are detached from the registry.
        """
        pass

    def on_error(self, error: Exception) -> None:
        """Handle exceptions raised by this plugin's hooks.

        Called by the registry whenever one of this plugin's hooks raises
        (including :class:`HookTimeoutError`). Does not supersede the
        configured error strategy — the registry still logs, re-raises, or
        collects the error as configured.

        Args:
            error: The exception raised by the failing hook.
        """
        pass

    def register_hook(
        self,
        event_name: str,
        callback: Callable,
        priority: int = 50,
        timeout: Optional[float] = None,
    ) -> None:
        """Register a callback for an event.

        Prefer the :func:`nitro_dispatch.hook` decorator for static hooks.
        Use this for runtime registration, typically from :meth:`on_load`.
        If the plugin is not yet attached to a manager, the hook is stored
        and registered when the plugin loads.

        Args:
            event_name: Event name to subscribe to. Supports wildcard
                patterns such as ``"user.*"`` or ``"db.before_*"``.
            callback: Callable invoked when the event fires. Receives the
                event data and may return modified data.
            priority: Execution order relative to other hooks for the same
                event — higher runs first. Ties break by registration
                order.
            timeout: Maximum execution time in seconds, or ``None`` for no
                limit. Exceeding raises :class:`HookTimeoutError`.

        Example:
            >>> class MyPlugin(PluginBase):
            ...     name = "my_plugin"
            ...
            ...     def on_load(self):
            ...         self.register_hook("user.login", self.audit, priority=90)
            ...
            ...     def audit(self, data):
            ...         return data
        """
        if self._manager:
            self._manager.register_hook(event_name, callback, self, priority, timeout)
        else:
            if event_name not in self._hooks:
                self._hooks[event_name] = []
            self._hooks[event_name].append(
                {
                    "callback": callback,
                    "priority": priority,
                    "timeout": timeout,
                }
            )

    def unregister_hook(self, event_name: str, callback: Callable) -> None:
        """Detach a previously registered callback from an event.

        A no-op if the plugin is not attached to a manager, or if the
        callback is not registered for ``event_name``.

        Args:
            event_name: Event name the callback was registered under.
            callback: The exact callable passed to :meth:`register_hook`.
        """
        if self._manager:
            self._manager.unregister_hook(event_name, callback, self)

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """Fire an event through this plugin's manager.

        Convenience wrapper so plugins can emit events without needing a
        direct reference to the manager. Returns ``data`` unchanged if the
        plugin is not yet attached to a manager.

        Args:
            event_name: Name of the event to trigger.
            data: Payload passed to each matching hook.

        Returns:
            The payload after every hook in the chain has run.
        """
        if self._manager:
            return self._manager.trigger(event_name, data)
        return data

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a config value scoped to this plugin.

        Looks up ``manager.config[self.name][key]``. Returns ``default`` if
        the plugin is not attached to a manager or the key is absent.

        Args:
            key: Configuration key within this plugin's namespace.
            default: Value to return when the key is not configured.

        Returns:
            The configured value, or ``default`` if unset.
        """
        if self._manager:
            return self._manager.get_plugin_config(self.name, key, default)
        return default

    def _collect_decorated_hooks(self) -> None:
        """Gather @hook-decorated methods into ``self._hooks``.

        Called from ``__init__`` so the manager can register them at load
        time. Skips private/magic attributes to avoid unnecessary access.
        """
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue

            try:
                attr = getattr(self, attr_name)
            except AttributeError:
                continue

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
        """Return a debug-friendly representation including name and version."""
        return f"<{self.__class__.__name__} name='{self.name}' " f"version='{self.version}'>"
