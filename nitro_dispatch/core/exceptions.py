"""Exception hierarchy for Nitro Dispatch.

All exceptions derive from :class:`NitroPluginError`, so callers can catch the
base class to handle any dispatch-related failure with a single ``except``.
"""


class NitroPluginError(Exception):
    """Base class for every exception raised by Nitro Dispatch.

    Catch this to handle any plugin or hook failure without enumerating
    subclasses. All other exceptions in this module inherit from it.
    """

    pass


class PluginLoadError(NitroPluginError):
    """Raised when a plugin cannot be loaded.

    Typical causes: the plugin's ``on_load`` raised, a dependency failed to
    load, or hook registration errored. The original exception is attached
    via ``__cause__``.
    """

    pass


class PluginRegistrationError(NitroPluginError):
    """Raised when a class cannot be registered as a plugin.

    Most commonly raised because the supplied class does not inherit from
    :class:`PluginBase`.
    """

    pass


class HookError(NitroPluginError):
    """Raised when a hook fails under the ``fail_fast`` error strategy.

    Wraps the underlying exception (available via ``__cause__``) and is only
    raised when the registry's error strategy is set to ``fail_fast``. Under
    ``log_and_continue`` or ``collect_all`` the original error is logged or
    collected instead.
    """

    pass


class PluginNotFoundError(NitroPluginError):
    """Raised when an operation targets a plugin that is not known.

    Thrown by lookups such as :meth:`PluginManager.load`,
    :meth:`PluginManager.unload`, :meth:`PluginManager.reload`, and
    :meth:`PluginManager.enable_plugin` when the given plugin name is not
    registered (or not loaded, where applicable).
    """

    pass


class DependencyError(NitroPluginError):
    """Raised when a plugin's declared dependency cannot be loaded.

    Raised from :meth:`PluginManager.load` when a name listed in
    ``dependencies`` is not registered or itself fails to load. The triggering
    exception is attached via ``__cause__``.
    """

    pass


class StopPropagation(NitroPluginError):
    """Raised by a hook to halt the remaining hook chain for an event.

    Hooks registered with a lower priority (or registered later at the same
    priority) will not run. The current accumulated data is returned to the
    caller of :meth:`trigger` / :meth:`trigger_async`.

    Example:
        >>> class Gatekeeper(PluginBase):
        ...     name = "gatekeeper"
        ...
        ...     @hook("user.login", priority=100)
        ...     def deny_banned(self, data):
        ...         if data.get("banned"):
        ...             raise StopPropagation("user is banned")
        ...         return data
    """

    pass


class HookTimeoutError(NitroPluginError):
    """Raised when a hook exceeds its configured ``timeout``.

    For sync hooks this surfaces a ``concurrent.futures.TimeoutError``; for
    async hooks it surfaces an ``asyncio.TimeoutError``. The message includes
    the configured timeout in seconds.
    """

    pass


class ValidationError(NitroPluginError):
    """Raised when a plugin's metadata fails validation at registration.

    Triggered by :meth:`PluginManager.register` when ``name``, ``version``,
    or ``dependencies`` are missing, empty, or of the wrong type. Disable by
    passing ``validate_metadata=False`` to :class:`PluginManager` or
    ``validate=False`` to :meth:`register`.
    """

    pass


class PluginDiscoveryError(NitroPluginError):
    """Raised when :meth:`PluginManager.discover_plugins` fails.

    Most commonly because the target directory does not exist or is not a
    directory. Errors loading individual discovered files are logged and
    skipped rather than raised.
    """

    pass
