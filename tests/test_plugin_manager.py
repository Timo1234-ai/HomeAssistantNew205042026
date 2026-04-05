"""Tests for the device plugin manager and built-in plugins."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from home_assistant.devices.plugin_manager import (
    BUILTIN_PLUGINS,
    DevicePlugin,
    PluginManager,
)


class TestDevicePlugin:
    def test_base_plugin_get_state(self):
        p = DevicePlugin("192.168.1.1")
        assert p.get_state() == {}

    def test_base_plugin_execute(self):
        p = DevicePlugin("192.168.1.1")
        result = p.execute("anything", {})
        assert result["ok"] is False

    def test_base_plugin_capabilities(self):
        p = DevicePlugin("192.168.1.1")
        assert p.get_capabilities() == []


class TestPluginManager:
    def test_builtin_plugins_load(self):
        pm = PluginManager()
        # At least some built-in plugins should be loaded
        loaded = list(pm._plugins.keys())
        assert len(loaded) > 0

    def test_get_plugin_class_returns_base_for_unknown(self):
        pm = PluginManager()
        cls = pm.get_plugin_class("nonexistent_xyz")
        assert cls is DevicePlugin or issubclass(cls, DevicePlugin)

    def test_get_instance_returns_plugin(self):
        pm = PluginManager()
        inst = pm.get_instance("generic", "192.168.1.1")
        assert isinstance(inst, DevicePlugin)
        assert inst.device_ip == "192.168.1.1"

    def test_get_instance_cached(self):
        pm = PluginManager()
        inst1 = pm.get_instance("generic", "10.0.0.1")
        inst2 = pm.get_instance("generic", "10.0.0.1")
        assert inst1 is inst2

    def test_list_available_plugins_includes_builtins(self):
        pm = PluginManager()
        plugins = pm.list_available_plugins()
        ids = [p["plugin_id"] for p in plugins]
        assert "generic" in ids
        assert "generic_http" in ids

    def test_fetch_remote_registry_handles_network_error(self):
        pm = PluginManager()
        with patch("requests.get", side_effect=Exception("network error")):
            registry = pm.fetch_remote_registry(force=True)
        assert isinstance(registry, dict)

    def test_find_plugin_class_finds_subclass(self):
        class FakeModule:
            class MyPlugin(DevicePlugin):
                plugin_id = "my_plugin"

        cls = PluginManager._find_plugin_class(FakeModule())
        assert cls is FakeModule.MyPlugin

    def test_find_plugin_class_ignores_base_class(self):
        class FakeModule:
            DevicePlugin = DevicePlugin  # same base class

        cls = PluginManager._find_plugin_class(FakeModule())
        assert cls is None


class TestGenericPlugin:
    def test_get_state(self):
        from home_assistant.devices.plugins.generic import GenericPlugin
        p = GenericPlugin("192.168.1.100")
        state = p.get_state()
        assert state["ip"] == "192.168.1.100"

    def test_ping_command(self):
        from home_assistant.devices.plugins.generic import GenericPlugin
        p = GenericPlugin("127.0.0.1")
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = p.execute("ping", {})
        assert result["ok"] is True
        assert result["reachable"] is True

    def test_unknown_command(self):
        from home_assistant.devices.plugins.generic import GenericPlugin
        p = GenericPlugin("127.0.0.1")
        result = p.execute("fly", {})
        assert result["ok"] is False


class TestGenericHttpPlugin:
    def test_get_state_json(self):
        from home_assistant.devices.plugins.generic_http import GenericHttpPlugin
        p = GenericHttpPlugin("192.168.1.2", port=80)
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"foo": "bar"}
        with patch("requests.get", return_value=mock_resp):
            state = p.get_state()
        assert state["status"] == 200
        assert state["data"] == {"foo": "bar"}

    def test_execute_post(self):
        from home_assistant.devices.plugins.generic_http import GenericHttpPlugin
        p = GenericHttpPlugin("192.168.1.2", port=80)
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"created": True}
        with patch("requests.post", return_value=mock_resp):
            result = p.execute("post", {"path": "/items", "body": {"name": "x"}})
        assert result["ok"] is True

    def test_execute_unknown_command(self):
        from home_assistant.devices.plugins.generic_http import GenericHttpPlugin
        p = GenericHttpPlugin("192.168.1.2")
        result = p.execute("delete", {})
        assert result["ok"] is False


class TestPhilipsHuePlugin:
    def test_get_state(self):
        from home_assistant.devices.plugins.philips_hue import PhilipsHuePlugin
        p = PhilipsHuePlugin("192.168.1.3", api_key="testkey")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"1": {"name": "Hall"}}
        with patch("requests.get", return_value=mock_resp):
            state = p.get_state()
        assert "lights" in state

    def test_set_light(self):
        from home_assistant.devices.plugins.philips_hue import PhilipsHuePlugin
        p = PhilipsHuePlugin("192.168.1.3", api_key="testkey")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = [{"success": {"/lights/1/state/on": True}}]
        with patch("requests.put", return_value=mock_resp):
            result = p.execute("set_light", {"light_id": 1, "on": True})
        assert result["ok"] is True


class TestLifxPlugin:
    def test_turn_on_sends_udp(self):
        from home_assistant.devices.plugins.lifx import LifxPlugin
        p = LifxPlugin("192.168.1.4")
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("socket.socket", return_value=mock_sock):
            result = p.execute("turn_on", {})
        assert result["ok"] is True

    def test_unknown_command(self):
        from home_assistant.devices.plugins.lifx import LifxPlugin
        p = LifxPlugin("192.168.1.4")
        result = p.execute("dance", {})
        assert result["ok"] is False
