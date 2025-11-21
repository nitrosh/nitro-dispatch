"""
Plugin discovery demonstration.

This example shows how to auto-discover plugins from a directory.
"""

from nitro_dispatch import PluginManager, PluginBase, hook
import os
import tempfile
from pathlib import Path


def create_sample_plugins(plugin_dir):
    """Create sample plugin files for discovery."""
    # Create plugin 1
    plugin1_content = """
from nitro_dispatch import PluginBase, hook

class DiscoveredPlugin1(PluginBase):
    name = "discovered_1"
    version = "1.0.0"
    description = "Auto-discovered plugin 1"

    @hook('test.event')
    def process(self, data):
        print(f"[{self.name}] Processing from discovered plugin 1")
        data['plugin1'] = True
        return data
"""

    # Create plugin 2
    plugin2_content = """
from nitro_dispatch import PluginBase, hook

class DiscoveredPlugin2(PluginBase):
    name = "discovered_2"
    version = "2.0.0"
    description = "Auto-discovered plugin 2"

    @hook('test.event', priority=100)
    def process(self, data):
        print(f"[{self.name}] Processing from discovered plugin 2 (high priority)")
        data['plugin2'] = True
        return data
"""

    # Write plugin files
    plugin1_file = plugin_dir / "example_plugin.py"
    plugin2_file = plugin_dir / "another_plugin.py"

    plugin1_file.write_text(plugin1_content)
    plugin2_file.write_text(plugin2_content)

    print(f"Created sample plugins in: {plugin_dir}")
    print(f"  - {plugin1_file.name}")
    print(f"  - {plugin2_file.name}")

    return [plugin1_file, plugin2_file]


def main():
    """Demonstrate plugin discovery."""
    print("=" * 60)
    print("Nitro Plugins - Discovery Example")
    print("=" * 60)

    # Create temporary directory for plugins
    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir) / "plugins"
        plugin_dir.mkdir()

        print("\n1. Creating sample plugin files...")
        create_sample_plugins(plugin_dir)

        # Create plugin manager
        manager = PluginManager()

        print("\n2. Discovering plugins...")
        discovered = manager.discover_plugins(plugin_dir, pattern="*_plugin.py")
        print(f"\nDiscovered {len(discovered)} plugins: {discovered}")

        print("\n3. Loading discovered plugins...")
        loaded = manager.load_all()
        print(f"Loaded: {loaded}")

        print("\n4. Triggering test event...")
        result = manager.trigger("test.event", {"source": "discovery_example"})
        print(f"\nResult: {result}")

        print("\n5. Plugin information:")
        for plugin_name in manager.get_loaded_plugins():
            plugin = manager.get_plugin(plugin_name)
            print(f"  - {plugin.name} v{plugin.version}: {plugin.description}")

    print("\n" + "=" * 60)
    print("Discovery example completed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
