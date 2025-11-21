"""
Integration tests for Nitro Dispatch.

These tests verify that all components work together correctly.
"""

import pytest
import asyncio
from nitro_dispatch import PluginManager, PluginBase, hook, StopPropagation


def test_complete_plugin_lifecycle():
    """Test complete plugin lifecycle from registration to unload."""

    class TestPlugin(PluginBase):
        name = "test"
        version = "1.0.0"

        def __init__(self):
            super().__init__()
            self.lifecycle = []

        def on_load(self):
            self.lifecycle.append("loaded")

        def on_unload(self):
            self.lifecycle.append("unloaded")

        @hook("test_event")
        def process(self, data):
            data["processed"] = True
            return data

    manager = PluginManager()
    manager.register(TestPlugin)
    plugin = manager.load("test")

    # Test lifecycle
    assert "loaded" in plugin.lifecycle

    # Test hook execution
    result = manager.trigger("test_event", {})
    assert result["processed"] is True

    # Unload
    manager.unload("test")
    assert "unloaded" in plugin.lifecycle


def test_priority_based_data_filtering():
    """Test that hooks execute in priority order and filter data correctly."""

    class HighPriorityPlugin(PluginBase):
        name = "high"

        @hook("process", priority=100)
        def process(self, data):
            if "steps" not in data:
                data["steps"] = []
            data["steps"].append("high")
            return data

    class MediumPriorityPlugin(PluginBase):
        name = "medium"

        @hook("process", priority=50)
        def process(self, data):
            if "steps" not in data:
                data["steps"] = []
            data["steps"].append("medium")
            return data

    class LowPriorityPlugin(PluginBase):
        name = "low"

        @hook("process", priority=10)
        def process(self, data):
            if "steps" not in data:
                data["steps"] = []
            data["steps"].append("low")
            return data

    manager = PluginManager()
    manager.register(LowPriorityPlugin)
    manager.register(MediumPriorityPlugin)
    manager.register(HighPriorityPlugin)
    manager.load_all()

    result = manager.trigger("process", {})

    # Should execute in priority order: high, medium, low
    assert result["steps"] == ["high", "medium", "low"]


def test_dependency_chain():
    """Test plugin dependency chains work correctly."""

    class PluginA(PluginBase):
        name = "a"

        @hook("test")
        def process(self, data):
            data["a"] = True
            return data

    class PluginB(PluginBase):
        name = "b"
        dependencies = ["a"]

        @hook("test")
        def process(self, data):
            data["b"] = True
            return data

    class PluginC(PluginBase):
        name = "c"
        dependencies = ["b"]

        @hook("test")
        def process(self, data):
            data["c"] = True
            return data

    manager = PluginManager()
    manager.register(PluginC)
    manager.register(PluginB)
    manager.register(PluginA)

    # Loading C should auto-load B and A
    manager.load("c")

    assert manager.is_loaded("a")
    assert manager.is_loaded("b")
    assert manager.is_loaded("c")

    result = manager.trigger("test", {})
    assert result["a"] is True
    assert result["b"] is True
    assert result["c"] is True


@pytest.mark.asyncio
async def test_async_and_sync_hooks_together():
    """Test that async and sync hooks can work together."""

    class SyncPlugin(PluginBase):
        name = "sync"

        @hook("process", priority=100)
        def process(self, data):
            data["sync"] = True
            return data

    class AsyncPlugin(PluginBase):
        name = "async"

        @hook("process", priority=50)
        async def process(self, data):
            await asyncio.sleep(0.01)
            data["async"] = True
            return data

    manager = PluginManager()
    manager.register(SyncPlugin)
    manager.register(AsyncPlugin)
    manager.load_all()

    result = await manager.trigger_async("process", {})

    assert result["sync"] is True
    assert result["async"] is True


def test_wildcard_events_integration():
    """Test wildcard event matching in real scenarios."""
    captured_events = []

    class AuditPlugin(PluginBase):
        name = "audit"

        @hook("user.*", priority=100)
        def audit_user_events(self, data):
            captured_events.append(f"user.{data.get('action', 'unknown')}")
            return data

        @hook("db.before_*")
        def audit_db_events(self, data):
            operation = data.get("operation", "unknown")
            captured_events.append(f"db.before.{operation}")
            return data

    manager = PluginManager()
    manager.register(AuditPlugin)
    manager.load("audit")

    manager.trigger("user.login", {"action": "login"})
    manager.trigger("user.logout", {"action": "logout"})
    manager.trigger("db.before_save", {"operation": "save"})
    manager.trigger("db.before_delete", {"operation": "delete"})

    assert len(captured_events) == 4


def test_stop_propagation_integration():
    """Test stop propagation in a real scenario."""

    class ValidationPlugin(PluginBase):
        name = "validator"

        @hook("save", priority=100)
        def validate(self, data):
            if not data.get("valid"):
                raise StopPropagation("Validation failed")
            data["validated"] = True
            return data

    class ProcessPlugin(PluginBase):
        name = "processor"

        @hook("save", priority=50)
        def process(self, data):
            data["processed"] = True
            return data

    manager = PluginManager()
    manager.register(ValidationPlugin)
    manager.register(ProcessPlugin)
    manager.load_all()

    # Valid data - should process
    result1 = manager.trigger("save", {"valid": True})
    assert result1.get("validated") is True
    assert result1.get("processed") is True

    # Invalid data - should stop before processing
    result2 = manager.trigger("save", {"valid": False})
    assert result2.get("validated") is None
    assert result2.get("processed") is None


def test_error_isolation():
    """Test that plugin errors don't crash the application."""

    class FailingPlugin(PluginBase):
        name = "failing"

        @hook("test", priority=100)
        def fail(self, data):
            raise ValueError("Plugin error")

    class WorkingPlugin(PluginBase):
        name = "working"

        @hook("test", priority=50)
        def work(self, data):
            data["worked"] = True
            return data

    manager = PluginManager()
    manager.set_error_strategy("log_and_continue")
    manager.register(FailingPlugin)
    manager.register(WorkingPlugin)
    manager.load_all()

    # Should continue despite error
    result = manager.trigger("test", {})
    assert result["worked"] is True


def test_enable_disable_integration():
    """Test enabling and disabling plugins affects hook execution."""

    class OptionalPlugin(PluginBase):
        name = "optional"

        @hook("test")
        def process(self, data):
            data["optional"] = True
            return data

    manager = PluginManager()
    manager.register(OptionalPlugin)
    manager.load("optional")

    # Enabled
    result1 = manager.trigger("test", {})
    assert result1.get("optional") is True

    # Disabled
    manager.disable_plugin("optional")
    result2 = manager.trigger("test", {})
    assert result2.get("optional") is None

    # Re-enabled
    manager.enable_plugin("optional")
    result3 = manager.trigger("test", {})
    assert result3.get("optional") is True


def test_plugin_configuration_integration():
    """Test plugin configuration works end-to-end."""

    class ConfigurablePlugin(PluginBase):
        name = "configurable"

        def on_load(self):
            self.max_items = self.get_config("max_items", 10)
            self.timeout = self.get_config("timeout", 30)

    config = {"configurable": {"max_items": 100, "timeout": 60}}

    manager = PluginManager(config=config)
    manager.register(ConfigurablePlugin)
    plugin = manager.load("configurable")

    assert plugin.max_items == 100
    assert plugin.timeout == 60


def test_lifecycle_events_integration():
    """Test built-in lifecycle events."""
    events_captured = []

    def on_registered(data):
        events_captured.append(("registered", data["plugin_name"]))
        return data

    def on_loaded(data):
        events_captured.append(("loaded", data["plugin_name"]))
        return data

    def on_unloaded(data):
        events_captured.append(("unloaded", data["plugin_name"]))
        return data

    class TestPlugin(PluginBase):
        name = "test"

    manager = PluginManager()
    manager.register_hook(PluginManager.EVENT_PLUGIN_REGISTERED, on_registered)
    manager.register_hook(PluginManager.EVENT_PLUGIN_LOADED, on_loaded)
    manager.register_hook(PluginManager.EVENT_PLUGIN_UNLOADED, on_unloaded)

    manager.register(TestPlugin)
    manager.load("test")
    manager.unload("test")

    assert ("registered", "test") in events_captured
    assert ("loaded", "test") in events_captured
    assert ("unloaded", "test") in events_captured


def test_multiple_hooks_same_plugin():
    """Test plugin with multiple hooks on different events."""

    class MultiHookPlugin(PluginBase):
        name = "multi"

        def __init__(self):
            super().__init__()
            self.event_count = {}

        @hook("event1")
        def handle_event1(self, data):
            self.event_count["event1"] = self.event_count.get("event1", 0) + 1
            return data

        @hook("event2")
        def handle_event2(self, data):
            self.event_count["event2"] = self.event_count.get("event2", 0) + 1
            return data

        @hook("event3", priority=100)
        def handle_event3(self, data):
            self.event_count["event3"] = self.event_count.get("event3", 0) + 1
            return data

    manager = PluginManager()
    manager.register(MultiHookPlugin)
    plugin = manager.load("multi")

    manager.trigger("event1", {})
    manager.trigger("event2", {})
    manager.trigger("event3", {})

    assert plugin.event_count["event1"] == 1
    assert plugin.event_count["event2"] == 1
    assert plugin.event_count["event3"] == 1


def test_complex_data_transformation_pipeline():
    """Test complex data transformation through multiple plugins."""

    class InputPlugin(PluginBase):
        name = "input"

        @hook("transform", priority=100)
        def parse_input(self, data):
            data["parsed"] = data.get("raw", "").upper()
            return data

    class ValidatorPlugin(PluginBase):
        name = "validator"

        @hook("transform", priority=90)
        def validate(self, data):
            if len(data.get("parsed", "")) < 3:
                raise StopPropagation("Too short")
            data["valid"] = True
            return data

    class EnricherPlugin(PluginBase):
        name = "enricher"

        @hook("transform", priority=80)
        def enrich(self, data):
            data["length"] = len(data.get("parsed", ""))
            data["enriched"] = True
            return data

    class OutputPlugin(PluginBase):
        name = "output"

        @hook("transform", priority=70)
        def format_output(self, data):
            parsed = data.get("parsed")
            length = data.get("length")
            data["output"] = f"{parsed} ({length} chars)"
            return data

    manager = PluginManager()
    manager.register(InputPlugin)
    manager.register(ValidatorPlugin)
    manager.register(EnricherPlugin)
    manager.register(OutputPlugin)
    manager.load_all()

    result = manager.trigger("transform", {"raw": "hello world"})

    assert result["parsed"] == "HELLO WORLD"
    assert result["valid"] is True
    assert result["length"] == 11
    assert result["enriched"] is True
    assert result["output"] == "HELLO WORLD (11 chars)"


@pytest.mark.asyncio
async def test_real_world_async_scenario():
    """Test a real-world async scenario with API calls."""

    class AuthPlugin(PluginBase):
        name = "auth"

        @hook("api_request", priority=100)
        async def authenticate(self, data):
            await asyncio.sleep(0.01)  # Simulate auth check
            data["authenticated"] = True
            data["user_id"] = "user123"
            return data

    class RateLimitPlugin(PluginBase):
        name = "rate_limit"

        @hook("api_request", priority=90)
        async def check_rate_limit(self, data):
            await asyncio.sleep(0.01)  # Simulate rate limit check
            data["rate_limit_ok"] = True
            return data

    class LoggingPlugin(PluginBase):
        name = "logging"

        @hook("api_request", priority=10)
        def log_request(self, data):
            data["logged"] = True
            return data

    manager = PluginManager()
    manager.register(AuthPlugin)
    manager.register(RateLimitPlugin)
    manager.register(LoggingPlugin)
    manager.load_all()

    result = await manager.trigger_async("api_request", {"endpoint": "/api/users"})

    assert result["authenticated"] is True
    assert result["user_id"] == "user123"
    assert result["rate_limit_ok"] is True
    assert result["logged"] is True
