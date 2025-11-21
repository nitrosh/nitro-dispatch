"""
Tests for Nitro Dispatch decorators.
"""

import asyncio
from nitro_dispatch.utils.decorators import hook
from nitro_dispatch import PluginBase


def test_hook_decorator_basic():
    """Test basic hook decorator."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event")
        def test_hook(self, data):
            return data

    plugin = TestPlugin()
    assert hasattr(plugin.test_hook, "_is_hook")
    assert plugin.test_hook._is_hook is True
    assert plugin.test_hook._event_name == "test_event"
    assert plugin.test_hook._priority == 50  # Default priority


def test_hook_decorator_with_priority():
    """Test hook decorator with priority."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event", priority=100)
        def test_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.test_hook._priority == 100


def test_hook_decorator_with_timeout():
    """Test hook decorator with timeout."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event", timeout=5.0)
        def test_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.test_hook._timeout == 5.0


def test_hook_decorator_async():
    """Test hook decorator with async function."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event")
        async def async_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.async_hook._is_async is True
    assert asyncio.iscoroutinefunction(plugin.async_hook)


def test_hook_decorator_sync():
    """Test hook decorator with sync function."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event")
        def sync_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.sync_hook._is_async is False


def test_hook_decorator_all_params():
    """Test hook decorator with all parameters."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event", priority=75, timeout=10.0, async_hook=False)
        def test_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.test_hook._event_name == "test_event"
    assert plugin.test_hook._priority == 75
    assert plugin.test_hook._timeout == 10.0
    assert plugin.test_hook._is_async is False


def test_hook_decorator_preserves_function_name():
    """Test that decorator preserves function metadata."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event")
        def my_hook_function(self, data):
            """Hook docstring."""
            return data

    plugin = TestPlugin()
    assert plugin.my_hook_function.__name__ == "my_hook_function"
    assert plugin.my_hook_function.__doc__ == "Hook docstring."


def test_hook_decorator_wildcard_event():
    """Test hook decorator with wildcard event name."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("user.*")
        def wildcard_hook(self, data):
            return data

    plugin = TestPlugin()
    assert plugin.wildcard_hook._event_name == "user.*"


def test_auto_collect_hooks():
    """Test that hooks are auto-collected during plugin initialization."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("event1", priority=100)
        def hook1(self, data):
            return data

        @hook("event2", priority=50)
        def hook2(self, data):
            return data

    plugin = TestPlugin()
    # Hooks should be stored in _hooks dict
    assert "event1" in plugin._hooks
    assert "event2" in plugin._hooks
