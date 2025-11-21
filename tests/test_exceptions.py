"""
Tests for Nitro Dispatch exceptions.
"""

from nitro_dispatch.core.exceptions import (
    NitroPluginError,
    PluginLoadError,
    PluginRegistrationError,
    HookError,
    PluginNotFoundError,
    DependencyError,
    StopPropagation,
    HookTimeoutError,
    ValidationError,
    PluginDiscoveryError,
)


def test_nitro_plugin_error():
    """Test base NitroPluginError."""
    error = NitroPluginError("Test error")
    assert str(error) == "Test error"
    assert isinstance(error, Exception)


def test_plugin_load_error():
    """Test PluginLoadError."""
    error = PluginLoadError("Failed to load")
    assert str(error) == "Failed to load"
    assert isinstance(error, NitroPluginError)


def test_plugin_registration_error():
    """Test PluginRegistrationError."""
    error = PluginRegistrationError("Failed to register")
    assert str(error) == "Failed to register"
    assert isinstance(error, NitroPluginError)


def test_hook_error():
    """Test HookError."""
    error = HookError("Hook failed")
    assert str(error) == "Hook failed"
    assert isinstance(error, NitroPluginError)


def test_plugin_not_found_error():
    """Test PluginNotFoundError."""
    error = PluginNotFoundError("Plugin not found")
    assert str(error) == "Plugin not found"
    assert isinstance(error, NitroPluginError)


def test_dependency_error():
    """Test DependencyError."""
    error = DependencyError("Dependency missing")
    assert str(error) == "Dependency missing"
    assert isinstance(error, NitroPluginError)


def test_stop_propagation():
    """Test StopPropagation."""
    error = StopPropagation("Stop here")
    assert str(error) == "Stop here"
    assert isinstance(error, NitroPluginError)


def test_hook_timeout_error():
    """Test HookTimeoutError."""
    error = HookTimeoutError("Timeout occurred")
    assert str(error) == "Timeout occurred"
    assert isinstance(error, NitroPluginError)


def test_validation_error():
    """Test ValidationError."""
    error = ValidationError("Validation failed")
    assert str(error) == "Validation failed"
    assert isinstance(error, NitroPluginError)


def test_plugin_discovery_error():
    """Test PluginDiscoveryError."""
    error = PluginDiscoveryError("Discovery failed")
    assert str(error) == "Discovery failed"
    assert isinstance(error, NitroPluginError)


def test_exception_inheritance():
    """Test that all exceptions inherit from NitroPluginError."""
    exceptions = [
        PluginLoadError,
        PluginRegistrationError,
        HookError,
        PluginNotFoundError,
        DependencyError,
        StopPropagation,
        HookTimeoutError,
        ValidationError,
        PluginDiscoveryError,
    ]

    for exc_class in exceptions:
        assert issubclass(exc_class, NitroPluginError)
        assert issubclass(exc_class, Exception)
