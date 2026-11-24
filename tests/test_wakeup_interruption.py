import asyncio
from unittest import mock

from core.wakeup_session import WakeupSessionManager


def test_reset_waits_for_old_conversation_cleanup_before_handoff():
    async def scenario():
        manager = WakeupSessionManager()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def old_conversation():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        task = asyncio.create_task(old_conversation())
        await started.wait()
        controller = mock.Mock()
        controller.is_active.return_value = True
        manager._openai_controller = controller
        manager._openai_task = task
        manager._stop_device_playback = mock.AsyncMock()
        with (
            mock.patch("core.xiaoai.XiaoAI.stop_conversation"),
            mock.patch("core.ref.get_xiaozhi", return_value=None),
        ):
            await manager.reset_all_sessions()
        assert task.done()
        assert cleaned.is_set()
        controller.stop.assert_called_once()
        manager._stop_device_playback.assert_awaited_once()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_old_session_cleanup_does_not_clear_new_session():
    async def scenario():
        manager = WakeupSessionManager()
        old_started = asyncio.Event()
        new_started = asyncio.Event()
        release_old = asyncio.Event()
        release_new = asyncio.Event()
        controllers = []

        class Controller:
            def __init__(self, **_kwargs):
                self.number = len(controllers)
                controllers.append(self)

            async def start(self):
                if self.number == 0:
                    old_started.set()
                    await release_old.wait()
                else:
                    new_started.set()
                    await release_new.wait()

        with (
            mock.patch("core.openai_conversation.OpenAIConversationController", Controller),
            mock.patch("core.wakeup_session.get_kws", return_value=None),
        ):
            old = asyncio.create_task(manager._start_openai_conversation())
            await old_started.wait()
            new = asyncio.create_task(manager._start_openai_conversation())
            await new_started.wait()
            new_task = manager._openai_task
            release_old.set()
            await old
            assert manager._openai_controller is controllers[1]
            assert manager._openai_task is new_task
            release_new.set()
            await new

    asyncio.run(scenario())
