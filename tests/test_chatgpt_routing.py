import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest import mock

from core.wakeup_session import WakeupSessionManager


class ChatGPTRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_chatgpt_route_starts_dedicated_controller_with_fresh_history(self):
        manager = WakeupSessionManager()

        async def route(*_args):
            return "chatgpt"

        manager.config = SimpleNamespace(
            get_app_config=lambda path, default=None: (
                route if path == "wakeup.before_wakeup" else default
            )
        )
        manager.reset_all_sessions = mock.AsyncMock()
        manager._start_chatgpt_conversation = mock.AsyncMock()
        with (
            mock.patch("core.wakeup_session.get_kws", return_value=None),
            mock.patch("core.wakeup_session.get_speaker", return_value=None),
        ):
            await manager.wakeup("小爱同学", "native_kws")
        manager._start_chatgpt_conversation.assert_awaited_once_with(
            initial_notify=False
        )

    def test_chatgpt_controller_uses_chatgpt_config_and_source(self):
        from core.chatgpt_conversation import ChatGPTConversationController

        self.assertEqual("chatgpt", ChatGPTConversationController.CONFIG_PREFIX)
        self.assertEqual("chatgpt", ChatGPTConversationController.WAKEUP_SOURCE)
        self.assertEqual("ChatGPT", ChatGPTConversationController.BACKEND_NAME)

    async def test_native_asr_dispatch_includes_chatgpt_controller(self):
        manager = WakeupSessionManager()
        controller = mock.Mock()
        controller.is_active.return_value = True
        controller.consume_xiaoai_recognize_result.return_value = True
        manager._chatgpt_controller = controller

        consumed = manager.consume_xiaoai_asr_result(
            dialog_id="dialog", text="你好", is_final=True, is_vad_begin=False
        )
        self.assertTrue(consumed)
        controller.consume_xiaoai_recognize_result.assert_called_once()

    async def test_interrupt_cancels_chatgpt_without_touching_openai_state(self):
        manager = WakeupSessionManager()
        controller = mock.Mock()
        controller.is_active.return_value = True
        manager._chatgpt_controller = controller
        task = asyncio.create_task(asyncio.Event().wait())
        manager._chatgpt_task = task
        manager._get_loop = mock.Mock(return_value=asyncio.get_running_loop())
        manager._stop_device_playback = mock.AsyncMock()
        openai_history = [{"role": "user", "content": "keep"}]
        from core.openai import OpenAIManager

        OpenAIManager._sessions = {"session": openai_history.copy()}
        with (
            mock.patch("core.wakeup_session.get_xiaozhi", return_value=None),
            mock.patch("core.xiaoai.XiaoAI.stop_conversation"),
            mock.patch("core.wakeup_session.asyncio.run_coroutine_threadsafe") as submit,
        ):
            submit.side_effect = lambda coroutine, _loop: (coroutine.close(), mock.Mock())[1]
            manager.on_interrupt()
            await asyncio.sleep(0)
            await asyncio.gather(task, return_exceptions=True)
        controller.stop.assert_called_once()
        self.assertTrue(task.cancelled())
        self.assertEqual(openai_history, OpenAIManager._sessions["session"])

    async def test_reset_waits_for_openai_and_chatgpt_cleanup_independently(self):
        manager = WakeupSessionManager()
        cleaned = []

        async def old(name):
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.append(name)

        manager._openai_task = asyncio.create_task(old("openai"))
        manager._chatgpt_task = asyncio.create_task(old("chatgpt"))
        await asyncio.sleep(0)
        manager._openai_controller = mock.Mock()
        manager._chatgpt_controller = mock.Mock()
        manager._openai_controller.is_active.return_value = True
        manager._chatgpt_controller.is_active.return_value = True
        manager._stop_device_playback = mock.AsyncMock()
        with (
            mock.patch("core.xiaoai.XiaoAI.stop_conversation"),
            mock.patch("core.ref.get_xiaozhi", return_value=None),
        ):
            await manager.reset_all_sessions()
        self.assertCountEqual(["openai", "chatgpt"], cleaned)
        manager._openai_controller.stop.assert_called_once()
        manager._chatgpt_controller.stop.assert_called_once()

    def test_main_app_exposes_independent_chatgpt_flag(self):
        from core.app import MainApp

        signature = inspect.signature(MainApp.__init__)
        self.assertIn("enable_openai", signature.parameters)
        self.assertIn("enable_chatgpt", signature.parameters)

    def test_openai_and_chatgpt_lifecycle_are_independent(self):
        from core.app import MainApp

        class FakeThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        submitted = []

        def submit(coroutine, _loop):
            submitted.append(coroutine)
            coroutine.close()
            return mock.Mock()

        MainApp._instance = None
        app = MainApp(
            enable_xiaozhi=False,
            enable_openai=True,
            enable_chatgpt=True,
        )
        app.config = mock.Mock()
        app.config.get_app_config.side_effect = lambda path, default=None: (
            "xiaoai_asr" if path in ("openai.input_mode", "chatgpt.input_mode") else default
        )
        try:
            with (
                mock.patch("core.app.audio_service_flags", return_value=(False, False)),
                mock.patch("core.app.threading.Thread", FakeThread),
                mock.patch("core.app.time.sleep"),
                mock.patch("core.app.asyncio.run_coroutine_threadsafe", side_effect=submit),
                mock.patch("core.app.OpenAIManager.initialize_from_config") as openai_init,
                mock.patch("core.app.OpenAIManager.connect", new=mock.AsyncMock()),
                mock.patch("core.app.ChatGPTManager.initialize_from_config") as chatgpt_init,
                mock.patch("core.app.ChatGPTManager.connect", new=mock.AsyncMock()),
            ):
                app.run()
            openai_init.assert_called_once()
            chatgpt_init.assert_called_once()

            with (
                mock.patch("core.app.asyncio.run_coroutine_threadsafe", side_effect=submit),
                mock.patch("core.app.OpenClawManager.is_connected", return_value=False),
                mock.patch("core.app.QwenPawManager.is_enabled", return_value=False),
                mock.patch("core.app.OpenAIManager.is_enabled", return_value=True),
                mock.patch("core.app.ChatGPTManager.is_enabled", return_value=True),
                mock.patch("core.app.OpenAIManager.close", new=mock.AsyncMock()) as openai_close,
                mock.patch("core.app.ChatGPTManager.close", new=mock.AsyncMock()) as chatgpt_close,
            ):
                app.shutdown()
            openai_close.assert_called_once()
            chatgpt_close.assert_called_once()
        finally:
            MainApp._instance = None
            app.loop.close()
