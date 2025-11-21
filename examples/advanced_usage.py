"""
Advanced usage example for Nitro Dispatch.

This example demonstrates:
1. Plugin dependencies
2. Data filtering chains
3. Error handling
4. Plugin configuration
5. Multiple hooks per event
"""

from nitro_dispatch import PluginManager, PluginBase, hook


class CachePlugin(PluginBase):
    """Plugin that caches data."""

    name = "cache"
    version = "1.0.0"
    description = "Caches processed data"

    def on_load(self):
        """Initialize cache on load."""
        self.cache = {}
        print(f"[{self.name}] Cache initialized")

    @hook("before_process")
    def check_cache(self, data):
        """Check if data is in cache."""
        key = data.get("id")
        if key and key in self.cache:
            print(f"[{self.name}] Cache hit for ID: {key}")
            data["cached"] = True
            data["value"] = self.cache[key]
        else:
            print(f"[{self.name}] Cache miss")
            data["cached"] = False
        return data

    @hook("after_process")
    def update_cache(self, data):
        """Store data in cache."""
        key = data.get("id")
        value = data.get("value")
        if key and value:
            self.cache[key] = value
            print(f"[{self.name}] Cached data for ID: {key}")
        return data


class ValidationPlugin(PluginBase):
    """Plugin that validates data."""

    name = "validator"
    version = "1.0.0"
    dependencies = ["cache"]  # Depends on cache plugin

    @hook("before_process")
    def validate(self, data):
        """Validate data structure."""
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")

        if "id" not in data:
            raise ValueError("Data must have an 'id' field")

        print(f"[{self.name}] Data validated")
        data["validated"] = True
        return data


class ProcessingPlugin(PluginBase):
    """Plugin that processes data."""

    name = "processor"
    version = "1.0.0"

    @hook("before_process")
    def process(self, data):
        """Process the data if not cached."""
        if not data.get("cached", False):
            # Simulate processing
            data["value"] = f"Processed: {data.get('id', 'unknown')}"
            print(f"[{self.name}] Data processed")
        else:
            print(f"[{self.name}] Using cached value")
        return data


class MetricsPlugin(PluginBase):
    """Plugin that tracks metrics."""

    name = "metrics"
    version = "1.0.0"

    def on_load(self):
        """Initialize metrics."""
        self.stats = {"processed": 0, "cached": 0, "errors": 0}

    @hook("after_process")
    def track_metrics(self, data):
        """Track processing metrics."""
        self.stats["processed"] += 1
        if data.get("cached", False):
            self.stats["cached"] += 1
        print(f"[{self.name}] Metrics updated: {self.stats}")
        return data

    def on_error(self, error):
        """Track errors."""
        self.stats["errors"] += 1
        print(f"[{self.name}] Error tracked: {error}")


def main():
    """Main function demonstrating advanced usage."""
    print("=" * 60)
    print("Nitro Dispatch - Advanced Usage Example")
    print("=" * 60)

    # Create plugin manager with configuration
    config = {"cache": {"max_size": 100, "ttl": 3600}, "processor": {"timeout": 30}}
    manager = PluginManager(config=config)

    # Register plugins (order doesn't matter - dependencies are resolved)
    print("\n1. Registering plugins...")
    manager.register(ProcessingPlugin)
    manager.register(ValidationPlugin)  # Has dependency on cache
    manager.register(CachePlugin)
    manager.register(MetricsPlugin)

    # Load all plugins (dependencies loaded first)
    print("\n2. Loading plugins in dependency order...")
    loaded = manager.load_all()
    print(f"   Loaded: {loaded}")

    # Process data - first time (cache miss)
    print("\n3. Processing data (first time - cache miss)...")
    data1 = {"id": "item_001"}
    result1 = manager.trigger("before_process", data1)
    result1 = manager.trigger("after_process", result1)
    print(f"   Result: {result1}")

    # Process same data - second time (cache hit)
    print("\n4. Processing same data (second time - cache hit)...")
    data2 = {"id": "item_001"}
    result2 = manager.trigger("before_process", data2)
    result2 = manager.trigger("after_process", result2)
    print(f"   Result: {result2}")

    # Process different data
    print("\n5. Processing different data...")
    data3 = {"id": "item_002"}
    result3 = manager.trigger("before_process", data3)
    result3 = manager.trigger("after_process", result3)
    print(f"   Result: {result3}")

    # Demonstrate error handling
    print("\n6. Testing error handling...")
    manager.set_error_strategy("log_and_continue")
    try:
        # This will fail validation (no 'id' field)
        bad_data = {"name": "test"}
        result = manager.trigger("before_process", bad_data)
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   Error caught: {e}")

    # Get metrics
    print("\n7. Final metrics...")
    metrics_plugin = manager.get_plugin("metrics")
    if metrics_plugin:
        print(f"   Stats: {metrics_plugin.stats}")

    # Plugin configuration example
    print("\n8. Using plugin configuration...")
    cache_plugin = manager.get_plugin("cache")
    if cache_plugin:
        max_size = cache_plugin.get_config("max_size", 50)
        ttl = cache_plugin.get_config("ttl", 1800)
        print(f"   Cache config - max_size: {max_size}, ttl: {ttl}")

    # Get all events
    print("\n9. Registered events...")
    events = manager.get_events()
    print(f"   Events: {events}")

    # Cleanup
    print("\n10. Cleaning up...")
    manager.unload_all()
    print("   All plugins unloaded")

    print("\n" + "=" * 60)
    print("Advanced example completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
