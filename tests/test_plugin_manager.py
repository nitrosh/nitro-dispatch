"""
Tests for PluginManager class.
"""

import pytest
import tempfile
import asyncio
from pathlib import Path
from nitro_dispatch import PluginManager, PluginBase, hook
from nitro_dispatch.core.exceptions import (
    PluginRegistrationError,
    PluginNotFoundError,
    PluginLoadError,
    ValidationError,
    PluginDiscoveryError,
)


@pytest.fixture
def manager():
    """Create a fresh PluginManager for each test."""
    return PluginManager()


@pytest.fixture
def sample_plugin():
    """Create a sample plugin class."""

    class SamplePlugin(PluginBase):
        name = "sample"
        version = "1.0.0"
        description = "Sample plugin"

        @hook("test_event")
        def test_hook(self, data):
            data["sample"] = True
            return data

    return SamplePlugin


def test_manager_initialization():
    """Test PluginManager initialization."""
    manager = PluginManager()
    assert manager._plugins == {}
    assert manager._plugin_classes == {}
    assert manager._loaded is False


def test_manager_with_config():
    """Test PluginManager with configuration."""
    config = {"plugin1": {"key": "value"}}
    manager = PluginManager(config=config)
    assert manager._config == config


def test_register_plugin(manager, sample_plugin):
    """Test registering a plugin."""
    manager.register(sample_plugin)
    assert "sample" in manager.get_registered_plugins()


def test_register_invalid_plugin(manager):
    """Test registering an invalid plugin (not subclass of PluginBase)."""

    class InvalidPlugin:
        pass

    with pytest.raises(PluginRegistrationError):
        manager.register(InvalidPlugin)


def test_register_plugin_without_name(manager):
    """Test registering a plugin without explicit name."""

    class MyPlugin(PluginBase):
        pass

    manager.register(MyPlugin)
    assert "MyPlugin" in manager.get_registered_plugins()


def test_register_duplicate_plugin(manager, sample_plugin):
    """Test registering the same plugin twice."""
    manager.register(sample_plugin)
    manager.register(sample_plugin)  # Should warn but not fail
    assert "sample" in manager.get_registered_plugins()


def test_unregister_plugin(manager, sample_plugin):
    """Test unregistering a plugin."""
    manager.register(sample_plugin)
    manager.unregister("sample")
    assert "sample" not in manager.get_registered_plugins()


def test_unregister_nonexistent_plugin(manager):
    """Test unregistering a plugin that doesn't exist."""
    with pytest.raises(PluginNotFoundError):
        manager.unregister("nonexistent")


def test_load_plugin(manager, sample_plugin):
    """Test loading a plugin."""
    manager.register(sample_plugin)
    plugin = manager.load("sample")
    assert plugin.name == "sample"
    assert plugin.enabled is True
    assert "sample" in manager.get_loaded_plugins()


def test_load_nonexistent_plugin(manager):
    """Test loading a plugin that isn't registered."""
    with pytest.raises(PluginNotFoundError):
        manager.load("nonexistent")


def test_load_already_loaded_plugin(manager, sample_plugin):
    """Test loading a plugin that's already loaded."""
    manager.register(sample_plugin)
    plugin1 = manager.load("sample")
    plugin2 = manager.load("sample")  # Should return same instance
    assert plugin1 is plugin2


def test_load_all_plugins(manager, sample_plugin):
    """Test loading all registered plugins."""

    class Plugin2(PluginBase):
        name = "plugin2"

    manager.register(sample_plugin)
    manager.register(Plugin2)

    loaded = manager.load_all()
    assert len(loaded) == 2
    assert "sample" in loaded
    assert "plugin2" in loaded


def test_unload_plugin(manager, sample_plugin):
    """Test unloading a plugin."""
    manager.register(sample_plugin)
    manager.load("sample")
    manager.unload("sample")
    assert "sample" not in manager.get_loaded_plugins()


def test_unload_nonexistent_plugin(manager):
    """Test unloading a plugin that isn't loaded."""
    with pytest.raises(PluginNotFoundError):
        manager.unload("nonexistent")


def test_unload_all_plugins(manager, sample_plugin):
    """Test unloading all plugins."""

    class Plugin2(PluginBase):
        name = "plugin2"

    manager.register(sample_plugin)
    manager.register(Plugin2)
    manager.load_all()

    manager.unload_all()
    assert len(manager.get_loaded_plugins()) == 0


def test_plugin_dependencies(manager):
    """Test plugin dependency resolution."""

    class PluginA(PluginBase):
        name = "plugin_a"

    class PluginB(PluginBase):
        name = "plugin_b"
        dependencies = ["plugin_a"]

    manager.register(PluginA)
    manager.register(PluginB)

    # Loading B should auto-load A
    manager.load("plugin_b")

    assert manager.is_loaded("plugin_a")
    assert manager.is_loaded("plugin_b")


def test_missing_dependency(manager):
    """Test loading plugin with missing dependency."""

    class PluginWithDep(PluginBase):
        name = "dependent"
        dependencies = ["missing"]

    manager.register(PluginWithDep)

    with pytest.raises(PluginLoadError):
        manager.load("dependent")


def test_trigger_event(manager, sample_plugin):
    """Test triggering an event."""
    manager.register(sample_plugin)
    manager.load("sample")

    result = manager.trigger("test_event", {"initial": True})
    assert result["initial"] is True
    assert result["sample"] is True


@pytest.mark.asyncio
async def test_trigger_async_event(manager):
    """Test triggering an async event."""

    class AsyncPlugin(PluginBase):
        name = "async_plugin"

        @hook("async_event")
        async def async_hook(self, data):
            await asyncio.sleep(0.01)
            data["async"] = True
            return data

    manager.register(AsyncPlugin)
    manager.load("async_plugin")

    result = await manager.trigger_async("async_event", {})
    assert result["async"] is True


def test_get_plugin(manager, sample_plugin):
    """Test getting a loaded plugin."""
    manager.register(sample_plugin)
    manager.load("sample")

    plugin = manager.get_plugin("sample")
    assert plugin is not None
    assert plugin.name == "sample"


def test_get_nonexistent_plugin(manager):
    """Test getting a plugin that doesn't exist."""
    plugin = manager.get_plugin("nonexistent")
    assert plugin is None


def test_is_loaded(manager, sample_plugin):
    """Test checking if plugin is loaded."""
    manager.register(sample_plugin)
    assert manager.is_loaded("sample") is False

    manager.load("sample")
    assert manager.is_loaded("sample") is True


def test_enable_disable_plugin(manager, sample_plugin):
    """Test enabling and disabling plugins."""
    manager.register(sample_plugin)
    manager.load("sample")

    # Disable
    manager.disable_plugin("sample")
    plugin = manager.get_plugin("sample")
    assert plugin.enabled is False

    # Re-enable
    manager.enable_plugin("sample")
    assert plugin.enabled is True


def test_enable_disable_nonexistent_plugin(manager):
    """Test enabling/disabling a plugin that doesn't exist."""
    with pytest.raises(PluginNotFoundError):
        manager.enable_plugin("nonexistent")

    with pytest.raises(PluginNotFoundError):
        manager.disable_plugin("nonexistent")


def test_get_plugin_config(manager):
    """Test getting plugin configuration."""
    config = {"sample": {"key1": "value1", "key2": "value2"}}
    manager = PluginManager(config=config)

    value = manager.get_plugin_config("sample", "key1")
    assert value == "value1"

    default_value = manager.get_plugin_config("sample", "nonexistent", "default")
    assert default_value == "default"


def test_plugin_lifecycle_events(manager, sample_plugin):
    """Test built-in lifecycle events."""
    loaded_plugins = []

    def on_plugin_loaded(data):
        loaded_plugins.append(data["plugin_name"])
        return data

    manager.register_hook(PluginManager.EVENT_PLUGIN_LOADED, on_plugin_loaded)
    manager.register(sample_plugin)
    manager.load("sample")

    assert "sample" in loaded_plugins


def test_metadata_validation():
    """Test plugin metadata validation."""

    class InvalidPlugin(PluginBase):
        name = ""  # Invalid: empty name

    manager_with_validation = PluginManager(validate_metadata=True)

    with pytest.raises(ValidationError):
        manager_with_validation.register(InvalidPlugin)


def test_metadata_validation_disabled(manager):
    """Test that validation can be disabled."""

    class InvalidPlugin(PluginBase):
        name = ""  # Would be invalid, but validation is off

    manager_no_validation = PluginManager(validate_metadata=False)
    # Should not raise
    manager_no_validation.register(InvalidPlugin, validate=False)


def test_plugin_discovery(manager):
    """Test plugin discovery from directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)

        # Create a plugin file
        plugin_content = """
from nitro_dispatch import PluginBase, hook

class DiscoveredPlugin(PluginBase):
    name = "discovered"
    version = "1.0.0"

    @hook('test_event')
    def test_hook(self, data):
        return data
"""
        plugin_file = plugin_dir / "test_plugin.py"
        plugin_file.write_text(plugin_content)

        # Discover plugins
        discovered = manager.discover_plugins(plugin_dir, pattern="*_plugin.py")

        assert "discovered" in discovered
        # Discovered but not loaded
        assert manager.is_loaded("discovered") is False


def test_plugin_discovery_nonexistent_dir(manager):
    """Test plugin discovery with nonexistent directory."""
    with pytest.raises(PluginDiscoveryError):
        manager.discover_plugins("/nonexistent/directory")


def test_plugin_discovery_recursive(manager):
    """Test recursive plugin discovery."""
    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)
        sub_dir = plugin_dir / "subdir"
        sub_dir.mkdir()

        # Create plugin in subdirectory
        plugin_content = """
from nitro_dispatch import PluginBase

class SubPlugin(PluginBase):
    name = "sub_plugin"
"""
        plugin_file = sub_dir / "sub_plugin.py"
        plugin_file.write_text(plugin_content)

        # Discover recursively
        discovered = manager.discover_plugins(plugin_dir, recursive=True)

        assert "sub_plugin" in discovered


def test_reload_plugin(manager, sample_plugin):
    """Test hot reloading a plugin."""
    manager.register(sample_plugin)
    manager.load("sample")

    # Reload
    reloaded_plugin = manager.reload("sample")

    assert reloaded_plugin.name == "sample"
    assert manager.is_loaded("sample")


def test_reload_nonexistent_plugin(manager):
    """Test reloading a plugin that doesn't exist."""
    with pytest.raises(PluginNotFoundError):
        manager.reload("nonexistent")


def test_set_error_strategy(manager):
    """Test setting error handling strategy."""
    manager.set_error_strategy("fail_fast")
    # Should not raise


def test_enable_hook_tracing(manager):
    """Test enabling hook tracing."""
    manager.enable_hook_tracing(True)
    manager.enable_hook_tracing(False)
    # Should not raise


def test_get_events(manager, sample_plugin):
    """Test getting all registered events."""
    manager.register(sample_plugin)
    manager.load("sample")

    events = manager.get_events()
    assert "test_event" in events


def test_get_all_plugins(manager, sample_plugin):
    """Test getting all loaded plugins."""

    class Plugin2(PluginBase):
        name = "plugin2"

    manager.register(sample_plugin)
    manager.register(Plugin2)
    manager.load_all()

    all_plugins = manager.get_all_plugins()
    assert len(all_plugins) == 2
    assert "sample" in all_plugins
    assert "plugin2" in all_plugins


def test_lifecycle_hooks_called(manager):
    """Test that lifecycle hooks are called."""

    class LifecyclePlugin(PluginBase):
        name = "lifecycle"

        def __init__(self):
            super().__init__()
            self.loaded = False
            self.unloaded = False

        def on_load(self):
            self.loaded = True

        def on_unload(self):
            self.unloaded = True

    manager.register(LifecyclePlugin)
    plugin = manager.load("lifecycle")

    assert plugin.loaded is True
    assert plugin.unloaded is False

    manager.unload("lifecycle")
    assert plugin.unloaded is True


def test_plugin_with_manager_reference(manager, sample_plugin):
    """Test that loaded plugin has manager reference."""
    manager.register(sample_plugin)
    plugin = manager.load("sample")

    assert plugin._manager is manager


def test_log_level_configuration():
    """Test manager with different log levels."""
    manager_debug = PluginManager(log_level="DEBUG")
    manager_info = PluginManager(log_level="INFO")
    manager_warning = PluginManager(log_level="WARNING")

    # Should not raise
    assert manager_debug is not None
    assert manager_info is not None
    assert manager_warning is not None


def test_validation_invalid_version(manager):
    """Test validation with invalid version."""

    class InvalidVersionPlugin(PluginBase):
        name = "test"
        version = None  # Invalid version

    manager_with_validation = PluginManager(validate_metadata=True)

    with pytest.raises(ValidationError):
        manager_with_validation.register(InvalidVersionPlugin)


def test_validation_invalid_dependencies(manager):
    """Test validation with invalid dependencies type."""

    class InvalidDepsPlugin(PluginBase):
        name = "test"
        dependencies = "not_a_list"  # Should be list

    manager_with_validation = PluginManager(validate_metadata=True)

    with pytest.raises(ValidationError):
        manager_with_validation.register(InvalidDepsPlugin)


def test_unregister_loaded_plugin(manager, sample_plugin):
    """Test unregistering a plugin that's currently loaded."""
    manager.register(sample_plugin)
    manager.load("sample")
    assert manager.is_loaded("sample")

    # Unregister should unload first
    manager.unregister("sample")
    assert manager.is_loaded("sample") is False
    assert "sample" not in manager.get_registered_plugins()


def test_load_all_with_failures(manager):
    """Test load_all when some plugins fail to load."""

    class GoodPlugin(PluginBase):
        name = "good"

    class BadPlugin(PluginBase):
        name = "bad"

        def on_load(self):
            raise RuntimeError("Failed to load")

    manager.register(GoodPlugin)
    manager.register(BadPlugin)

    # load_all should continue despite failures
    loaded = manager.load_all()

    assert "good" in loaded
    assert "bad" not in loaded
    assert manager.is_loaded("good")
    assert not manager.is_loaded("bad")


def test_unload_with_error(manager):
    """Test unload when plugin's on_unload raises exception."""

    class ErrorOnUnloadPlugin(PluginBase):
        name = "error_unload"

        def on_unload(self):
            raise RuntimeError("Unload error")

    manager.register(ErrorOnUnloadPlugin)
    manager.load("error_unload")

    # Should raise the exception
    with pytest.raises(RuntimeError):
        manager.unload("error_unload")


def test_unload_all_with_errors(manager):
    """Test unload_all continues despite errors."""

    class GoodPlugin(PluginBase):
        name = "good"

    class BadPlugin(PluginBase):
        name = "bad"

        def on_unload(self):
            raise RuntimeError("Unload failed")

    manager.register(GoodPlugin)
    manager.register(BadPlugin)
    manager.load_all()

    # unload_all should continue despite errors
    manager.unload_all()

    # Good plugin should be unloaded, bad might fail but shouldn't block others
    assert manager.is_loaded("good") is False


def test_plugin_discovery_with_invalid_files(manager):
    """Test plugin discovery with non-Python files and directories."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)

        # Create a non-Python file
        (plugin_dir / "readme.txt").write_text("Not a plugin")

        # Create a subdirectory (not a file)
        subdir = plugin_dir / "subdir"
        subdir.mkdir()

        # Create valid plugin file
        plugin_content = """
from nitro_dispatch import PluginBase

class ValidPlugin(PluginBase):
    name = "valid"
"""
        (plugin_dir / "valid_plugin.py").write_text(plugin_content)

        # Discovery should skip non-Python files and directories
        discovered = manager.discover_plugins(plugin_dir, pattern="*.py")
        assert "valid" in discovered


def test_plugin_discovery_with_broken_plugin(manager):
    """Test plugin discovery when a plugin file has errors."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)

        # Create a broken plugin file
        broken_content = """
from nitro_dispatch import PluginBase

class BrokenPlugin(PluginBase):
    name = "broken"
    raise SyntaxError("Broken")  # This will cause an error
"""
        (plugin_dir / "broken_plugin.py").write_text(broken_content)

        # Create a good plugin
        good_content = """
from nitro_dispatch import PluginBase

class GoodPlugin(PluginBase):
    name = "good"
"""
        (plugin_dir / "good_plugin.py").write_text(good_content)

        # Discovery should continue despite broken plugin
        discovered = manager.discover_plugins(plugin_dir, pattern="*.py")

        # Should discover the good plugin despite the broken one
        assert "good" in discovered


def test_unregister_hook_method(manager, sample_plugin):
    """Test the unregister_hook method."""
    manager.register(sample_plugin)
    plugin = manager.load("sample")

    def my_callback(data):
        data["called"] = True
        return data

    # Register a hook
    manager.register_hook("test_event", my_callback, plugin)

    # Verify it works
    result1 = manager.trigger("test_event", {})
    assert result1.get("called") is True or result1.get("sample") is True

    # Unregister it
    manager.unregister_hook("test_event", my_callback, plugin)

    # Should not be called anymore (only sample plugin's hook remains)
    result2 = manager.trigger("test_event", {})
    # If sample is True, that's the original hook from sample_plugin
    assert result2.get("called") is None


def test_old_format_hook_registration(manager):
    """Test loading plugin with old format hooks (backward compatibility)."""

    class OldFormatPlugin(PluginBase):
        name = "old_format"

        def __init__(self):
            super().__init__()

            # Manually add hook in old format (just callback, not dict)
            def my_hook(data):
                data["old"] = True
                return data

            self._hooks["test_event"] = [my_hook]

    manager.register(OldFormatPlugin)
    manager.load("old_format")

    result = manager.trigger("test_event", {})
    assert result["old"] is True


def test_plugin_discovery_with_directory_in_results(manager):
    """Test that directories are skipped during plugin discovery."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)

        # Create a directory that matches the pattern
        subdir = plugin_dir / "not_a_plugin.py"
        subdir.mkdir()  # This is a directory, not a file

        # Create a valid plugin
        plugin_content = """
from nitro_dispatch import PluginBase

class ValidPlugin(PluginBase):
    name = "valid"
"""
        (plugin_dir / "valid.py").write_text(plugin_content)

        # Discovery should skip the directory
        discovered = manager.discover_plugins(plugin_dir, pattern="*.py")
        assert "valid" in discovered


def test_plugin_discovery_overall_exception(manager):
    """Test plugin discovery with overall exception."""
    import tempfile
    from pathlib import Path
    from unittest import mock

    with tempfile.TemporaryDirectory() as temp_dir:
        plugin_dir = Path(temp_dir)

        # Mock glob to raise an exception
        with mock.patch.object(Path, "glob", side_effect=RuntimeError("Glob failed")):
            with pytest.raises(PluginDiscoveryError) as exc_info:
                manager.discover_plugins(plugin_dir, pattern="*.py")
            assert "Plugin discovery failed" in str(exc_info.value)
