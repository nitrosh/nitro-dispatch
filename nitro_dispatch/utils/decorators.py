"""
Decorators for Nitro Plugins.
"""

import asyncio
from functools import wraps
from typing import Callable, Optional


def hook(
    event_name: str,
    priority: int = 50,
    timeout: Optional[float] = None,
    async_hook: bool = False,
) -> Callable:
    """
    Decorator to mark a method as a hook for a specific event.

    This decorator automatically registers the method as a hook
    when the plugin is loaded.

    Args:
        event_name: Name of the event to listen for (supports wildcards:
            'user.*')
        priority: Execution priority (higher = earlier). Default: 50
        timeout: Maximum execution time in seconds. None = no timeout
        async_hook: Whether this is an async hook (auto-detected if not
            specified)

    Example:
        class MyPlugin(PluginBase):
            @hook('before_save', priority=100)
            def validate_data(self, data):
                data['validated'] = True
                return data

            @hook('user.*', priority=10)  # Wildcard event
            def log_user_action(self, data):
                return data

            @hook('fetch_data', timeout=5.0)
            async def fetch_async(self, data):
                result = await some_api()
                return result
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
