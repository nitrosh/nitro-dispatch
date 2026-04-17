"""Decorators for declaring plugin hooks."""

import asyncio
from functools import wraps
from typing import Callable, Optional


def hook(
    event_name: str,
    priority: int = 50,
    timeout: Optional[float] = None,
    async_hook: bool = False,
) -> Callable:
    """Mark a plugin method as a hook for an event.

    The decorator attaches metadata to the wrapped method;
    :class:`PluginBase` collects every method marked this way when the
    plugin is instantiated and registers them with the manager on load.
    Works for both sync and ``async def`` methods — async is detected
    automatically, so ``async_hook`` is only needed in unusual cases.

    Args:
        event_name: Event to subscribe to. Supports wildcard patterns
            like ``"user.*"`` or ``"db.before_*"``.
        priority: Execution order relative to other hooks on the same
            event — higher runs first. Default 50.
        timeout: Per-call execution limit in seconds, or ``None`` for
            no limit. Exceeding raises :class:`HookTimeoutError`.
        async_hook: Force-mark the method as async. Normally left
            ``False`` — auto-detection covers the common cases.

    Returns:
        A decorator that wraps the method with hook metadata and
        preserves its original signature via :func:`functools.wraps`.

    Example:
        >>> from nitro_dispatch import PluginBase, hook
        >>> class MyPlugin(PluginBase):
        ...     name = "my_plugin"
        ...
        ...     @hook("before_save", priority=100)
        ...     def validate(self, data):
        ...         data["validated"] = True
        ...         return data
        ...
        ...     @hook("user.*", priority=10)
        ...     def log_user_action(self, data):
        ...         return data
        ...
        ...     @hook("fetch_data", timeout=5.0)
        ...     async def fetch(self, data):
        ...         return data
    """

    def decorator(func: Callable) -> Callable:
        is_async = async_hook or asyncio.iscoroutinefunction(func)

        if is_async:

            @wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                return await func(self, *args, **kwargs)

            wrapper = async_wrapper

        else:

            @wraps(func)
            def sync_wrapper(self, *args, **kwargs):
                return func(self, *args, **kwargs)

            wrapper = sync_wrapper

        # Mark the function as a hook with metadata
        wrapper._is_hook = True
        wrapper._event_name = event_name
        wrapper._priority = priority
        wrapper._timeout = timeout
        wrapper._is_async = is_async

        return wrapper

    return decorator
