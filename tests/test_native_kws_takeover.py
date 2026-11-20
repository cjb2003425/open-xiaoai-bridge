import asyncio
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_config_module():
    spec = importlib.util.spec_from_file_location(
        "native_kws_test_config", ROOT / "config.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_isolated_xiaoai():
    np_stub = types.SimpleNamespace(int16=object(), float32=object())

    class ConfigManagerStub:
        @classmethod
        def instance(cls):
            return cls()

        def get_app_config(self, _path=None, default=None):
            return default

    isolated = ("core.xiaoai", "core.wakeup_session")
    saved = {name: sys.modules.get(name) for name in isolated}
    for name in isolated:
        sys.modules.pop(name, None)
    try:
        with mock.patch.dict(
            sys.modules,
            {
                "numpy": np_stub,
                "open_xiaoai_server": types.SimpleNamespace(),
                "core.utils.config": types.SimpleNamespace(
                    ConfigManager=ConfigManagerStub
                ),
            },
        ):
            module = importlib.import_module("core.xiaoai")
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


class NativeKwsConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config_module()

    def test_bridge_keywords_are_disabled(self):
        self.assertEqual([], self.config.APP_CONFIG["wakeup"]["keywords"])

    def test_native_wake_routes_to_openai_without_playing_ack(self):
        speaker = mock.AsyncMock()
        route = asyncio.run(
            self.config.before_wakeup(
                speaker, "小爱同学", "native_kws", object()
            )
        )
        self.assertEqual("openai", route)
        speaker.play.assert_not_awaited()


class NativeKwsEventTest(unittest.TestCase):
    def setUp(self):
        self.module = load_isolated_xiaoai()
        self.module.XiaoAI._native_wake_dialog_ids.clear()
        self.module.XiaoAI._native_wake_dialog_set.clear()
        self.module.XiaoAI._suppressed_dialog_ids.clear()

    def event(self, **overrides):
        data = {
            "dialog_id": "dialog-native-1",
            "local_suppressed": True,
            "ack_played": True,
        }
        data.update(overrides)
        return json.dumps(
            {"event": "native_kws", "data": data}, ensure_ascii=False
        )

    def test_valid_event_dispatches_once(self):
        manager = types.SimpleNamespace(wakeup=mock.AsyncMock())
        with mock.patch.object(self.module, "EventManager", manager):
            asyncio.run(self.module.XiaoAI.on_event(self.event()))
            asyncio.run(self.module.XiaoAI.on_event(self.event()))
        manager.wakeup.assert_awaited_once_with("小爱同学", "native_kws")

    def test_failed_local_suppression_is_retried(self):
        manager = types.SimpleNamespace(wakeup=mock.AsyncMock())
        suppress = mock.AsyncMock()
        with (
            mock.patch.object(self.module, "EventManager", manager),
            mock.patch.object(
                self.module.XiaoAI, "_suppress_dialog", new=suppress
            ),
        ):
            asyncio.run(
                self.module.XiaoAI.on_event(
                    self.event(local_suppressed=False)
                )
            )
        suppress.assert_awaited_once_with(
            "dialog-native-1", "音箱端原生唤醒压制失败"
        )

    def test_invalid_payload_is_ignored(self):
        manager = types.SimpleNamespace(wakeup=mock.AsyncMock())
        with mock.patch.object(self.module, "EventManager", manager):
            asyncio.run(
                self.module.XiaoAI.on_event(self.event(ack_played="yes"))
            )
        manager.wakeup.assert_not_awaited()

    def test_raw_empty_marker_does_not_interrupt_structured_native_wake(self):
        manager = types.SimpleNamespace(
            wakeup=mock.AsyncMock(),
            on_interrupt=mock.Mock(),
            consume_xiaoai_asr_result=mock.Mock(return_value=False),
        )
        line = {
            "header": {
                "namespace": "SpeechRecognizer",
                "name": "RecognizeResult",
                "dialog_id": "dialog-native-2",
            },
            "payload": {
                "results": [{"text": ""}],
                "is_final": False,
                "is_vad_begin": False,
            },
        }
        event = json.dumps(
            {
                "event": "instruction",
                "data": {"NewLine": json.dumps(line)},
            }
        )
        with mock.patch.object(self.module, "EventManager", manager):
            asyncio.run(self.module.XiaoAI.on_event(event))
        manager.on_interrupt.assert_not_called()


class NativeKwsConversationTest(unittest.TestCase):
    def test_initial_notify_can_be_skipped(self):
        from core.external_conversation import ExternalConversationController

        class Controller(ExternalConversationController):
            MANAGER = object()

        controller = Controller(initial_notify=False)
        controller.active = False
        controller._stop_recording = mock.AsyncMock()
        controller._play_notify = mock.AsyncMock()
        controller._start_recording = mock.AsyncMock()

        asyncio.run(controller._conversation_loop())

        controller._stop_recording.assert_not_awaited()
        controller._play_notify.assert_not_awaited()
        controller._start_recording.assert_not_awaited()

    def test_initial_notify_preserves_recording_handoff_order(self):
        from core.external_conversation import ExternalConversationController

        class Controller(ExternalConversationController):
            MANAGER = object()

        calls = []
        controller = Controller(initial_notify=True)
        controller.active = False
        controller._stop_recording = mock.AsyncMock(
            side_effect=lambda: calls.append("stop")
        )
        controller._play_notify = mock.AsyncMock(
            side_effect=lambda: calls.append("notify")
        )
        controller._start_recording = mock.AsyncMock(
            side_effect=lambda: calls.append("start")
        )

        asyncio.run(controller._conversation_loop())

        self.assertEqual(["stop", "notify", "start"], calls)


if __name__ == "__main__":
    unittest.main()
