"""Regression tests for the second review pass.

Covers timeout starvation and exit blocking, stale collected errors,
failed hot reloads, on_load rollback, logging hygiene, async detection,
context propagation, tie ordering, cycle reporting, and dotted plugin
file names.
"""

import contextvars
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
import warnings

import pytest

from nitro_dispatch import PluginBase, PluginManager, hook
from nitro_dispatch.core.exceptions import HookTimeoutError, PluginLoadError
from nitro_dispatch.core.hook_registry import HookRegistry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_timed_hooks_still_run_after_many_timeouts():
    reg = HookRegistry()
    release = threading.Event()

    for i in range(12):
        reg.register(f"hang{i}", lambda d: release.wait(10), timeout=0.02)
        reg.trigger(f"hang{i}")

    ran = []
    reg.register("quick", lambda d: ran.append(True), timeout=1.0)
    try:
        reg.trigger("quick")
    finally:
        release.set()

    assert ran == [True]


def test_timed_sync_hook_still_raises_its_own_errors():
    reg = HookRegistry()
    reg.set_error_strategy("fail_fast")

    def boom(_data):
        raise KeyError("nope")

    reg.register("evt", boom, timeout=1.0)
    with pytest.raises(Exception) as excinfo:
        reg.trigger("evt")
    assert isinstance(excinfo.value.__cause__, KeyError)


def _run_script(source):
    started = time.monotonic()
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPO_ROOT,
        check=True,
        timeout=30,
    )
    return time.monotonic() - started


def test_hung_sync_hook_does_not_block_process_exit():
    elapsed = _run_script(
        """
        import time
        from nitro_dispatch import HookRegistry
        reg = HookRegistry()
        reg.register("hang", lambda d: time.sleep(20), timeout=0.05)
        reg.trigger("hang")
    """
    )
    assert elapsed < 10


def test_hung_sync_hook_does_not_block_asyncio_run_exit():
    elapsed = _run_script(
        """
        import asyncio, time
        from nitro_dispatch import HookRegistry
        reg = HookRegistry()
        reg.register("hang", lambda d: time.sleep(20), timeout=0.05)
        asyncio.run(reg.trigger_async("hang"))
    """
    )
    assert elapsed < 10


def test_last_errors_cleared_by_event_with_no_hooks():
    reg = HookRegistry()
    reg.set_error_strategy("collect_all")
    reg.register("bad", lambda d: 1 / 0)

    reg.trigger("bad")
    assert len(reg.get_last_errors()) == 1

    reg.trigger("nothing_here")
    assert reg.get_last_errors() == []


async def test_last_errors_cleared_by_async_event_with_no_hooks():
    reg = HookRegistry()
    reg.set_error_strategy("collect_all")
    reg.register("bad", lambda d: 1 / 0)

    await reg.trigger_async("bad")
    await reg.trigger_async("nothing_here")
    assert reg.get_last_errors() == []


def test_failed_reload_restores_previously_loaded_plugins(tmp_path):
    plugin_file = tmp_path / "pair_plugin.py"
    plugin_file.write_text(
        textwrap.dedent(
            """
        from nitro_dispatch import PluginBase, hook

        class First(PluginBase):
            name = "first"

            @hook("ping")
            def ping(self, data):
                return "first"

        class Second(PluginBase):
            name = "second"
    """
        )
    )

    mgr = PluginManager()
    mgr.discover_plugins(tmp_path)
    mgr.load_all()

    with plugin_file.open("a") as fh:
        fh.write("\nthis is not python\n")
    stat = plugin_file.stat()
    os.utime(plugin_file, (stat.st_atime + 5, stat.st_mtime + 5))

    with pytest.raises(PluginLoadError):
        mgr.reload("first")

    assert sorted(mgr.get_loaded_plugins()) == ["first", "second"]
    assert mgr.trigger("ping") == "first"


def test_failed_reload_does_not_load_a_plugin_that_was_not_loaded(tmp_path):
    plugin_file = tmp_path / "lonely_plugin.py"
    plugin_file.write_text(
        "from nitro_dispatch import PluginBase\n\nclass Lonely(PluginBase):\n    name = 'lonely'\n"
    )

    mgr = PluginManager()
    mgr.discover_plugins(tmp_path)

    plugin_file.write_text("this is not python\n")
    stat = plugin_file.stat()
    os.utime(plugin_file, (stat.st_atime + 5, stat.st_mtime + 5))

    with pytest.raises(PluginLoadError):
        mgr.reload("lonely")
    assert mgr.get_loaded_plugins() == []


def test_hooks_registered_in_on_load_are_rolled_back_on_failure():
    class Boom(PluginBase):
        name = "boom"

        @hook("decorated")
        def decorated(self, data):
            return data

        def on_load(self):
            self.register_hook("manual", lambda d: "orphan")
            raise RuntimeError("nope")

    mgr = PluginManager()
    mgr.register(Boom)
    with pytest.raises(PluginLoadError):
        mgr.load("boom")

    assert mgr._registry.get_hooks("manual") == []
    assert mgr._registry.get_hooks("decorated") == []


def test_unregister_plugin_removes_only_that_plugins_hooks():
    reg = HookRegistry()

    class Owner:
        name = "owner"

    mine, theirs = Owner(), Owner()
    reg.register("a", lambda d: d, plugin=mine)
    reg.register("user.*", lambda d: d, plugin=mine)
    reg.register("a", lambda d: d, plugin=theirs)

    assert reg.unregister_plugin(mine) == 2
    assert [h["plugin"] for h in reg.get_hooks("a")] == [theirs]
    assert reg.get_hooks("user.login") == []


@pytest.fixture
def package_logger():
    logger = logging.getLogger("nitro_dispatch")
    level = logger.level
    yield logger
    logger.setLevel(level)


def test_manager_does_not_configure_root_logging(package_logger):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    PluginManager()
    PluginManager(log_level="DEBUG")

    assert root.handlers == handlers
    assert root.level == level


def test_log_level_applies_to_package_logger(package_logger):
    PluginManager(log_level="warning")
    assert package_logger.level == logging.WARNING


def test_invalid_log_level_raises_value_error(package_logger):
    with pytest.raises(ValueError):
        PluginManager(log_level="LOUD")


def test_package_logger_has_null_handler():
    handlers = logging.getLogger("nitro_dispatch").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)


def test_registering_hooks_emits_no_deprecation_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)

        class Plugin(PluginBase):
            name = "p"

            @hook("evt")
            async def handler(self, data):
                return data

        reg = HookRegistry()
        reg.register("evt", lambda d: d)
        reg.register("evt", Plugin().handler)


async def test_object_with_async_call_is_awaited():
    class AsyncCallable:
        async def __call__(self, data):
            return data + 1

    reg = HookRegistry()
    reg.register("evt", AsyncCallable())

    assert reg.get_hooks("evt")[0]["is_async"] is True
    assert await reg.trigger_async("evt", 1) == 2


request_id = contextvars.ContextVar("request_id", default=None)


def test_timed_sync_hook_sees_caller_context():
    reg = HookRegistry()
    reg.register("evt", lambda d: request_id.get(), timeout=1.0)

    token = request_id.set("abc")
    try:
        assert reg.trigger("evt") == "abc"
    finally:
        request_id.reset(token)


@pytest.mark.parametrize("timeout", [None, 1.0])
async def test_sync_hook_in_trigger_async_sees_caller_context(timeout):
    reg = HookRegistry()
    reg.register("evt", lambda d: request_id.get(), timeout=timeout)

    token = request_id.set("xyz")
    try:
        assert await reg.trigger_async("evt") == "xyz"
    finally:
        request_id.reset(token)


async def test_timed_sync_hook_timeout_in_trigger_async():
    reg = HookRegistry()
    reg.set_error_strategy("collect_all")
    release = threading.Event()
    reg.register("evt", lambda d: release.wait(10), timeout=0.05)
    try:
        await reg.trigger_async("evt")
    finally:
        release.set()

    [error] = reg.get_last_errors()
    assert isinstance(error["error"], HookTimeoutError)


def test_equal_priority_runs_in_registration_order_across_wildcards():
    reg = HookRegistry()
    order = []
    reg.register("user.login", lambda d: order.append("a"))
    reg.register("user.*", lambda d: order.append("b"))
    reg.register("user.login", lambda d: order.append("c"))

    reg.trigger("user.login")
    assert order == ["a", "b", "c"]


def test_circular_dependency_message_shows_the_actual_cycle():
    class Zed(PluginBase):
        name = "zed"
        dependencies = ["alpha"]

    class Alpha(PluginBase):
        name = "alpha"
        dependencies = ["mid"]

    class Mid(PluginBase):
        name = "mid"
        dependencies = ["alpha"]

    mgr = PluginManager()
    for cls in (Zed, Alpha, Mid):
        mgr.register(cls)

    with pytest.raises(PluginLoadError) as excinfo:
        mgr.load("zed")

    message = str(excinfo.value)
    assert "alpha -> mid -> alpha" in message
    assert "zed ->" not in message


def test_plugin_file_with_dots_in_its_name_can_be_reloaded(tmp_path):
    plugin_file = tmp_path / "v1.2_plugin.py"
    plugin_file.write_text(
        textwrap.dedent(
            """
        from nitro_dispatch import PluginBase, hook

        class Dotted(PluginBase):
            name = "dotted"

            @hook("ping")
            def ping(self, data):
                return "old"
    """
        )
    )

    mgr = PluginManager()
    assert mgr.discover_plugins(tmp_path) == ["dotted"]
    mgr.load("dotted")

    plugin_file.write_text(plugin_file.read_text().replace('"old"', '"new"'))
    stat = plugin_file.stat()
    os.utime(plugin_file, (stat.st_atime + 5, stat.st_mtime + 5))

    mgr.reload("dotted")
    assert mgr.trigger("ping") == "new"


def test_manager_exposes_last_errors():
    mgr = PluginManager()
    mgr.set_error_strategy("collect_all")
    mgr.register_hook("bad", lambda d: 1 / 0)

    mgr.trigger("bad")
    [error] = mgr.get_last_errors()
    assert error["event"] == "bad"
    assert isinstance(error["error"], ZeroDivisionError)
