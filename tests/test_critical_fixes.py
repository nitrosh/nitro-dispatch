"""Regression tests for the critical issues identified in code review.

Each test pins the fix for a specific issue so the bug can't silently
reappear. Reference IDs (#1-#11) match the review report ordering.
"""

import asyncio
import os
import sys
import threading
import time

import pytest

from nitro_dispatch import PluginBase, PluginManager, hook
from nitro_dispatch.core.exceptions import (
    DependencyError,
    HookTimeoutError,
    PluginLoadError,
)
from nitro_dispatch.core.hook_registry import HookRegistry


# ---------------------------------------------------------------------------
# #1 — Sync timeout is actually enforced (no shutdown-blocks-on-timeout bug)
# ---------------------------------------------------------------------------


def test_sync_timeout_does_not_block_on_runaway_callback():
    """A sync hook that ignores its timeout must NOT pin the caller.

    Previously the per-call ThreadPoolExecutor's __exit__ joined the
    worker, so a runaway callback hung the caller despite the timeout.
    """
    reg = HookRegistry()

    def slow(_data):
        time.sleep(5.0)
        return _data

    reg.register("evt", slow, priority=50, timeout=0.05)
    reg.set_error_strategy("fail_fast")

    start = time.time()
    with pytest.raises(Exception):  # wrapped HookTimeoutError -> HookError
        reg.trigger("evt", {})
    elapsed = time.time() - start

    # Should return within ~timeout, never the full 5s sleep. Generous
    # slack for CI scheduling, but well below the runaway sleep.
    assert elapsed < 1.0, f"trigger blocked for {elapsed:.2f}s; timeout broken"


# ---------------------------------------------------------------------------
# #2 — load() rolls back partial hook registration when on_load() raises
# ---------------------------------------------------------------------------


class _BoomOnLoad(PluginBase):
    name = "boom"

    @hook("some.event")
    def react(self, data):
        return data

    def on_load(self):
        raise RuntimeError("kaboom")


def test_load_failure_does_not_leak_hooks_into_registry():
    mgr = PluginManager()
    mgr.register(_BoomOnLoad)

    with pytest.raises(PluginLoadError):
        mgr.load("boom")

    # The registry must not retain a hook bound to the failed instance.
    matching = mgr._registry.get_hooks("some.event")
    assert matching == [], f"orphan hooks remained: {matching}"

    # And triggering must not invoke the dead hook.
    result = mgr.trigger("some.event", {"x": 1})
    assert result == {"x": 1}


# ---------------------------------------------------------------------------
# #3 — unload() is exception-safe even when on_unload() raises
# ---------------------------------------------------------------------------


class _BoomOnUnload(PluginBase):
    name = "boom_unload"

    @hook("u.event")
    def react(self, data):
        return data

    def on_unload(self):
        raise RuntimeError("unload boom")


def test_unload_cleans_up_even_when_on_unload_raises():
    mgr = PluginManager()
    mgr.register(_BoomOnUnload)
    mgr.load("boom_unload")
    assert mgr.is_loaded("boom_unload")

    with pytest.raises(RuntimeError, match="unload boom"):
        mgr.unload("boom_unload")

    # Must be fully detached despite the on_unload failure.
    assert not mgr.is_loaded("boom_unload")
    assert mgr._registry.get_hooks("u.event") == []
    # Subsequent unload should not crash on stale state — it should
    # report not-loaded cleanly.
    from nitro_dispatch.core.exceptions import PluginNotFoundError

    with pytest.raises(PluginNotFoundError):
        mgr.unload("boom_unload")


# ---------------------------------------------------------------------------
# #4 — Circular dependencies raise immediately, not via RecursionError
# ---------------------------------------------------------------------------


class _PluginA(PluginBase):
    name = "cyc_a"
    dependencies = ["cyc_b"]


class _PluginB(PluginBase):
    name = "cyc_b"
    dependencies = ["cyc_a"]


def test_circular_dependency_raises_dependency_error_not_recursion():
    mgr = PluginManager()
    mgr.register(_PluginA)
    mgr.register(_PluginB)

    with pytest.raises(PluginLoadError) as excinfo:
        mgr.load("cyc_a")

    # The chained cause should be a DependencyError mentioning the cycle.
    chain_messages = []
    err = excinfo.value
    while err is not None:
        chain_messages.append(str(err))
        err = err.__cause__
    joined = " | ".join(chain_messages).lower()
    assert "circular" in joined, f"expected cycle message, got: {joined}"


# ---------------------------------------------------------------------------
# #5 — unload() detaches ALL hooks even when a plugin registered multiple
#       for the same event (mutation-during-iteration regression).
# ---------------------------------------------------------------------------


class _MultiHook(PluginBase):
    name = "multi"

    @hook("multi.event", priority=10)
    def low(self, data):
        return data

    @hook("multi.event", priority=20)
    def mid(self, data):
        return data

    @hook("multi.event", priority=30)
    def high(self, data):
        return data


def test_unload_removes_all_hooks_for_same_event():
    mgr = PluginManager()
    mgr.register(_MultiHook)
    mgr.load("multi")
    assert len(mgr._registry.get_hooks("multi.event")) == 3

    mgr.unload("multi")
    assert mgr._registry.get_hooks("multi.event") == []


# ---------------------------------------------------------------------------
# #6 — reload() refreshes every class in a multi-class module
# ---------------------------------------------------------------------------


def test_reload_refreshes_sibling_classes_in_same_module(tmp_path):
    plugin_file = tmp_path / "twoclass_plugin.py"
    plugin_file.write_text(
        "from nitro_dispatch import PluginBase, hook\n"
        "class First(PluginBase):\n"
        "    name = 'first'\n"
        "    version = '1.0.0'\n"
        "    @hook('e1')\n"
        "    def h(self, d):\n"
        "        return 'first-v1'\n"
        "class Second(PluginBase):\n"
        "    name = 'second'\n"
        "    version = '1.0.0'\n"
        "    @hook('e2')\n"
        "    def h(self, d):\n"
        "        return 'second-v1'\n"
    )

    mgr = PluginManager()
    mgr.discover_plugins(str(tmp_path), pattern="*_plugin.py")
    mgr.load("first")
    mgr.load("second")

    assert mgr.trigger("e1", None) == "first-v1"
    assert mgr.trigger("e2", None) == "second-v1"

    # Rewrite both classes. Bump mtime past fs resolution to be safe.
    plugin_file.write_text(
        "from nitro_dispatch import PluginBase, hook\n"
        "class First(PluginBase):\n"
        "    name = 'first'\n"
        "    version = '2.0.0'\n"
        "    @hook('e1')\n"
        "    def h(self, d):\n"
        "        return 'first-v2'\n"
        "class Second(PluginBase):\n"
        "    name = 'second'\n"
        "    version = '2.0.0'\n"
        "    @hook('e2')\n"
        "    def h(self, d):\n"
        "        return 'second-v2'\n"
    )
    future_mtime = time.time() + 2
    os.utime(plugin_file, (future_mtime, future_mtime))

    mgr.reload("first")

    # Both first AND second must run the new code, even though only
    # 'first' was reloaded explicitly.
    assert mgr.trigger("e1", None) == "first-v2"
    assert (
        mgr.trigger("e2", None) == "second-v2"
    ), "sibling class in the same reloaded module is stale"


# ---------------------------------------------------------------------------
# #7 — discover_plugins() must not clobber sys.modules entries by stem
# ---------------------------------------------------------------------------


def test_discover_does_not_clobber_sys_modules_by_stem(tmp_path):
    # Pick a stem that overlaps with the stdlib — if discover_plugins
    # inserts under that bare key, the real module disappears from
    # sys.modules.
    plugin_file = tmp_path / "logging_plugin.py"
    plugin_file.write_text(
        "from nitro_dispatch import PluginBase\n"
        "class LP(PluginBase):\n"
        "    name = 'lp'\n"
        "    version = '1.0.0'\n"
    )

    original_logging = sys.modules.get("logging")

    mgr = PluginManager()
    mgr.discover_plugins(str(tmp_path), pattern="*_plugin.py")

    # The real `logging` module must still be the one in sys.modules.
    assert sys.modules.get("logging") is original_logging

    # The discovered module must live under the namespaced prefix.
    discovered_keys = [k for k in sys.modules if k.startswith("nitro_dispatch._discovered.")]
    assert any(
        "logging_plugin" in k for k in discovered_keys
    ), f"discovered module not under namespaced prefix; keys: {discovered_keys}"


# ---------------------------------------------------------------------------
# #8 — async on_error coroutines are awaited under trigger_async
# ---------------------------------------------------------------------------


class _AsyncOnErrorPlugin(PluginBase):
    name = "async_err"
    on_error_called_with = None

    @hook("err.event")
    def boom(self, data):
        raise RuntimeError("hook failed")

    async def on_error(self, error):
        # Suspension proves we were truly awaited.
        await asyncio.sleep(0)
        type(self).on_error_called_with = error


@pytest.mark.asyncio
async def test_async_on_error_is_awaited():
    _AsyncOnErrorPlugin.on_error_called_with = None
    mgr = PluginManager()
    mgr.register(_AsyncOnErrorPlugin)
    mgr.load("async_err")

    await mgr.trigger_async("err.event", {})

    assert isinstance(_AsyncOnErrorPlugin.on_error_called_with, RuntimeError)


# ---------------------------------------------------------------------------
# #9 — collect_all errors are programmatically retrievable
# ---------------------------------------------------------------------------


def test_collect_all_errors_are_retrievable():
    reg = HookRegistry()
    reg.set_error_strategy("collect_all")

    def good(data):
        return data + 1

    def bad(_data):
        raise ValueError("nope")

    reg.register("e", good, priority=100)
    reg.register("e", bad, priority=50)

    out = reg.trigger("e", 1)
    assert out == 2  # good ran, bad failed silently in collect_all

    errors = reg.get_last_errors()
    assert len(errors) == 1
    assert errors[0]["event"] == "e"
    assert isinstance(errors[0]["error"], ValueError)


# ---------------------------------------------------------------------------
# #10 — registry is thread-safe across concurrent register/dispatch
# ---------------------------------------------------------------------------


def test_concurrent_register_and_trigger_does_not_raise():
    """Hammer register and trigger from multiple threads.

    Previously _get_matching_hooks iterated self._hooks.items() without
    a lock, so a concurrent register could raise
    'dictionary changed size during iteration'.
    """
    reg = HookRegistry()
    reg.set_error_strategy("log_and_continue")
    stop = threading.Event()
    errors: list = []

    def producer():
        i = 0
        while not stop.is_set():
            try:
                reg.register(f"e.{i % 5}", lambda d: d)
                i += 1
            except Exception as e:  # pragma: no cover - regression guard
                errors.append(e)

    def consumer():
        while not stop.is_set():
            try:
                reg.trigger("e.1", None)
                reg.trigger("e.2", None)
            except Exception as e:  # pragma: no cover - regression guard
                errors.append(e)

    threads = [threading.Thread(target=producer) for _ in range(2)]
    threads += [threading.Thread(target=consumer) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    stop.set()
    for t in threads:
        t.join()

    assert not errors, f"thread-safety regression: {errors[:3]}"


# ---------------------------------------------------------------------------
# #11 — _collect_decorated_hooks does not invoke @property descriptors
# ---------------------------------------------------------------------------


def test_property_descriptors_are_not_invoked_during_init():
    invocations = []

    class HasProperty(PluginBase):
        name = "hp"

        @property
        def expensive(self):
            invocations.append("called")
            return 42

        @hook("hp.event")
        def react(self, data):
            return data

    inst = HasProperty()  # noqa: F841 — instantiation is the test
    assert invocations == [], f"property accessed during __init__: {invocations}"


# ---------------------------------------------------------------------------
# #12 — wildcard `*` requires a non-empty segment
# ---------------------------------------------------------------------------


def test_wildcard_does_not_match_empty_segment():
    reg = HookRegistry()
    hits = []

    def cb(data):
        hits.append(data)
        return data

    reg.register("user.*", cb)
    reg.trigger("user.", "empty-segment")
    reg.trigger("user.login", "good")

    assert hits == ["good"], f"wildcard matched empty segment: {hits}"
