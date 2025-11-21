"""
Sample plugins demonstrating Nitro Dispatch functionality.
"""

from nitro_dispatch import PluginBase, hook


class ValidationPlugin(PluginBase):
    """
    Example plugin that validates data before saving.
    """

    name = "validation_plugin"
    version = "1.0.0"
    description = "Validates data before save operations"
    author = "Nitro Team"

    @hook("before_save")
    def validate_data(self, data):
        """Validate data and add validation flag."""
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")

        data["validated"] = True
        data["validator"] = self.name
        print(f"[{self.name}] Data validated")
        return data


class LoggingPlugin(PluginBase):
    """
    Example plugin that logs all operations.
    """

    name = "logging_plugin"
    version = "1.0.0"
    description = "Logs all hook events"
    author = "Nitro Team"

    def on_load(self):
        """Register hooks when plugin loads."""
        self.register_hook("before_save", self.log_before_save)
        self.register_hook("after_save", self.log_after_save)
        print(f"[{self.name}] Plugin loaded")

    def on_unload(self):
        """Cleanup when plugin unloads."""
        print(f"[{self.name}] Plugin unloaded")

    def log_before_save(self, data):
        """Log before save event."""
        print(f"[{self.name}] Before save: {data}")
        return data

    def log_after_save(self, data):
        """Log after save event."""
        print(f"[{self.name}] After save: {data}")
        return data


class TransformPlugin(PluginBase):
    """
    Example plugin that transforms data.
    """

    name = "transform_plugin"
    version = "1.0.0"
    description = "Transforms data by adding metadata"
    author = "Nitro Team"

    @hook("before_save")
    def add_metadata(self, data):
        """Add metadata to the data."""
        import datetime

        if not isinstance(data, dict):
            return data

        data["metadata"] = {
            "timestamp": datetime.datetime.now().isoformat(),
            "processed_by": self.name,
        }
        print(f"[{self.name}] Added metadata")
        return data


class DependentPlugin(PluginBase):
    """
    Example plugin that depends on another plugin.
    """

    name = "dependent_plugin"
    version = "1.0.0"
    description = "Demonstrates plugin dependencies"
    author = "Nitro Team"
    dependencies = ["logging_plugin"]  # This plugin requires logging_plugin

    @hook("before_save")
    def process_data(self, data):
        """Process data after dependencies are loaded."""
        print(f"[{self.name}] Processing data (after dependencies)")
        if isinstance(data, dict):
            data["processed"] = True
        return data
