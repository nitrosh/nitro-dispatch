"""Hook registry: event subscriptions and sync/async dispatch."""

import asyncio
import concurrent.futures
import re
from typing import Any, Callable, Dict, List, Optional
import logging

from .exceptions import HookError, StopPropagation, HookTimeoutError

logger = logging.getLogger(__name__)


class HookRegistry:
    """Event bus storing hooks and dispatching them to listeners.

    Hooks are kept per event name and sorted by priority (higher first,
    registration order for ties). On :meth:`trigger` / :meth:`trigger_async`
    the registry gathers every hook whose registered name matches the fired
    event — either literally or via a wildcard pattern like ``"user.*"`` —
    and invokes them in priority order, threading the return value of each
    hook into the next as its input.

    The manager owns an instance of this class; most application code does
    not interact with it directly. Use it standalone when you want the hook
    mechanism without plugins.

    Features:
        - Priority-based execution with deterministic ordering.
        - Per-hook timeout (thread-based for sync, ``asyncio.wait_for``
          for async).
        - Wildcard event matching (``"user.*"``, ``"db.before_*"``).
        - :class:`StopPropagation` to halt the chain from a hook.
        - Plugin-level enable/disable: hooks from disabled plugins are
          skipped without unregistering.
    """

    def __init__(self) -> None:
        """Initialize an empty registry with the default error strategy."""
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
        """Register a callback to run when an event fires.

        Whether ``callback`` is treated as async is auto-detected via
        :func:`asyncio.iscoroutinefunction`. Async callbacks are skipped
        in :meth:`trigger` with a warning; use :meth:`trigger_async`.

        Args:
            event_name: Event name to subscribe to. May be a literal like
                ``"before_save"`` or a wildcard pattern like ``"user.*"``
                — wildcard patterns match multiple literal events at
                dispatch time.
            callback: Function invoked when the event fires. Receives
                the event's data and may return modified data.
            plugin: Owning plugin instance, used for attribution and to
                honor ``enabled``/``disabled`` state. ``None`` for
                anonymous hooks.
            priority: Higher values run earlier. Default 50.
            timeout: Per-hook execution limit in seconds. Exceeding
                raises :class:`HookTimeoutError` inside dispatch.

        Example:
            >>> reg = HookRegistry()
            >>> reg.register("user.*", lambda d: d, priority=100)
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
        """Remove a previously registered callback from an event.

        Match is on the exact ``(callback, plugin)`` pair. If the same
        callback was registered for multiple events, each must be
        unregistered separately.

        Args:
            event_name: Event name the callback was registered under.
            callback: The exact callable passed to :meth:`register`.
            plugin: The same owning plugin used at registration.

        Returns:
            True if a hook was found and removed; False otherwise.
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

        # Thread-based timeout: portable (works on Windows and in non-main
        # threads, unlike signal.SIGALRM) and safe to call from executors.
        # Note: the worker thread cannot be forcibly killed on timeout — this
        # matches asyncio.wait_for's behavior for async hooks.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(callback, data)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise HookTimeoutError(
                    f"Hook execution exceeded timeout of {timeout}s"
                )

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
        """Fire an event and run matching hooks synchronously.

        Hooks run in priority order (highest first). Each hook's
        non-``None`` return value becomes the ``data`` input of the next
        hook. A hook raising :class:`StopPropagation` halts the chain
        and the current ``data`` is returned immediately. Async hooks
        are skipped with a warning — use :meth:`trigger_async` for
        those.

        Args:
            event_name: Event name to fire. Literal plus wildcard
                matches are dispatched.
            data: Payload threaded through the chain.

        Returns:
            The payload after the last hook returned.

        Raises:
            HookError: If the error strategy is ``"fail_fast"`` and a
                hook raises.

        Example:
            >>> reg = HookRegistry()
            >>> reg.register("sum", lambda d: d + 1)
            >>> reg.trigger("sum", 41)
            42
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
        """Fire an event asynchronously, running matching hooks.

        Async hooks run natively via ``asyncio.wait_for``. Sync hooks
        are dispatched to the default executor so they do not block
        the event loop — which means sync hooks must be thread-safe
        when invoked through this method. Ordering, stop-propagation,
        and error-strategy semantics are identical to :meth:`trigger`.

        Args:
            event_name: Event name to fire.
            data: Payload threaded through the chain.

        Returns:
            The payload after the last hook returned.

        Raises:
            HookError: If the error strategy is ``"fail_fast"`` and a
                hook raises.

        Example:
            >>> import asyncio
            >>> reg = HookRegistry()
            >>> async def bump(d): return d + 1
            >>> reg.register("sum", bump)
            >>> asyncio.run(reg.trigger_async("sum", 41))
            42
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
        """Return every hook that would run for an event, in priority order.

        Includes hooks registered against wildcard patterns that match
        ``event_name``, not just literal matches.

        Args:
            event_name: Event name to resolve.

        Returns:
            List of hook info dicts with keys ``callback``, ``plugin``,
            ``plugin_name``, ``priority``, ``timeout``, ``is_async``.
        """
        return self._get_matching_hooks(event_name)

    def get_all_events(self) -> List[str]:
        """Return every registered event name.

        Returns:
            The literal strings used at registration — wildcard patterns
            are returned as-is (e.g. ``"user.*"``).
        """
        return list(self._hooks.keys())

    def clear_event(self, event_name: str) -> None:
        """Remove every hook registered under a single event name.

        Only removes hooks registered with the literal ``event_name``;
        wildcard patterns that happen to match are left intact.

        Args:
            event_name: Event name to clear.
        """
        if event_name in self._hooks:
            del self._hooks[event_name]
            logger.debug(f"Cleared all hooks for event '{event_name}'")

    def clear_all(self) -> None:
        """Remove every registered hook.

        Use between tests or when reconfiguring the registry from
        scratch.
        """
        self._hooks.clear()
        logger.debug("Cleared all hooks")

    def set_error_strategy(self, strategy: str) -> None:
        """Choose how hook exceptions are handled during dispatch.

        Strategies:
            - ``"log_and_continue"`` (default): log the error and run
              the next hook.
            - ``"fail_fast"``: raise :class:`HookError` and abort the
              chain.
            - ``"collect_all"``: run every hook, then log a summary of
              how many failed.

        Args:
            strategy: One of the values above.

        Raises:
            ValueError: If ``strategy`` is not one of the listed names.
        """
        valid_strategies = ["log_and_continue", "fail_fast", "collect_all"]
        if strategy not in valid_strategies:
            raise ValueError(f"Invalid strategy. Must be one of {valid_strategies}")
        self._error_strategy = strategy
        logger.debug(f"Error strategy set to '{strategy}'")

    def enable_hook_tracing(self, enabled: bool = True) -> None:
        """Toggle per-hook timing logs for debugging.

        When on, each dispatch logs the elapsed time of every hook at
        DEBUG level. Configure the root logger at DEBUG to see output.

        Args:
            enabled: True to turn tracing on, False to turn it off.
        """
        self._hook_tracing = enabled
        logger.debug(f"Hook tracing {'enabled' if enabled else 'disabled'}")
