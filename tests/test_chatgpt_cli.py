import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeLoginManager:
    def __init__(self):
        self.credentials = None
        self.close = AsyncMock()

    async def login(self, presenter, selector):
        presenter("https://auth.openai.com/authorize?safe=1")
        selected = selector(
            [
                SimpleNamespace(slug="gpt-first", display_name="GPT First"),
                SimpleNamespace(slug="gpt-second", display_name="GPT Second"),
            ]
        )
        self.credentials = SimpleNamespace(selected_model=selected)
        return self.credentials


class ChatGPTCLITest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")

    async def test_login_command_prints_ssh_hint_and_selects_model(self):
        manager = FakeLoginManager()
        output = []
        answers = iter(["2"])

        result = await self.main.run_chatgpt_admin(
            "chatgpt-login",
            auth_manager=manager,
            input_fn=lambda _prompt: next(answers),
            output=output.append,
        )

        rendered = "\n".join(output)
        self.assertEqual(0, result)
        self.assertIn("ssh -L 1455:127.0.0.1:1455", rendered)
        self.assertIn("https://auth.openai.com/authorize?safe=1", rendered)
        self.assertIn("gpt-second", rendered)
        manager.close.assert_awaited_once()

    async def test_logout_command_reports_revocation_state(self):
        manager = SimpleNamespace(
            logout=AsyncMock(
                return_value=SimpleNamespace(
                    revocation_confirmed=False,
                    local_credentials_deleted=True,
                )
            ),
            close=AsyncMock(),
        )
        output = []

        result = await self.main.run_chatgpt_admin(
            "chatgpt-logout", auth_manager=manager, output=output.append
        )

        self.assertEqual(0, result)
        self.assertIn("未确认远端撤销", "\n".join(output))
        manager.close.assert_awaited_once()

    async def test_smoke_command_requires_credentials(self):
        from core.chatgpt_auth import ChatGPTAuthError

        manager = SimpleNamespace(credentials=None, close=AsyncMock())
        smoke_runner = AsyncMock()
        with self.assertRaisesRegex(ChatGPTAuthError, "chatgpt-login"):
            await self.main.run_chatgpt_admin(
                "chatgpt-smoke-test",
                auth_manager=manager,
                smoke_runner=smoke_runner,
            )
        smoke_runner.assert_not_awaited()
        manager.close.assert_awaited_once()

    def test_service_start_does_not_run_for_admin_commands(self):
        def consume(coroutine):
            coroutine.close()
            return 0

        with (
            patch.object(self.main, "run_services") as run_services,
            patch.object(self.main.asyncio, "run", side_effect=consume) as async_run,
        ):
            result = self.main.main("chatgpt-login")

        self.assertEqual(0, result)
        async_run.assert_called_once()
        run_services.assert_not_called()
