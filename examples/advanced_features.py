"""
Advanced features demonstration for Nitro Dispatch.

This example demonstrates:
1. Hook priorities
2. Async hooks
3. Hook timeouts
4. Event namespacing with wildcards
5. Stop propagation
6. Plugin discovery
7. Hot reloading
8. Built-in lifecycle events
9. Hook tracing/debugging
"""

import asyncio
import time
from nitro_dispatch import PluginManager, PluginBase, hook, StopPropagation


# ============================================================================
# Feature 1: Hook Priority
# ============================================================================
class HighPriorityPlugin(PluginBase):
    """Demonstrates high priority hooks that run first."""

    name = "high_priority"
    version = "1.0.0"

    @hook("user.login", priority=100)  # Runs first (higher priority)
    def security_check(self, data):
        """High-priority security validation."""
        print(f"[{self.name}] Security check (priority=100)")
        if not data.get("username"):
            raise ValueError("Username required")
        data["security_checked"] = True
        return data


class LowPriorityPlugin(PluginBase):
    """Demonstrates low priority hooks that run last."""

    name = "low_priority"
    version = "1.0.0"

    @hook("user.login", priority=10)  # Runs last (lower priority)
    def log_login(self, data):
        """Low-priority logging."""
        print(f"[{self.name}] Logging (priority=10)")
        data["logged"] = True
        return data


# ============================================================================
# Feature 2: Async Hooks
# ============================================================================
class AsyncPlugin(PluginBase):
    """Demonstrates async hook support."""

    name = "async_plugin"
    version = "1.0.0"

    @hook("data.fetch")
    async def fetch_data(self, data):
        """Async data fetching."""
        print(f"[{self.name}] Fetching data asynchronously...")
        await asyncio.sleep(0.5)  # Simulate API call
        data["fetched"] = True
        data["timestamp"] = time.time()
        print(f"[{self.name}] Data fetched!")
        return data


# ============================================================================
# Feature 3: Hook Timeouts
# ============================================================================
class TimeoutPlugin(PluginBase):
    """Demonstrates hook timeout protection."""

    name = "timeout_plugin"
    version = "1.0.0"

    @hook("process.data", timeout=2.0)  # 2 second timeout
    def fast_process(self, data):
        """Fast processing with timeout."""
        print(f"[{self.name}] Processing quickly...")
        time.sleep(0.5)  # Simulates processing
        data["processed"] = True
        return data


# ============================================================================
# Feature 4: Event Namespacing with Wildcards
# ============================================================================
class WildcardPlugin(PluginBase):
    """Demonstrates wildcard event matching."""

    name = "wildcard_plugin"
    version = "1.0.0"

    @hook("user.*", priority=75)  # Matches all user.* events
    def log_all_user_events(self, data):
        """Logs all user-related events."""
        print(f"[{self.name}] User event captured by wildcard")
        data["wildcarded"] = True
        return data

    @hook("db.before_*", priority=80)  # Matches db.before_save, db.before_delete, etc.
    def audit_db_operations(self, data):
        """Audits all 'before' database operations."""
        print(f"[{self.name}] DB operation audited")
        data["audited"] = True
        return data


# ============================================================================
# Feature 5: Stop Propagation
# ============================================================================
class ValidationPlugin(PluginBase):
    """Demonstrates stopping hook propagation."""

    name = "validation_plugin"
    version = "1.0.0"

    @hook("data.validate", priority=90)
    def strict_validation(self, data):
        """Validates data and stops propagation if invalid."""
        print(f"[{self.name}] Validating...")
        if data.get("invalid"):
            print(f"[{self.name}] Invalid data! Stopping propagation.")
            raise StopPropagation("Data validation failed")
        data["validated"] = True
        return data


class PostValidationPlugin(PluginBase):
    """This plugin's hook won't run if validation stops propagation."""

    name = "post_validation"
    version = "1.0.0"

    @hook("data.validate", priority=50)
    def process_after_validation(self, data):
        """Only runs if validation passes."""
        print(f"[{self.name}] Processing after validation")
        data["post_processed"] = True
        return data


# ============================================================================
# Feature 6: Enable/Disable Plugins
# ============================================================================
class OptionalPlugin(PluginBase):
    """Plugin that can be disabled at runtime."""

    name = "optional_plugin"
    version = "1.0.0"

    @hook("test.event")
    def optional_hook(self, data):
        """This hook only runs when plugin is enabled."""
        print(f"[{self.name}] Optional hook executed")
        data["optional_ran"] = True
        return data


def demo_priorities():
    """Demonstrate hook priorities."""
    print("\n" + "=" * 60)
    print("DEMO 1: Hook Priorities")
    print("=" * 60)

    manager = PluginManager()
    manager.register(LowPriorityPlugin)
    manager.register(HighPriorityPlugin)
    manager.load_all()

    result = manager.trigger("user.login", {"username": "alice"})
    print(f"Result: {result}\n")


async def demo_async():
    """Demonstrate async hooks."""
    print("\n" + "=" * 60)
    print("DEMO 2: Async Hooks")
    print("=" * 60)

    manager = PluginManager()
    manager.register(AsyncPlugin)
    manager.load_all()

    result = await manager.trigger_async("data.fetch", {"query": "users"})
    print(f"Result: {result}\n")


def demo_wildcards():
    """Demonstrate wildcard event matching."""
    print("\n" + "=" * 60)
    print("DEMO 3: Event Namespacing with Wildcards")
    print("=" * 60)

    manager = PluginManager()
    manager.register(WildcardPlugin)
    manager.load_all()

    print("\nTriggering 'user.login':")
    manager.trigger("user.login", {})

    print("\nTriggering 'user.logout':")
    manager.trigger("user.logout", {})

    print("\nTriggering 'db.before_save':")
    manager.trigger("db.before_save", {})

    print()


def demo_stop_propagation():
    """Demonstrate stop propagation."""
    print("\n" + "=" * 60)
    print("DEMO 4: Stop Propagation")
    print("=" * 60)

    manager = PluginManager()
    manager.register(ValidationPlugin)
    manager.register(PostValidationPlugin)
    manager.load_all()

    print("\nValid data:")
    result1 = manager.trigger("data.validate", {"valid": True})
    print(f"Result: {result1}")

    print("\nInvalid data:")
    result2 = manager.trigger("data.validate", {"invalid": True})
    print(f"Result: {result2}\n")


def demo_enable_disable():
    """Demonstrate enable/disable plugins."""
    print("\n" + "=" * 60)
    print("DEMO 5: Enable/Disable Plugins")
    print("=" * 60)

    manager = PluginManager()
    manager.register(OptionalPlugin)
    manager.load_all()

    print("\nPlugin enabled:")
    result1 = manager.trigger("test.event", {})
    print(f"Result: {result1}")

    print("\nDisabling plugin...")
    manager.disable_plugin("optional_plugin")

    print("\nPlugin disabled (hook won't run):")
    result2 = manager.trigger("test.event", {})
    print(f"Result: {result2}")

    print("\nRe-enabling plugin...")
    manager.enable_plugin("optional_plugin")

    print("\nPlugin enabled again:")
    result3 = manager.trigger("test.event", {})
    print(f"Result: {result3}\n")


def demo_hook_tracing():
    """Demonstrate hook tracing for debugging."""
    print("\n" + "=" * 60)
    print("DEMO 6: Hook Tracing (Debugging)")
    print("=" * 60)

    manager = PluginManager(log_level="DEBUG")
    manager.enable_hook_tracing(True)
    manager.register(HighPriorityPlugin)
    manager.register(LowPriorityPlugin)
    manager.load_all()

    print("\nWith hook tracing enabled:")
    result = manager.trigger("user.login", {"username": "bob"})
    print()


def demo_built_in_events():
    """Demonstrate built-in lifecycle events."""
    print("\n" + "=" * 60)
    print("DEMO 7: Built-in Lifecycle Events")
    print("=" * 60)

    manager = PluginManager()

    # Create a simple listener function for lifecycle events
    def on_plugin_loaded(data):
        print(f"[LIFECYCLE] Plugin loaded: {data['plugin_name']} v{data['version']}")
        return data

    def on_plugin_registered(data):
        print(f"[LIFECYCLE] Plugin registered: {data['plugin_name']} v{data['version']}")
        return data

    # Register listeners for built-in events
    manager.register_hook(PluginManager.EVENT_PLUGIN_LOADED, on_plugin_loaded)
    manager.register_hook(PluginManager.EVENT_PLUGIN_REGISTERED, on_plugin_registered)

    print("\nLoading plugins (watch for lifecycle events):")
    manager.register(HighPriorityPlugin)
    manager.load("high_priority")
    print()


def main():
    """Run all demonstrations."""
    print("\n" + "=" * 70)
    print(" Nitro Plugins - Advanced Features Demonstration")
    print("=" * 70)

    # Run sync demos
    demo_priorities()
    demo_wildcards()
    demo_stop_propagation()
    demo_enable_disable()
    demo_hook_tracing()
    demo_built_in_events()

    # Run async demo
    print("\n" + "=" * 60)
    print("DEMO 2: Async Hooks (Running async demo)")
    print("=" * 60)
    asyncio.run(demo_async())

    print("\n" + "=" * 70)
    print(" All demonstrations completed!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
