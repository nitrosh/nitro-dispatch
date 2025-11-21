"""
Hook registry for managing event subscriptions and triggers.
"""

import asyncio
import re
import signal
from typing import Any, Callable, Dict, List, Optional
import logging

from .exceptions import HookError, StopPropagation, HookTimeoutError

logger = logging.getLogger(__name__)


def timeout_handler(signum, frame):
    """Signal handler for hook timeout."""
    raise HookTimeoutError("Hook execution exceeded timeout")


class HookRegistry:
    """
    Manages hook registration and event triggering.

    This class maintains a registry of event hooks and provides both
    synchronous and asynchronous execution with data filtering capabilities.

    Features:
    - Priority-based execution
    - Timeout protection
    - Async/await support
    - Event namespacing with wildcards
    - Stop propagation support
    - Enable/disable plugin filtering
    """

    def __init__(self):
        """Initialize the hook registry."""
        self._hooks: Dict[str, List[Dict[str, Any]]] = {}
        self._error_strategy: str = "log_and_continue"
        self._hook_tracing: bool = False

    def register(
        self,
        event_name: str,
        callback: Callable,
        plugin: Optional[Any] = None,
        priority: int = 50,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Register a callback for an event.

        Args:
            event_name: Name of the event (supports wildcards: 'user.*')
            callback: Function to call when event is triggered
            plugin: Plugin instance that owns this hook (optional)
            priority: Execution priority (higher = earlier). Default: 50
            timeout: Maximum execution time in seconds
        """
        if event_name not in self._hooks:
            self._hooks[event_name] = []

        hook_info = {
            "callback": callback,
            "plugin": plugin,
            "plugin_name": plugin.name if plugin else "anonymous",
            "priority": priority,
            "timeout": timeout,
            "is_async": asyncio.iscoroutinefunction(callback),
        }

        self._hooks[event_name].append(hook_info)

        # Sort hooks by priority (higher priority first)
        self._hooks[event_name].sort(key=lambda h: h["priority"], reverse=True)

        logger.debug(
            f"Registered hook '{event_name}' from plugin "
            f"'{hook_info['plugin_name']}' (priority={priority}, "
            f"timeout={timeout})"
        )

    def unregister(self, event_name: str, callback: Callable, plugin: Optional[Any] = None) -> bool:
        """
        Unregister a callback for an event.

        Args:
            event_name: Name of the event
            callback: Function to unregister
            plugin: Plugin instance (optional)

        Returns:
            True if hook was found and removed, False otherwise
        """
        if event_name not in self._hooks:
            return False

        original_length = len(self._hooks[event_name])
        self._hooks[event_name] = [
            hook
            for hook in self._hooks[event_name]
            if not (hook["callback"] == callback and hook["plugin"] == plugin)
        ]

        removed = len(self._hooks[event_name]) < original_length
        if removed:
            logger.debug(f"Unregistered hook '{event_name}'")
        return removed

    def _match_event_pattern(self, pattern: str, event: str) -> bool:
        """
        Check if an event matches a pattern (with wildcard support).

        Args:
            pattern: Pattern to match (e.g., 'user.*', 'db.before_*')
            event: Event name to check

        Returns:
            True if event matches pattern
        """
        # Convert wildcard pattern to regex
        regex_pattern = pattern.replace(".", r"\.").replace("*", ".*")
        regex_pattern = f"^{regex_pattern}$"
        return bool(re.match(regex_pattern, event))

    def _get_matching_hooks(self, event_name: str) -> List[Dict[str, Any]]:
        """
        Get all hooks that match the event name (including wildcards).

        Args:
            event_name: Event name to match

        Returns:
            List of matching hook information dictionaries
        """
        matching_hooks = []

        for registered_event, hooks in self._hooks.items():
            # Exact match
            if registered_event == event_name:
                matching_hooks.extend(hooks)
            # Wildcard match
            elif "*" in registered_event:
                if self._match_event_pattern(registered_event, event_name):
                    matching_hooks.extend(hooks)

        # Sort by priority (higher first)
        matching_hooks.sort(key=lambda h: h["priority"], reverse=True)

        return matching_hooks

    def _execute_hook_with_timeout(
        self, callback: Callable, data: Any, timeout: Optional[float]
    ) -> Any:
        """
        Execute a synchronous hook with optional timeout.

        Args:
            callback: Hook callback function
            data: Data to pass to callback
            timeout: Timeout in seconds (None = no timeout)

        Returns:
            Result from callback

        Raises:
            HookTimeoutError: If execution exceeds timeout
        """
        if timeout is None:
            return callback(data)

        # Use signal-based timeout for synchronous code
        try:
            # Set timeout signal
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout)

            try:
                result = callback(data)
            finally:
                # Cancel the timer
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)

            return result
        except HookTimeoutError:
            raise
        except Exception:
            # Re-raise other exceptions
            raise

    async def _execute_async_hook_with_timeout(
        self, callback: Callable, data: Any, timeout: Optional[float]
    ) -> Any:
        """
        Execute an async hook with optional timeout.

        Args:
            callback: Async hook callback function
            data: Data to pass to callback
            timeout: Timeout in seconds (None = no timeout)

        Returns:
            Result from callback

        Raises:
            HookTimeoutError: If execution exceeds timeout
        """
        if timeout is None:
            return await callback(data)

        try:
            return await asyncio.wait_for(callback(data), timeout=timeout)
        except asyncio.TimeoutError:
            raise HookTimeoutError(f"Async hook execution exceeded timeout of {timeout}s")

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """
        Trigger an event and execute all registered hooks synchronously.

        Hooks are executed in priority order (highest first).
        Each hook can modify the data, which is passed to the next hook.

        Args:
            event_name: Name of the event to trigger
            data: Data to pass to hooks (can be modified by hooks)

        Returns:
            Modified data after passing through all hooks

        Raises:
            HookError: If error_strategy is 'fail_fast' and a hook fails
            StopPropagation: If a hook raises this to stop the chain
        """
        hooks = self._get_matching_hooks(event_name)

        if not hooks:
            logger.debug(f"No hooks registered for event '{event_name}'")
            return data

        if self._hook_tracing:
            logger.debug(f"Triggering event '{event_name}' with {len(hooks)} hooks")

        errors = []
        result = data

        for hook_info in hooks:
            callback = hook_info["callback"]
            plugin = hook_info["plugin"]
            plugin_name = hook_info["plugin_name"]
            priority = hook_info["priority"]
            timeout = hook_info["timeout"]
            is_async = hook_info["is_async"]

            # Skip disabled plugins
            if plugin and hasattr(plugin, "enabled") and not plugin.enabled:
                logger.debug(f"Skipping hook from disabled plugin '{plugin_name}'")
                continue

            # Can't execute async hooks in sync context
            if is_async:
                logger.warning(
                    f"Skipping async hook '{plugin_name}' in sync trigger. "
                    f"Use trigger_async() instead."
                )
                continue

            try:
                import time

                start_time = time.time() if self._hook_tracing else None

                # Execute hook with timeout
                new_result = self._execute_hook_with_timeout(callback, result, timeout)

                if self._hook_tracing:
                    elapsed = time.time() - start_time
                    logger.debug(
                        f"Hook '{event_name}' from '{plugin_name}' "
                        f"(priority={priority}) executed in {elapsed:.4f}s"
                    )

                # Only update result if callback returned something
                if new_result is not None:
                    result = new_result

            except StopPropagation as e:
                logger.info(
                    f"Hook propagation stopped by '{plugin_name}' " f"for event '{event_name}': {e}"
                )
                break

            except HookTimeoutError as e:
                error_msg = f"Hook '{event_name}' from plugin '{plugin_name}' " f"timed out: {e}"
                logger.error(error_msg)

                if plugin and hasattr(plugin, "on_error"):
                    try:
                        plugin.on_error(e)
                    except Exception as notify_error:
                        logger.error(f"Error in plugin error handler: {notify_error}")

                if self._error_strategy == "fail_fast":
                    raise HookError(error_msg) from e
                elif self._error_strategy == "collect_all":
                    errors.append(
                        {
                            "plugin": plugin_name,
                            "error": e,
                            "event": event_name,
                        }
                    )

            except Exception as e:
                error_msg = (
                    f"Error executing hook '{event_name}' from plugin " f"'{plugin_name}': {e}"
                )
                logger.error(error_msg)

                # Notify plugin of error
                if plugin and hasattr(plugin, "on_error"):
                    try:
                        plugin.on_error(e)
                    except Exception as notify_error:
                        logger.error(f"Error in plugin error handler: {notify_error}")

                if self._error_strategy == "fail_fast":
                    raise HookError(error_msg) from e
                elif self._error_strategy == "collect_all":
                    errors.append(
                        {
                            "plugin": plugin_name,
                            "error": e,
                            "event": event_name,
                        }
                    )
                # log_and_continue: just continue to next hook

        if errors and self._error_strategy == "collect_all":
            logger.warning(f"Event '{event_name}' completed with {len(errors)} errors")

        return result

    async def trigger_async(self, event_name: str, data: Any = None) -> Any:
        """
        Trigger an event and execute all registered hooks asynchronously.

        Both sync and async hooks are supported. Sync hooks are wrapped
        to run in the async context.

        Args:
            event_name: Name of the event to trigger
            data: Data to pass to hooks

        Returns:
            Modified data after passing through all hooks

        Raises:
            HookError: If error_strategy is 'fail_fast' and a hook fails
            StopPropagation: If a hook raises this to stop the chain
        """
        hooks = self._get_matching_hooks(event_name)

        if not hooks:
            logger.debug(f"No hooks registered for event '{event_name}'")
            return data

        if self._hook_tracing:
            logger.debug(f"Triggering async event '{event_name}' with " f"{len(hooks)} hooks")

        errors = []
        result = data

        for hook_info in hooks:
            callback = hook_info["callback"]
            plugin = hook_info["plugin"]
            plugin_name = hook_info["plugin_name"]
            priority = hook_info["priority"]
            timeout = hook_info["timeout"]
            is_async = hook_info["is_async"]

            # Skip disabled plugins
            if plugin and hasattr(plugin, "enabled") and not plugin.enabled:
                logger.debug(f"Skipping hook from disabled plugin '{plugin_name}'")
                continue

            try:
                import time

                start_time = time.time() if self._hook_tracing else None

                # Execute hook (async or sync)
                if is_async:
                    new_result = await self._execute_async_hook_with_timeout(
                        callback, result, timeout
                    )
                else:
                    # Run sync hook in executor to avoid blocking
                    loop = asyncio.get_event_loop()
                    new_result = await loop.run_in_executor(
                        None,
                        self._execute_hook_with_timeout,
                        callback,
                        result,
                        timeout,
                    )

                if self._hook_tracing:
                    elapsed = time.time() - start_time
                    logger.debug(
                        f"Async hook '{event_name}' from '{plugin_name}' "
                        f"(priority={priority}) executed in {elapsed:.4f}s"
                    )

                # Only update result if callback returned something
                if new_result is not None:
                    result = new_result

            except StopPropagation as e:
                logger.info(
                    f"Hook propagation stopped by '{plugin_name}' " f"for event '{event_name}': {e}"
                )
                break

            except HookTimeoutError as e:
                error_msg = (
                    f"Async hook '{event_name}' from plugin '{plugin_name}' " f"timed out: {e}"
                )
                logger.error(error_msg)

                if plugin and hasattr(plugin, "on_error"):
                    try:
                        plugin.on_error(e)
                    except Exception as notify_error:
                        logger.error(f"Error in plugin error handler: {notify_error}")

                if self._error_strategy == "fail_fast":
                    raise HookError(error_msg) from e
                elif self._error_strategy == "collect_all":
                    errors.append(
                        {
                            "plugin": plugin_name,
                            "error": e,
                            "event": event_name,
                        }
                    )

            except Exception as e:
                error_msg = (
                    f"Error executing async hook '{event_name}' from plugin "
                    f"'{plugin_name}': {e}"
                )
                logger.error(error_msg)

                if plugin and hasattr(plugin, "on_error"):
                    try:
                        plugin.on_error(e)
                    except Exception as notify_error:
                        logger.error(f"Error in plugin error handler: {notify_error}")

                if self._error_strategy == "fail_fast":
                    raise HookError(error_msg) from e
                elif self._error_strategy == "collect_all":
                    errors.append(
                        {
                            "plugin": plugin_name,
                            "error": e,
                            "event": event_name,
                        }
                    )

        if errors and self._error_strategy == "collect_all":
            logger.warning(f"Async event '{event_name}' completed with " f"{len(errors)} errors")

        return result

    def get_hooks(self, event_name: str) -> List[Dict[str, Any]]:
        """
        Get all hooks registered for an event (including wildcards).

        Args:
            event_name: Name of the event

        Returns:
            List of hook information dictionaries
        """
        return self._get_matching_hooks(event_name)

    def get_all_events(self) -> List[str]:
        """
        Get all registered event names.

        Returns:
            List of event names
        """
        return list(self._hooks.keys())

    def clear_event(self, event_name: str) -> None:
        """
        Clear all hooks for a specific event.

        Args:
            event_name: Name of the event to clear
        """
        if event_name in self._hooks:
            del self._hooks[event_name]
            logger.debug(f"Cleared all hooks for event '{event_name}'")

    def clear_all(self) -> None:
        """Clear all registered hooks."""
        self._hooks.clear()
        logger.debug("Cleared all hooks")

    def set_error_strategy(self, strategy: str) -> None:
        """
        Set the error handling strategy.

        Args:
            strategy: One of 'log_and_continue', 'fail_fast', 'collect_all'
        """
        valid_strategies = ["log_and_continue", "fail_fast", "collect_all"]
        if strategy not in valid_strategies:
            raise ValueError(f"Invalid strategy. Must be one of {valid_strategies}")
        self._error_strategy = strategy
        logger.debug(f"Error strategy set to '{strategy}'")

    def enable_hook_tracing(self, enabled: bool = True) -> None:
        """
        Enable or disable hook tracing for debugging.

        When enabled, logs detailed information about hook execution times.

        Args:
            enabled: Whether to enable tracing
        """
        self._hook_tracing = enabled
        logger.debug(f"Hook tracing {'enabled' if enabled else 'disabled'}")
