# Nitro Dispatch

A powerful, framework-agnostic plugin system for Python with advanced features like async/await support, hook priorities, timeouts, event namespacing, and plugin discovery.

## Requirements

Python `3.8` or higher is required.

## Installation

```bash
pip install nitro-dispatch
```

## Features

### Core Features
- **Simple API** - Easy to learn with minimal boilerplate
- **Framework Agnostic** - Works with any Python application
- **Hook System** - Register callbacks for custom events with `@hook` decorator
- **Data Filtering** - Each hook can modify and transform data
- **Error Isolation** - Plugin errors don't crash your application
- **Dependency Management** - Automatic dependency resolution
- **Zero Dependencies** - No external dependencies required

### Advanced Features
- **Async/Await Support** - Native async hook execution
- **Hook Priorities** - Control execution order (higher priority = runs first)
- **Timeout Protection** - Prevent slow plugins from blocking
- **Event Namespacing** - Organize events hierarchically (`user.login`, `db.save`)
- **Wildcard Events** - Listen to multiple events (`user.*`, `db.before_*`)
- **Plugin Discovery** - Auto-discover plugins from directories
- **Hot Reloading** - Reload plugins without restarting
- **Stop Propagation** - Halt an event chain from within hooks
- **Hook Tracing** - Debug with detailed execution timing
- **Built-in Lifecycle Events** - Hook into plugin lifecycle
- **Metadata Validation** - Ensure plugin quality

## Quick Start

```python
from nitro_dispatch import PluginManager, PluginBase, hook

class WelcomePlugin(PluginBase):
    name = "welcome"

    @hook('user.login', priority=100)
    def greet_user(self, data):
        print(f"Welcome, {data['username']}!")
        data['greeted'] = True
        return data

manager = PluginManager()
manager.register(WelcomePlugin)
manager.load_all()

result = manager.trigger('user.login', {'username': 'Alice'})
# Output: Welcome, Alice!
```

## Core Concepts

### 1. Plugins

Plugins inherit from `PluginBase`:

```python
class MyPlugin(PluginBase):
    name = "my_plugin"              # Required: unique identifier
    version = "1.0.0"               # Plugin version
    description = "Does cool stuff" # Human-readable description
    author = "Your Name"            # Plugin author
    dependencies = []               # List of required plugin names
```

### 2. Hooks

Register callbacks for events using the `@hook` decorator:

```python
class ValidationPlugin(PluginBase):
    name = "validator"

    @hook('before_save', priority=100, timeout=5.0)
    def validate(self, data):
        if not data.get('email'):
            raise ValueError("Email required")
        return data
```

Or register manually in `on_load()`:

```python
class LoggingPlugin(PluginBase):
    name = "logger"

    def on_load(self):
        self.register_hook('before_save', self.log_data, priority=50)

    def log_data(self, data):
        print(f"Saving: {data}")
        return data
```

### 3. Data Filtering

Hooks execute in priority order (highest first). Each hook receives data, modifies it, and returns it:

```python
# Hook 1 (priority=100)
@hook('process_data', priority=100)
def add_timestamp(self, data):
    data['timestamp'] = datetime.now()
    return data

# Hook 2 (priority=50)
@hook('process_data', priority=50)
def add_id(self, data):
    data['id'] = generate_id()
    return data

# Data flows: original → add_timestamp → add_id → final result
```

## Advanced Usage

### Hook Priorities

Control execution order with priority values (higher = earlier):

```python
class SecurityPlugin(PluginBase):
    @hook('user.login', priority=100)  # Runs first
    def security_check(self, data):
        return data

class LoggingPlugin(PluginBase):
    @hook('user.login', priority=10)  # Runs last
    def log_login(self, data):
        return data
```

### Async/Await Support

Native support for async hooks:

```python
class AsyncPlugin(PluginBase):
    @hook('data.fetch')
    async def fetch_from_api(self, data):
        result = await aiohttp.get('https://api.example.com')
        data['result'] = await result.json()
        return data

# Trigger async
result = await manager.trigger_async('data.fetch', {})
```

### Hook Timeouts

Prevent slow plugins from blocking:

```python
@hook('process_data', timeout=2.0)  # 2 second timeout
def slow_process(self, data):
    # If this takes > 2s, HookTimeoutError is raised
    time.sleep(5)  # This will timeout!
    return data
```

### Event Namespacing with Wildcards

Organize events hierarchically and use wildcards:

```python
class AuditPlugin(PluginBase):
    @hook('user.*')  # Matches user.login, user.logout, etc.
    def audit_user_events(self, data):
        log.info(f"User event: {data}")
        return data

    @hook('db.before_*')  # Matches db.before_save, db.before_delete
    def audit_db_operations(self, data):
        log.info(f"DB operation: {data}")
        return data

# Trigger events
manager.trigger('user.login', {})      # Caught by user.*
manager.trigger('db.before_save', {})  # Caught by db.before_*
```

### Stop Propagation

Stop the hook chain from within a hook:

```python
from nitro_dispatch import StopPropagation

class ValidationPlugin(PluginBase):
    @hook('process_data', priority=100)
    def validate(self, data):
        if not data.get('valid'):
            raise StopPropagation("Invalid data")
        return data

# Hooks with lower priority won't execute if validation fails
```

### Plugin Discovery

Auto-discover and load plugins from directories:

```python
manager = PluginManager()

# Discover plugins from directory
discovered = manager.discover_plugins(
    '~/.myapp/plugins',
    pattern='*_plugin.py',
    recursive=True
)

print(f"Discovered: {discovered}")
manager.load_all()
```

### Hot Reloading

Reload plugins without restarting:

```python
# Reload a specific plugin
manager.reload('my_plugin')

# The plugin will be unloaded, module reloaded, and loaded again
```

### Enable/Disable Plugins

Toggle plugins at runtime:

```python
# Disable a plugin (hooks won't execute)
manager.disable_plugin('optional_plugin')

# Re-enable it
manager.enable_plugin('optional_plugin')
```

### Built-in Lifecycle Events

Hook into the plugin system's lifecycle:

```python
def on_plugin_loaded(data):
    print(f"Plugin loaded: {data['plugin_name']}")
    return data

manager.register_hook(
    PluginManager.EVENT_PLUGIN_LOADED,
    on_plugin_loaded
)

# Built-in events:
# - nitro.plugin.registered
# - nitro.plugin.loaded
# - nitro.plugin.unloaded
# - nitro.plugin.error
# - nitro.app.startup
# - nitro.app.shutdown
```

### Hook Tracing/Debugging

Enable detailed logging for debugging:

```python
manager = PluginManager(log_level='DEBUG')
manager.enable_hook_tracing(True)

# Now all hook executions are logged with timing info
result = manager.trigger('user.login', {})
```

### Error Handling Strategies

Configure how errors are handled:

```python
# Log and continue (default)
manager.set_error_strategy('log_and_continue')

# Stop on first error
manager.set_error_strategy('fail_fast')

# Collect all errors
manager.set_error_strategy('collect_all')
```

### Plugin Configuration

Pass configuration to plugins:

```python
config = {
    'cache': {
        'max_size': 100,
        'ttl': 3600
    }
}
manager = PluginManager(config=config)

class CachePlugin(PluginBase):
    name = "cache"

    def on_load(self):
        max_size = self.get_config('max_size', 50)
        ttl = self.get_config('ttl', 1800)
```

## API Reference

### PluginManager

| Method                                                      | Description                     |
|-------------------------------------------------------------|---------------------------------|
| `__init__(config, log_level, validate_metadata)`            | Initialize manager              |
| `register(plugin_class)`                                    | Register a plugin class         |
| `unregister(plugin_name)`                                   | Unregister and unload a plugin  |
| `load(plugin_name)`                                         | Load a specific plugin          |
| `load_all()`                                                | Load all registered plugins     |
| `unload(plugin_name)`                                       | Unload a plugin                 |
| `unload_all()`                                              | Unload all plugins              |
| `reload(plugin_name)`                                       | Hot reload a plugin             |
| `discover_plugins(directory, pattern, recursive)`           | Auto-discover plugins           |
| `register_hook(event, callback, plugin, priority, timeout)` | Register a hook manually        |
| `unregister_hook(event, callback, plugin)`                  | Unregister a hook               |
| `trigger(event, data)`                                      | Trigger event (sync)            |
| `trigger_async(event, data)`                                | Trigger event (async)           |
| `get_plugin(name)`                                          | Get a loaded plugin by name     |
| `get_all_plugins()`                                         | Get all loaded plugins          |
| `get_registered_plugins()`                                  | Get names of registered plugins |
| `get_loaded_plugins()`                                      | Get names of loaded plugins     |
| `is_loaded(plugin_name)`                                    | Check if a plugin is loaded     |
| `get_events()`                                              | Get all registered event names  |
| `enable_plugin(name)`                                       | Enable a plugin                 |
| `disable_plugin(name)`                                      | Disable a plugin                |
| `enable_hook_tracing(enabled)`                              | Enable debugging                |
| `set_error_strategy(strategy)`                              | Set error handling              |

### PluginBase

| Attribute/Method                                    | Description                      |
|-----------------------------------------------------|----------------------------------|
| `name`                                              | Plugin name (required)           |
| `version`                                           | Plugin version                   |
| `description`                                       | Plugin description               |
| `author`                                            | Plugin author                    |
| `dependencies`                                      | List of required plugins         |
| `enabled`                                           | Whether the plugin is enabled    |
| `on_load()`                                         | Called when plugin loads         |
| `on_unload()`                                       | Called when plugin unloads       |
| `on_error(error)`                                   | Called on hook errors            |
| `register_hook(event, callback, priority, timeout)` | Register a hook                  |
| `unregister_hook(event, callback)`                  | Unregister a hook                |
| `trigger(event, data)`                              | Trigger an event from the plugin |
| `get_config(key, default)`                          | Get configuration value          |

### @hook Decorator

```python
@hook(event_name, priority=50, timeout=None, async_hook=False)
```

| Parameter    | Description                                        |
|--------------|----------------------------------------------------|
| `event_name` | Event to listen for (supports wildcards)           |
| `priority`   | Execution priority (higher = earlier). Default: 50 |
| `timeout`    | Max execution time in seconds. Default: None       |
| `async_hook` | Whether hook is async (auto-detected)              |

### Exceptions

All exceptions inherit from `NitroPluginError`:

| Exception               | Description                                  |
|-------------------------|----------------------------------------------|
| `NitroPluginError`      | Base exception for all Nitro Plugin errors   |
| `PluginLoadError`       | Raised when a plugin fails to load           |
| `PluginRegistrationError` | Raised when plugin registration fails      |
| `PluginNotFoundError`   | Raised when a requested plugin is not found  |
| `PluginDiscoveryError`  | Raised when plugin discovery fails           |
| `DependencyError`       | Raised when plugin dependencies cannot be resolved |
| `HookError`             | Raised when hook execution fails             |
| `HookTimeoutError`      | Raised when a hook exceeds its timeout       |
| `ValidationError`       | Raised when plugin metadata validation fails |
| `StopPropagation`       | Raised to stop hook propagation in the event chain |

```python
from nitro_dispatch import (
    NitroPluginError,
    PluginLoadError,
    HookTimeoutError,
    StopPropagation,
)

try:
    manager.load('my_plugin')
except PluginLoadError as e:
    print(f"Failed to load plugin: {e}")
```

## Examples

### Basic Usage
```bash
python examples/basic_usage.py
```

### Advanced Features
```bash
python examples/advanced_usage.py
python examples/advanced_features.py
```

### Plugin Discovery
```bash
python examples/discovery_example.py
```

## Development

### Setup
```bash
git clone https://github.com/nitro/nitro-dispatch.git
cd nitro-dispatch
pip install -e ".[dev]"
```

### Run Tests
```bash
pytest
pytest --cov=nitro_dispatch
```

### Format Code
```bash
black nitro_dispatch tests examples
```

## License

Please see [LICENSE](LICENSE) for licensing details.

## Author

[github.com/sn](https://github.com/sn)