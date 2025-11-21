"""
Tests for HookRegistry class.
"""

import pytest
import asyncio
import time
from nitro_dispatch.core.hook_registry import HookRegistry
from nitro_dispatch.core.exceptions import HookError, StopPropagation
from nitro_dispatch import PluginBase


@pytest.fixture
def registry():
    """Create a fresh HookRegistry for each test."""
    return HookRegistry()


@pytest.fixture
def mock_plugin():
    """Create a mock plugin for testing."""

    class MockPlugin(PluginBase):
        name = "mock"
        version = "1.0.0"

        def __init__(self):
            super().__init__()
            self.error_count = 0

        def on_error(self, error):
            self.error_count += 1

    return MockPlugin()


def test_register_hook(registry):
    """Test registering a hook."""

    def callback(data):
        return data

    registry.register("test_event", callback)
    hooks = registry.get_hooks("test_event")
    assert len(hooks) == 1
    assert hooks[0]["callback"] == callback


def test_register_hook_with_priority(registry):
    """Test registering hooks with different priorities."""

    def callback1(data):
        return data

    def callback2(data):
        return data

    registry.register("test_event", callback1, priority=50)
    registry.register("test_event", callback2, priority=100)

    hooks = registry.get_hooks("test_event")
    assert len(hooks) == 2
    # Higher priority should come first
    assert hooks[0]["priority"] == 100
    assert hooks[1]["priority"] == 50


def test_unregister_hook(registry):
    """Test unregistering a hook."""

    def callback(data):
        return data

    registry.register("test_event", callback)
    assert len(registry.get_hooks("test_event")) == 1

    result = registry.unregister("test_event", callback)
    assert result is True
    assert len(registry.get_hooks("test_event")) == 0


def test_unregister_nonexistent_hook(registry):
    """Test unregistering a hook that doesn't exist."""

    def callback(data):
        return data

    result = registry.unregister("nonexistent", callback)
    assert result is False


def test_trigger_sync_hooks(registry):
    """Test triggering synchronous hooks."""
    called = []

    def callback1(data):
        called.append(1)
        data["step1"] = True
        return data

    def callback2(data):
        called.append(2)
        data["step2"] = True
        return data

    registry.register("test_event", callback1, priority=100)
    registry.register("test_event", callback2, priority=50)

    result = registry.trigger("test_event", {})

    assert called == [1, 2]  # Priority order
    assert result["step1"] is True
    assert result["step2"] is True


@pytest.mark.asyncio
async def test_trigger_async_hooks(registry):
    """Test triggering asynchronous hooks."""
    called = []

    async def async_callback(data):
        called.append("async")
        await asyncio.sleep(0.01)
        data["async"] = True
        return data

    registry.register("test_event", async_callback)

    result = await registry.trigger_async("test_event", {})

    assert "async" in called
    assert result["async"] is True


@pytest.mark.asyncio
async def test_trigger_async_mixed_hooks(registry):
    """Test triggering mixed sync and async hooks."""
    called = []

    def sync_callback(data):
        called.append("sync")
        data["sync"] = True
        return data

    async def async_callback(data):
        called.append("async")
        await asyncio.sleep(0.01)
        data["async"] = True
        return data

    registry.register("test_event", sync_callback, priority=100)
    registry.register("test_event", async_callback, priority=50)

    result = await registry.trigger_async("test_event", {})

    assert called == ["sync", "async"]
    assert result["sync"] is True
    assert result["async"] is True


def test_wildcard_matching(registry):
    """Test wildcard event matching."""

    def callback(data):
        data["matched"] = True
        return data

    registry.register("user.*", callback)

    # Should match
    result1 = registry.trigger("user.login", {})
    assert result1["matched"] is True

    result2 = registry.trigger("user.logout", {})
    assert result2["matched"] is True

    # Should not match
    result3 = registry.trigger("admin.login", {})
    assert "matched" not in result3


def test_wildcard_patterns(registry):
    """Test various wildcard patterns."""

    def callback(data):
        data["matched"] = True
        return data

    registry.register("db.before_*", callback)

    result1 = registry.trigger("db.before_save", {})
    assert result1["matched"] is True

    result2 = registry.trigger("db.before_delete", {})
    assert result2["matched"] is True

    result3 = registry.trigger("db.after_save", {})
    assert "matched" not in result3


def test_stop_propagation(registry):
    """Test stop propagation."""
    called = []

    def callback1(data):
        called.append(1)
        raise StopPropagation("Stop here")

    def callback2(data):
        called.append(2)
        return data

    registry.register("test_event", callback1, priority=100)
    registry.register("test_event", callback2, priority=50)

    registry.trigger("test_event", {})

    # Only first callback should be called
    assert called == [1]


def test_hook_timeout(registry):
    """Test hook timeout protection."""

    def slow_callback(data):
        time.sleep(3)  # Exceeds timeout
        return data

    registry.register("test_event", slow_callback, timeout=0.1)
    registry.set_error_strategy("fail_fast")

    with pytest.raises(HookError):
        registry.trigger("test_event", {})


def test_error_strategy_log_and_continue(registry, mock_plugin):
    """Test log_and_continue error strategy."""

    def failing_callback(data):
        raise ValueError("Test error")

    def working_callback(data):
        data["worked"] = True
        return data

    mock_plugin.enabled = True  # Ensure plugin is enabled
    registry.set_error_strategy("log_and_continue")
    registry.register("test_event", failing_callback, mock_plugin, priority=100)
    registry.register("test_event", working_callback, priority=50)

    result = registry.trigger("test_event", {})

    # Should continue despite error
    assert result["worked"] is True
    assert mock_plugin.error_count == 1


def test_error_strategy_fail_fast(registry):
    """Test fail_fast error strategy."""

    def failing_callback(data):
        raise ValueError("Test error")

    registry.set_error_strategy("fail_fast")
    registry.register("test_event", failing_callback)

    with pytest.raises(HookError):
        registry.trigger("test_event", {})


def test_disabled_plugin_hooks_skipped(registry, mock_plugin):
    """Test that hooks from disabled plugins are skipped."""
    called = []

    def callback(data):
        called.append(1)
        return data

    mock_plugin.enabled = True
    registry.register("test_event", callback, mock_plugin)

    # Enabled - should execute
    registry.trigger("test_event", {})
    assert len(called) == 1

    # Disable plugin
    mock_plugin.enabled = False
    registry.trigger("test_event", {})
    # Should still be 1 (not executed again)
    assert len(called) == 1


def test_hook_tracing(registry):
    """Test hook tracing for debugging."""

    def callback(data):
        return data

    registry.enable_hook_tracing(True)
    registry.register("test_event", callback)

    # Should not raise an error
    result = registry.trigger("test_event", {})
    assert result == {}


def test_get_all_events(registry):
    """Test getting all registered events."""

    def callback(data):
        return data

    registry.register("event1", callback)
    registry.register("event2", callback)
    registry.register("event3", callback)

    events = registry.get_all_events()
    assert "event1" in events
    assert "event2" in events
    assert "event3" in events


def test_clear_event(registry):
    """Test clearing hooks for a specific event."""

    def callback(data):
        return data

    registry.register("event1", callback)
    registry.register("event2", callback)

    registry.clear_event("event1")

    assert len(registry.get_hooks("event1")) == 0
    assert len(registry.get_hooks("event2")) == 1


def test_clear_all(registry):
    """Test clearing all hooks."""

    def callback(data):
        return data

    registry.register("event1", callback)
    registry.register("event2", callback)

    registry.clear_all()

    assert len(registry.get_all_events()) == 0


def test_hook_with_none_return(registry):
    """Test that hooks can return None without breaking the chain."""

    def callback1(data):
        data["step1"] = True
        return None  # Return None

    def callback2(data):
        data["step2"] = True
        return data

    registry.register("test_event", callback1, priority=100)
    registry.register("test_event", callback2, priority=50)

    result = registry.trigger("test_event", {})

    # Should preserve step1 even though callback1 returned None
    assert result["step1"] is True
    assert result["step2"] is True


def test_set_invalid_error_strategy(registry):
    """Test setting an invalid error strategy."""
    with pytest.raises(ValueError):
        registry.set_error_strategy("invalid_strategy")


@pytest.mark.asyncio
async def test_async_hook_timeout(registry):
    """Test async hook timeout."""

    async def slow_async_callback(data):
        await asyncio.sleep(5)
        return data

    registry.register("test_event", slow_async_callback, timeout=0.1)
    registry.set_error_strategy("fail_fast")

    with pytest.raises(HookError):
        await registry.trigger_async("test_event", {})


def test_trigger_no_hooks(registry):
    """Test triggering an event with no hooks."""
    result = registry.trigger("nonexistent_event", {"data": "test"})
    assert result == {"data": "test"}


@pytest.mark.asyncio
async def test_trigger_async_no_hooks(registry):
    """Test triggering async event with no hooks."""
    result = await registry.trigger_async("nonexistent_event", {"data": "test"})
    assert result == {"data": "test"}


def test_error_strategy_collect_all(registry, mock_plugin):
    """Test collect_all error strategy."""

    def failing_callback1(data):
        raise ValueError("Error 1")

    def failing_callback2(data):
        raise TypeError("Error 2")

    def working_callback(data):
        data["worked"] = True
        return data

    mock_plugin.enabled = True
    registry.set_error_strategy("collect_all")
    registry.register("test_event", failing_callback1, mock_plugin, priority=100)
    registry.register("test_event", failing_callback2, mock_plugin, priority=90)
    registry.register("test_event", working_callback, priority=50)

    # Should collect all errors and continue (doesn't raise, just logs)
    result = registry.trigger("test_event", {})

    # Working callback should still execute
    assert result["worked"] is True


def test_async_hook_in_sync_trigger(registry):
    """Test that async hooks are skipped in sync trigger with warning."""

    async def async_callback(data):
        data["async"] = True
        return data

    def sync_callback(data):
        data["sync"] = True
        return data

    registry.register("test_event", async_callback, priority=100)
    registry.register("test_event", sync_callback, priority=50)

    result = registry.trigger("test_event", {})

    # Async hook should be skipped, only sync executed
    assert "async" not in result
    assert result["sync"] is True


def test_plugin_on_error_raises_exception(registry):
    """Test when plugin's on_error handler itself raises an exception."""

    class FailingErrorHandlerPlugin(PluginBase):
        name = "failing_error_handler"

        def on_error(self, error):
            raise RuntimeError("Error handler failed")

    plugin = FailingErrorHandlerPlugin()
    plugin.enabled = True

    def failing_callback(data):
        raise ValueError("Original error")

    registry.set_error_strategy("log_and_continue")
    registry.register("test_event", failing_callback, plugin)

    # Should handle the error in on_error gracefully
    result = registry.trigger("test_event", {})
    assert result == {}


@pytest.mark.asyncio
async def test_async_error_strategy_collect_all(registry, mock_plugin):
    """Test collect_all error strategy with async hooks."""

    async def failing_async_callback(data):
        raise ValueError("Async error")

    async def working_async_callback(data):
        data["worked"] = True
        return data

    mock_plugin.enabled = True
    registry.set_error_strategy("collect_all")
    registry.register("test_event", failing_async_callback, mock_plugin, priority=100)
    registry.register("test_event", working_async_callback, priority=50)

    # Should collect errors and continue (doesn't raise, just logs)
    result = await registry.trigger_async("test_event", {})

    # Working callback should still execute
    assert result["worked"] is True


def test_hook_with_timeout_success(registry):
    """Test hook with timeout that completes successfully."""

    def quick_callback(data):
        data["quick"] = True
        return data

    registry.register("test_event", quick_callback, timeout=1.0)

    result = registry.trigger("test_event", {})
    assert result["quick"] is True


@pytest.mark.asyncio
async def test_async_hook_tracing(registry):
    """Test async hook tracing enabled."""

    async def async_callback(data):
        data["traced"] = True
        return data

    registry.enable_hook_tracing(True)
    registry.register("test_event", async_callback)

    result = await registry.trigger_async("test_event", {})
    assert result["traced"] is True


@pytest.mark.asyncio
async def test_async_disabled_plugin_skipped(registry, mock_plugin):
    """Test that disabled plugins are skipped in async trigger."""

    async def async_callback(data):
        data["should_not_run"] = True
        return data

    mock_plugin.enabled = False  # Disabled
    registry.register("test_event", async_callback, mock_plugin)

    result = await registry.trigger_async("test_event", {})
    assert "should_not_run" not in result


@pytest.mark.asyncio
async def test_async_stop_propagation(registry):
    """Test StopPropagation in async hooks."""

    async def first_callback(data):
        data["first"] = True
        raise StopPropagation("Stop here")

    async def second_callback(data):
        data["second"] = True
        return data

    registry.register("test_event", first_callback, priority=100)
    registry.register("test_event", second_callback, priority=50)

    result = await registry.trigger_async("test_event", {})
    assert result["first"] is True
    assert "second" not in result


@pytest.mark.asyncio
async def test_async_plugin_on_error_raises(registry):
    """Test when plugin's on_error raises in async hook."""

    class FailingErrorHandlerPlugin(PluginBase):
        name = "failing_async_error"

        def on_error(self, error):
            raise RuntimeError("Error handler failed")

    plugin = FailingErrorHandlerPlugin()
    plugin.enabled = True

    async def failing_callback(data):
        raise ValueError("Original async error")

    registry.set_error_strategy("log_and_continue")
    registry.register("test_event", failing_callback, plugin)

    # Should handle the error in on_error gracefully
    result = await registry.trigger_async("test_event", {})
    assert result == {}


@pytest.mark.asyncio
async def test_async_timeout_collect_all(registry, mock_plugin):
    """Test async timeout with collect_all strategy."""

    async def slow_callback(data):
        await asyncio.sleep(5)
        return data

    mock_plugin.enabled = True
    registry.set_error_strategy("collect_all")
    registry.register("test_event", slow_callback, mock_plugin, timeout=0.1)

    # Should log but not raise
    result = await registry.trigger_async("test_event", {})
    assert result == {}


def test_hook_exception_with_timeout(registry):
    """Test hook that raises exception (not timeout) with timeout set."""

    def failing_callback(data):
        raise ValueError("Regular error")

    registry.register("test_event", failing_callback, timeout=1.0)
    registry.set_error_strategy("log_and_continue")

    # Should handle the exception normally
    result = registry.trigger("test_event", {})
    assert result == {}


def test_timeout_with_on_error_failing(registry):
    """Test timeout error when plugin's on_error raises exception."""

    class FailingOnErrorPlugin(PluginBase):
        name = "failing_on_error"

        def on_error(self, error):
            raise RuntimeError("on_error failed")

    plugin = FailingOnErrorPlugin()
    plugin.enabled = True

    def slow_callback(data):
        time.sleep(3)
        return data

    registry.set_error_strategy("log_and_continue")
    registry.register("test_event", slow_callback, plugin, timeout=0.1)

    # Should handle both timeout and on_error exception
    result = registry.trigger("test_event", {})
    assert result == {}


def test_timeout_collect_all_strategy(registry, mock_plugin):
    """Test timeout with collect_all error strategy."""

    def slow_callback(data):
        time.sleep(3)
        return data

    mock_plugin.enabled = True
    registry.set_error_strategy("collect_all")
    registry.register("test_event", slow_callback, mock_plugin, timeout=0.1)

    # Should collect timeout error and continue
    result = registry.trigger("test_event", {})
    assert result == {}


@pytest.mark.asyncio
async def test_async_timeout_on_error_failing(registry):
    """Test async timeout when plugin's on_error raises."""

    class FailingOnErrorPlugin(PluginBase):
        name = "failing_async_on_error"

        def on_error(self, error):
            raise RuntimeError("async on_error failed")

    plugin = FailingOnErrorPlugin()
    plugin.enabled = True

    async def slow_callback(data):
        await asyncio.sleep(5)
        return data

    registry.set_error_strategy("log_and_continue")
    registry.register("test_event", slow_callback, plugin, timeout=0.1)

    # Should handle both timeout and on_error exception
    result = await registry.trigger_async("test_event", {})
    assert result == {}


@pytest.mark.asyncio
async def test_async_error_collect_all_strategy(registry, mock_plugin):
    """Test async error with collect_all strategy."""

    async def failing_callback(data):
        raise ValueError("Async error")

    mock_plugin.enabled = True
    registry.set_error_strategy("collect_all")
    registry.register("test_event", failing_callback, mock_plugin)

    # Should collect error and continue
    result = await registry.trigger_async("test_event", {})
    assert result == {}


@pytest.mark.asyncio
async def test_async_error_fail_fast_strategy(registry, mock_plugin):
    """Test async error with fail_fast strategy."""

    async def failing_callback(data):
        raise ValueError("Async error")

    mock_plugin.enabled = True
    registry.set_error_strategy("fail_fast")
    registry.register("test_event", failing_callback, mock_plugin)

    # Should raise HookError
    with pytest.raises(HookError):
        await registry.trigger_async("test_event", {})
