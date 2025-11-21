"""
Tests for PluginBase class.
"""

from nitro_dispatch import PluginBase, hook


def test_plugin_base_initialization():
    """Test PluginBase initialization."""

    class TestPlugin(PluginBase):
        name = "test_plugin"
        version = "1.0.0"

    plugin = TestPlugin()
    assert plugin.name == "test_plugin"
    assert plugin.version == "1.0.0"
    assert plugin.enabled is False
    assert plugin._manager is None
    assert isinstance(plugin._hooks, dict)


def test_plugin_base_auto_name():
    """Test that plugin gets class name if name not provided."""

    class MyCustomPlugin(PluginBase):
        pass

    plugin = MyCustomPlugin()
    assert plugin.name == "MyCustomPlugin"


def test_plugin_base_defaults():
    """Test PluginBase default values."""

    class TestPlugin(PluginBase):
        name = "test"

    plugin = TestPlugin()
    assert plugin.version == "1.0.0"
    assert plugin.description == ""
    assert plugin.author == ""
    assert plugin.dependencies == []


def test_plugin_base_custom_metadata():
    """Test PluginBase with custom metadata."""

    class TestPlugin(PluginBase):
        name = "test"
        version = "2.0.0"
        description = "Test plugin"
        author = "Test Author"
        dependencies = ["dep1", "dep2"]

    plugin = TestPlugin()
    assert plugin.version == "2.0.0"
    assert plugin.description == "Test plugin"
    assert plugin.author == "Test Author"
    assert plugin.dependencies == ["dep1", "dep2"]


def test_plugin_lifecycle_hooks():
    """Test plugin lifecycle hooks."""

    class TestPlugin(PluginBase):
        name = "test"

        def __init__(self):
            super().__init__()
            self.loaded = False
            self.unloaded = False
            self.error_caught = None

        def on_load(self):
            self.loaded = True

        def on_unload(self):
            self.unloaded = True

        def on_error(self, error):
            self.error_caught = error

    plugin = TestPlugin()
    assert plugin.loaded is False
    assert plugin.unloaded is False

    plugin.on_load()
    assert plugin.loaded is True

    plugin.on_unload()
    assert plugin.unloaded is True

    test_error = Exception("test")
    plugin.on_error(test_error)
    assert plugin.error_caught == test_error


def test_register_hook():
    """Test register_hook method."""

    class TestPlugin(PluginBase):
        name = "test"

    plugin = TestPlugin()

    def my_callback(data):
        return data

    # Without manager
    plugin.register_hook("test_event", my_callback)
    assert "test_event" in plugin._hooks
    assert len(plugin._hooks["test_event"]) == 1


def test_register_hook_with_priority():
    """Test register_hook with priority and timeout."""

    class TestPlugin(PluginBase):
        name = "test"

    plugin = TestPlugin()

    def my_callback(data):
        return data

    plugin.register_hook("test_event", my_callback, priority=100, timeout=5.0)
    assert "test_event" in plugin._hooks
    hook_data = plugin._hooks["test_event"][0]
    assert hook_data["priority"] == 100
    assert hook_data["timeout"] == 5.0


def test_get_config_without_manager():
    """Test get_config without manager."""

    class TestPlugin(PluginBase):
        name = "test"

    plugin = TestPlugin()
    assert plugin.get_config("key", "default") == "default"


def test_trigger_without_manager():
    """Test trigger without manager."""

    class TestPlugin(PluginBase):
        name = "test"

    plugin = TestPlugin()
    data = {"test": True}
    result = plugin.trigger("event", data)
    assert result == data


def test_repr():
    """Test __repr__ method."""

    class TestPlugin(PluginBase):
        name = "test_plugin"
        version = "1.5.0"

    plugin = TestPlugin()
    repr_str = repr(plugin)
    assert "TestPlugin" in repr_str
    assert "test_plugin" in repr_str
    assert "1.5.0" in repr_str


def test_auto_collect_decorated_hooks():
    """Test that decorated hooks are auto-collected."""

    class TestPlugin(PluginBase):
        name = "test"

        @hook("event1")
        def hook1(self, data):
            return data

        @hook("event2", priority=100)
        def hook2(self, data):
            return data

    plugin = TestPlugin()
    assert "event1" in plugin._hooks
    assert "event2" in plugin._hooks
    assert len(plugin._hooks["event1"]) == 1
    assert len(plugin._hooks["event2"]) == 1


def test_register_hook_with_manager():
    """Test registering a hook after plugin is loaded with manager."""
    from nitro_dispatch import PluginManager

    class TestPlugin(PluginBase):
        name = "test"

    manager = PluginManager()
    manager.register(TestPlugin)
    plugin = manager.load("test")

    def my_callback(data):
        data["called"] = True
        return data

    # Register hook after loading
    plugin.register_hook("new_event", my_callback, priority=100)

    # Trigger and verify
    result = manager.trigger("new_event", {})
    assert result["called"] is True


def test_unregister_hook_with_manager():
    """Test unregistering a hook when plugin has a manager."""
    from nitro_dispatch import PluginManager

    class TestPlugin(PluginBase):
        name = "test"

        @hook("test_event")
        def my_hook(self, data):
            data["called"] = True
            return data

    manager = PluginManager()
    manager.register(TestPlugin)
    plugin = manager.load("test")

    # Trigger works before unregister
    result1 = manager.trigger("test_event", {})
    assert result1["called"] is True

    # Unregister the hook
    plugin.unregister_hook("test_event", plugin.my_hook)

    # Should not be called after unregister
    result2 = manager.trigger("test_event", {})
    assert "called" not in result2


def test_trigger_with_manager():
    """Test triggering events from within a plugin."""
    from nitro_dispatch import PluginManager

    class TestPlugin(PluginBase):
        name = "test"

        def do_something(self):
            # Trigger an event from within the plugin
            return self.trigger("internal_event", {"from": "plugin"})

    manager = PluginManager()
    manager.register(TestPlugin)
    plugin = manager.load("test")

    result = plugin.do_something()
    assert result["from"] == "plugin"


def test_collect_hooks_with_property_error():
    """Test _collect_decorated_hooks handles AttributeError gracefully."""

    class TestPlugin(PluginBase):
        name = "test"

        @property
        def broken_property(self):
            raise AttributeError("Cannot access")

    # Should not raise despite the broken property
    plugin = TestPlugin()
    assert plugin.name == "test"
