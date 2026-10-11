"""Hook registry: event subscriptions and sync/async dispatch."""

import asyncio
import contextvars
import inspect
import itertools
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging

from .exceptions import HookError, StopPropagation, HookTimeoutError

logger = logging.getLogger(__name__)


class HookRegistry:
    """Event bus storing hooks and dispatching them to listeners.

    Hooks are kept per event name and sorted by priority (higher first,
    registration order for ties, including across literal and wildcard
    registrations). On :meth:`trigger` / :meth:`trigger_async`
    the registry gathers every hook whose registered name matches the fired
    event, either literally or via a wildcard pattern like ``"user.*"``,
    and invokes them in priority order, threading the return value of each
    hook into the next as its input.

    The manager owns an instance of this class; most application code does
    not interact with it directly. Use it standalone when you want the hook
    mechanism without plugins.

    Features:
        - Priority-based execution with deterministic ordering.
        - Per-hook timeout (a daemon worker thread for sync hooks,
          ``asyncio.wait_for`` for async).
        - Wildcard event matching (``"user.*"``, ``"db.before_*"``).
        - :class:`StopPropagation` to halt the chain from a hook.
        - Plugin-level enable/disable: hooks from disabled plugins are
          skipped without unregistering.
        - Thread-safe registration and dispatch: mutations and
          ``_get_matching_hooks`` are guarded by an :class:`RLock` and
          iteration snapshots the hook map.
    """

    def __init__(self) -> None:
        """Initialize an empty registry with the default error strategy."""
        self._hooks: Dict[str, List[Dict[str, Any]]] = {}
        self._error_strategy: str = "log_and_continue"
        self._hook_tracing: bool = False
        # Reentrant: hooks running in worker threads may call back into
        # register/unregister, and trigger_async dispatches sync hooks
        # to executor threads concurrently.
        self._lock = threading.RLock()
        # Populated by trigger/trigger_async when the strategy is
        # ``collect_all`` so callers can inspect what failed. Cleared at
        # the start of each dispatch.
        self._last_errors: List[Dict[str, Any]] = []
        self._sequence = itertools.count()

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
        :func:`inspect.iscoroutinefunction` (an object with an ``async def
        __call__`` counts as async too). Async callbacks are skipped
        in :meth:`trigger` with a warning; use :meth:`trigger_async`.

        Args:
            event_name: Event name to subscribe to. May be a literal like
                ``"before_save"`` or a wildcard pattern like ``"user.*"``;
                wildcard patterns match multiple literal events at
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
        hook_info = {
            "callback": callback,
            "plugin": plugin,
            "plugin_name": plugin.name if plugin else "anonymous",
            "priority": priority,
            "timeout": timeout,
            "is_async": _is_async_callable(callback),
        }

        with self._lock:
            hook_info["sequence"] = next(self._sequence)
            if event_name not in self._hooks:
                self._hooks[event_name] = []
            self._hooks[event_name].append(hook_info)
            self._hooks[event_name].sort(key=_execution_order)

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
        with self._lock:
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

    def unregister_plugin(self, plugin: Any) -> int:
        """Remove every hook owned by ``plugin``, across all events.

        Args:
            plugin: The owning plugin instance. Matched by identity.

        Returns:
            How many hooks were removed.
        """
        removed = 0
        with self._lock:
            for event_name in list(self._hooks):
                kept = [hook for hook in self._hooks[event_name] if hook["plugin"] is not plugin]
                removed += len(self._hooks[event_name]) - len(kept)
                self._hooks[event_name] = kept

        if removed:
            plugin_name = getattr(plugin, "name", "?")
            logger.debug(f"Unregistered {removed} hooks from plugin '{plugin_name}'")
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
        # `*` matches a single non-empty dot-delimited segment, mirroring
        # glob semantics rather than regex `.*` (which would cross segment
        # boundaries). ``+`` (one-or-more) instead of ``*`` (zero-or-more)
        # is intentional: ``user.*`` does NOT match the literal string
        # ``"user."`` with an empty trailing segment.
        regex_pattern = pattern.replace(".", r"\.").replace("*", "[^.]+")
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
        matching_hooks: List[Dict[str, Any]] = []

        # Snapshot under the lock so a concurrent register/unregister
        # from a hook running in a worker thread cannot mutate the dict
        # mid-iteration.
        with self._lock:
            snapshot = [(event, list(hooks)) for event, hooks in self._hooks.items()]

        for registered_event, hooks in snapshot:
            # Exact match
            if registered_event == event_name:
                matching_hooks.extend(hooks)
            # Wildcard match
            elif "*" in registered_event:
                if self._match_event_pattern(registered_event, event_name):
                    matching_hooks.extend(hooks)

        matching_hooks.sort(key=_execution_order)

        return matching_hooks

    def _execute_hook_with_timeout(
        self, callback: Callable, data: Any, timeout: Optional[float]
    ) -> Any:
        """
        Execute a synchronous hook with optional timeout.

        Each timed call gets its own daemon thread. A shared pool would
        fill up with runaway hooks (Python can't kill a thread), after
        which every later timed hook would sit in the queue and "time
        out" without ever running. Pool threads are also joined at
        interpreter exit, so one hung hook would stop the process from
        exiting. A daemon thread has neither problem: on timeout it's
        abandoned and left to finish, or die with the process.

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

        done = threading.Event()
        outcome: List[Tuple[Any, Optional[BaseException]]] = []

        def finish(value: Any, error: Optional[BaseException]) -> None:
            outcome.append((value, error))
            done.set()

        _run_in_daemon_thread(callback, data, finish)
        if not done.wait(timeout):
            raise HookTimeoutError(f"Hook execution exceeded timeout of {timeout}s")

        value, error = outcome[0]
        if error is not None:
            raise error
        return value

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

    async def _execute_sync_hook_in_executor(
        self,
        callback: Callable,
        data: Any,
        timeout: Optional[float],
    ) -> Any:
        """Run a sync hook off the event loop, with optional timeout.

        Untimed hooks go to the loop's default executor. Timed hooks get
        a daemon thread instead: ``asyncio.run`` joins the default
        executor on shutdown, so a runaway hook there would hang the
        caller's exit even after its timeout fired. Both paths copy the
        caller's context so context variables are visible to the hook.
        """
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()

        if timeout is None:
            return await loop.run_in_executor(None, context.run, callback, data)

        future = loop.create_future()

        def settle(value: Any, error: Optional[BaseException]) -> None:
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(value)

        def finish(value: Any, error: Optional[BaseException]) -> None:
            try:
                loop.call_soon_threadsafe(settle, value, error)
            except RuntimeError:
                pass

        _run_in_daemon_thread(callback, data, finish, context)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise HookTimeoutError(f"Hook execution exceeded timeout of {timeout}s")

    async def _notify_on_error(self, plugin: Any, error: Exception) -> None:
        """Call ``plugin.on_error`` and ``await`` the result if it's a coroutine."""
        if not (plugin and hasattr(plugin, "on_error")):
            return
        try:
            maybe_coro = plugin.on_error(error)
            if inspect.iscoroutine(maybe_coro):
                await maybe_coro
        except Exception as notify_error:
            logger.error(f"Error in plugin error handler: {notify_error}")

    def _notify_on_error_sync(self, plugin: Any, error: Exception) -> None:
        """Sync variant: drops async ``on_error`` coroutines with a warning."""
        if not (plugin and hasattr(plugin, "on_error")):
            return
        try:
            maybe_coro = plugin.on_error(error)
            if inspect.iscoroutine(maybe_coro):
                # The sync trigger() path cannot await this; close it to
                # silence "coroutine was never awaited" RuntimeWarning and
                # tell the user.
                maybe_coro.close()
                logger.warning(
                    f"Async on_error coroutine from plugin "
                    f"'{getattr(plugin, 'name', '?')}' was dropped in sync "
                    f"trigger(); use trigger_async() to await it."
                )
        except Exception as notify_error:
            logger.error(f"Error in plugin error handler: {notify_error}")

    def trigger(self, event_name: str, data: Any = None) -> Any:
        """Fire an event and run matching hooks synchronously.

        Hooks run in priority order (highest first). Each hook's
        non-``None`` return value becomes the ``data`` input of the next
        hook. A hook raising :class:`StopPropagation` halts the chain
        and the current ``data`` is returned immediately. Async hooks
        are skipped with a warning; use :meth:`trigger_async` for
        those.

        Note: a hook that returns ``None`` does NOT clear the payload
        for the next hook — the previous ``data`` is preserved. If you
        need to set the chain value to ``None`` explicitly, raise
        :class:`StopPropagation` or use a sentinel value.

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
        self._last_errors = []
        hooks = self._get_matching_hooks(event_name)

        if not hooks:
            logger.debug("No hooks registered for event '%s'", event_name)
            return data

        if self._hook_tracing:
            logger.debug(f"Triggering event '{event_name}' with {len(hooks)} hooks")

        errors: List[Dict[str, Any]] = []
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
                logger.debug("Skipping hook from disabled plugin '%s'", plugin_name)
                continue

            # Can't execute async hooks in sync context
            if is_async:
                logger.warning(
                    f"Skipping async hook '{plugin_name}' in sync trigger. "
                    f"Use trigger_async() instead."
                )
                continue

            try:
                start_time = time.perf_counter()

                # Execute hook with timeout
                new_result = self._execute_hook_with_timeout(callback, result, timeout)

                if self._hook_tracing:
                    elapsed = time.perf_counter() - start_time
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

                self._notify_on_error_sync(plugin, e)

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

                self._notify_on_error_sync(plugin, e)

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

        self._last_errors = errors
        if errors and self._error_strategy == "collect_all":
            logger.warning(f"Event '{event_name}' completed with {len(errors)} errors")

        return result

    async def trigger_async(self, event_name: str, data: Any = None) -> Any:
        """Fire an event asynchronously, running matching hooks.

        Async hooks run natively via ``asyncio.wait_for``. Sync hooks
        are dispatched to the default executor so they do not block
        the event loop, which means sync hooks must be thread-safe
        when invoked through this method. Ordering, stop-propagation,
        and error-strategy semantics are identical to :meth:`trigger`.

        ``on_error`` callbacks are awaited if they return a coroutine,
        so plugins may define ``async def on_error``.

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
        self._last_errors = []
        hooks = self._get_matching_hooks(event_name)

        if not hooks:
            logger.debug("No hooks registered for event '%s'", event_name)
            return data

        if self._hook_tracing:
            logger.debug(f"Triggering async event '{event_name}' with " f"{len(hooks)} hooks")

        errors: List[Dict[str, Any]] = []
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
                logger.debug("Skipping hook from disabled plugin '%s'", plugin_name)
                continue

            try:
                start_time = time.perf_counter()

                # Execute hook (async or sync)
                if is_async:
                    new_result = await self._execute_async_hook_with_timeout(
                        callback, result, timeout
                    )
                else:
                    # Single-thread dispatch with asyncio-level timeout
                    # enforcement. The previous implementation called
                    # run_in_executor -> _execute_hook_with_timeout, which
                    # itself spun up another ThreadPoolExecutor — pinning
                    # two threads per timed hook and inheriting the
                    # shutdown-blocks-on-timeout bug.
                    new_result = await self._execute_sync_hook_in_executor(
                        callback, result, timeout
                    )

                if self._hook_tracing:
                    elapsed = time.perf_counter() - start_time
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

                await self._notify_on_error(plugin, e)

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

                await self._notify_on_error(plugin, e)

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

        self._last_errors = errors
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
            The literal strings used at registration. Wildcard patterns
            are returned as-is (e.g. ``"user.*"``).
        """
        with self._lock:
            return list(self._hooks.keys())

    def clear_event(self, event_name: str) -> None:
        """Remove every hook registered under a single event name.

        Only removes hooks registered with the literal ``event_name``;
        wildcard patterns that happen to match are left intact.

        Args:
            event_name: Event name to clear.
        """
        with self._lock:
            if event_name in self._hooks:
                del self._hooks[event_name]
                logger.debug(f"Cleared all hooks for event '{event_name}'")

    def clear_all(self) -> None:
        """Remove every registered hook.

        Use between tests or when reconfiguring the registry from
        scratch.
        """
        with self._lock:
            self._hooks.clear()
        logger.debug("Cleared all hooks")

    def get_last_errors(self) -> List[Dict[str, Any]]:
        """Return errors collected during the most recent dispatch.

        Populated when the error strategy is ``"collect_all"``. Each
        entry is a dict with keys ``plugin``, ``error``, ``event``.
        Cleared at the start of every :meth:`trigger` /
        :meth:`trigger_async`.

        Returns:
            A list of error records from the last dispatch, possibly
            empty.
        """
        return list(self._last_errors)

    def set_error_strategy(self, strategy: str) -> None:
        """Choose how hook exceptions are handled during dispatch.

        Strategies:
            - ``"log_and_continue"`` (default): log the error and run
              the next hook.
            - ``"fail_fast"``: raise :class:`HookError` and abort the
              chain.
            - ``"collect_all"``: run every hook, then expose collected
              errors via :meth:`get_last_errors`.

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


def _is_async_callable(callback: Callable) -> bool:
    """True for ``async def`` functions and objects with ``async def __call__``."""
    if inspect.iscoroutinefunction(callback):
        return True
    return inspect.iscoroutinefunction(getattr(type(callback), "__call__", None))


def _execution_order(hook_info: Dict[str, Any]) -> Tuple[int, int]:
    """Sort key: higher priority first, then registration order."""
    return (-hook_info["priority"], hook_info["sequence"])


def _run_in_daemon_thread(
    callback: Callable,
    data: Any,
    finish: Callable[[Any, Optional[BaseException]], None],
    context: Optional[contextvars.Context] = None,
) -> None:
    """Call ``callback(data)`` on a daemon thread and report via ``finish``."""
    context = context or contextvars.copy_context()

    def run() -> None:
        try:
            value = context.run(callback, data)
        except BaseException as error:
            finish(None, error)
        else:
            finish(value, None)

    threading.Thread(target=run, name="nitro-hook-timeout", daemon=True).start()
