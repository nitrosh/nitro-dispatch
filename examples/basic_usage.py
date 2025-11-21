"""
Basic usage example for Nitro Dispatch.

This example demonstrates:
1. Importing the plugin system
2. Creating simple plugins
3. Registering and loading plugins
4. Triggering events with data filtering
"""

from nitro_dispatch import PluginManager, PluginBase, hook


# Define a simple plugin using the @hook decorator
class WelcomePlugin(PluginBase):
    name = "welcome"
    version = "1.0.0"

    @hook("user_login")
    def greet_user(self, data):
        """Add a welcome message to user login data."""
        print(f"Welcome, {data.get('username', 'Guest')}!")
        data["greeted"] = True
        return data


# Define another plugin using manual hook registration
class AuditPlugin(PluginBase):
    name = "audit"
    version = "1.0.0"

    def on_load(self):
        """Register hooks when plugin loads."""
        self.register_hook("user_login", self.log_login)
        self.register_hook("user_logout", self.log_logout)

    def log_login(self, data):
        """Log user login."""
        username = data.get("username", "Unknown")
        print(f"[AUDIT] User '{username}' logged in")
        data["audit_logged"] = True
        return data

    def log_logout(self, data):
        """Log user logout."""
        username = data.get("username", "Unknown")
        print(f"[AUDIT] User '{username}' logged out")
        return data


def main():
    """Main function demonstrating basic usage."""
    print("=" * 50)
    print("Nitro Dispatch - Basic Usage Example")
    print("=" * 50)

    # Create plugin manager
    manager = PluginManager()

    # Register plugins
    print("\n1. Registering plugins...")
    manager.register(WelcomePlugin)
    manager.register(AuditPlugin)
    print(f"   Registered: {manager.get_registered_plugins()}")

    # Load all plugins
    print("\n2. Loading plugins...")
    loaded = manager.load_all()
    print(f"   Loaded: {loaded}")

    # Trigger user_login event
    print("\n3. Triggering 'user_login' event...")
    login_data = {"username": "john_doe", "timestamp": "2025-01-01T10:00:00"}
    result = manager.trigger("user_login", login_data)
    print(f"   Result: {result}")

    # Trigger user_logout event
    print("\n4. Triggering 'user_logout' event...")
    logout_data = {"username": "john_doe", "timestamp": "2025-01-01T11:00:00"}
    result = manager.trigger("user_logout", logout_data)
    print(f"   Result: {result}")

    # Get plugin information
    print("\n5. Plugin information...")
    for plugin_name in manager.get_loaded_plugins():
        plugin = manager.get_plugin(plugin_name)
        print(f"   {plugin}")

    # Unload all plugins
    print("\n6. Unloading plugins...")
    manager.unload_all()
    print("   All plugins unloaded")

    print("\n" + "=" * 50)
    print("Example completed!")
    print("=" * 50)


if __name__ == "__main__":
    main()
