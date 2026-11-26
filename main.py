import argparse
import asyncio
import os
import signal
import sys
import time

# Fix: Add onnxruntime library path for sherpa_onnx on macOS/Linux
# This ensures sherpa_onnx can find libonnxruntime at runtime
if sys.platform in ("darwin", "linux"):
    try:
        import onnxruntime as ort
        ort_lib_dir = os.path.join(os.path.dirname(ort.__file__), "capi")
        if os.path.exists(ort_lib_dir):
            if sys.platform == "darwin":
                os.environ.setdefault("DYLD_LIBRARY_PATH", "")
                if ort_lib_dir not in os.environ["DYLD_LIBRARY_PATH"]:
                    os.environ["DYLD_LIBRARY_PATH"] = ort_lib_dir + ":" + os.environ["DYLD_LIBRARY_PATH"]
            else:  # linux
                os.environ.setdefault("LD_LIBRARY_PATH", "")
                if ort_lib_dir not in os.environ["LD_LIBRARY_PATH"]:
                    os.environ["LD_LIBRARY_PATH"] = ort_lib_dir + ":" + os.environ["LD_LIBRARY_PATH"]
    except ImportError:
        pass

from core.utils.config_loader import ensure_config_module_loaded

config_path = ensure_config_module_loaded()

from core.app import MainApp
from core.utils.logger import logger


main_app_instance = None

# 启动配置（从环境变量读取）
connect_xiaozhi = False  # 是否连接小智 AI
enable_api_server = False  # 是否开启 API Server


ADMIN_COMMANDS = ("chatgpt-login", "chatgpt-logout", "chatgpt-smoke-test")


def setup_config(argv=None):
    """解析命令行参数和环境变量"""
    global connect_xiaozhi, enable_api_server, enable_xiaozhi, enable_openclaw, enable_openai, enable_chatgpt, enable_qwenpaw

    parser = argparse.ArgumentParser(description="小爱音箱接入 Open XiaoAI")
    parser.add_argument("command", nargs="?", choices=ADMIN_COMMANDS)
    args = parser.parse_args(argv)

    # 从环境变量读取配置
    enable_api_server = os.environ.get("API_SERVER_ENABLE", "").lower() in ("1", "true", "yes")
    enable_xiaozhi = os.environ.get("XIAOZHI_ENABLE", "").lower() in ("1", "true", "yes")
    # 兼容 OPENCLAW_ENABLE (新) 和 OPENCLAW_ENABLED (旧)
    openclaw_env = os.environ.get("OPENCLAW_ENABLE") or os.environ.get("OPENCLAW_ENABLED") or ""
    enable_openclaw = openclaw_env.lower() in ("1", "true", "yes")
    enable_openai = os.environ.get("OPENAI_ENABLE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    enable_chatgpt = os.environ.get("CHATGPT_ENABLE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    enable_qwenpaw = os.environ.get("QWENPAW_ENABLE", "").lower() in (
        "1",
        "true",
        "yes",
    )

    # 计算 AUDIO_INPUT_ENABLE 实际生效的值（默认 1/true）
    audio_input_enabled = os.environ.get("AUDIO_INPUT_ENABLE", "1").strip().lower() in ("1", "true", "yes", "on")
    
    logger.info(f"[Main] ENV: XIAOZHI_ENABLE={os.environ.get('XIAOZHI_ENABLE') or 'not set (disabled)'}, "
                f"API_SERVER_ENABLE={os.environ.get('API_SERVER_ENABLE') or 'not set (disabled)'}, "
                f"OPENCLAW_ENABLE={os.environ.get('OPENCLAW_ENABLE') or os.environ.get('OPENCLAW_ENABLED') or 'not set (disabled)'}, "
                f"OPENAI_ENABLE={os.environ.get('OPENAI_ENABLE') or 'not set (disabled)'}, "
                f"CHATGPT_ENABLE={os.environ.get('CHATGPT_ENABLE') or 'not set (disabled)'}, "
                f"QWENPAW_ENABLE={os.environ.get('QWENPAW_ENABLE') or 'not set (disabled)'}, "
                f"AUDIO_INPUT_ENABLE={1 if audio_input_enabled else 0}")
    logger.info(f"[Main] Using config file: {config_path}")

    # 打印模块启用情况
    logger.info("[Main] 模块启用情况:")
    logger.info("小爱指令拦截器启用", module="Main")
    logger.info(
        f"小智 AI Bridge: {'启用' if enable_xiaozhi else '禁用'}",
        module="Main",
    )
    logger.info(
        f"OpenClaw Bridge: {'启用' if enable_openclaw else '禁用'}",
        module="Main",
    )
    logger.info(
        f"OpenAI: {'启用' if enable_openai else '禁用'}",
        module="Main",
    )
    logger.info(
        f"ChatGPT: {'启用' if enable_chatgpt else '禁用'}",
        module="Main",
    )
    logger.info(
        f"QwenPaw: {'启用' if enable_qwenpaw else '禁用'}",
        module="Main",
    )
    return args.command


def _build_chatgpt_auth_manager():
    from core.chatgpt_auth import ChatGPTAuthConfig, ChatGPTAuthManager
    from core.utils.config import ConfigManager

    mapping = ConfigManager.instance().get_app_config("chatgpt", {})
    return ChatGPTAuthManager(ChatGPTAuthConfig.from_mapping(mapping))


async def _run_chatgpt_smoke_test():
    from core.chatgpt import ChatGPTManager

    ChatGPTManager.initialize_from_config(enabled=True)
    try:
        if not await ChatGPTManager.connect():
            return None
        return await ChatGPTManager.send("请只回答：连接成功", wait_response=True)
    finally:
        await ChatGPTManager.close()


async def run_chatgpt_admin(
    command,
    *,
    auth_manager=None,
    input_fn=input,
    output=print,
    smoke_runner=None,
):
    """Run one SIWC maintenance command without starting audio services."""
    from core.chatgpt_auth import ChatGPTAuthError

    manager = auth_manager or _build_chatgpt_auth_manager()
    smoke_runner = smoke_runner or _run_chatgpt_smoke_test
    try:
        if command == "chatgpt-login":
            port = manager.config.callback_port if hasattr(manager, "config") else 1455
            output(
                "远程服务器请先建立端口转发：\n"
                f"ssh -L {port}:127.0.0.1:{port} user@bridge-server"
            )

            def present(url):
                output(f"请在浏览器打开：\n{url}")

            def select(models):
                output("可用模型：")
                for index, model in enumerate(models, start=1):
                    output(f"  {index}. {model.display_name} ({model.slug})")
                while True:
                    answer = input_fn("请选择模型编号: ").strip()
                    try:
                        selected = models[int(answer) - 1]
                    except (ValueError, IndexError):
                        output("编号无效，请重试。")
                        continue
                    return selected.slug

            credentials = await manager.login(present, select)
            output(f"ChatGPT 登录成功，模型：{credentials.selected_model}")
            return 0

        if command == "chatgpt-logout":
            result = await manager.logout()
            if result.revocation_confirmed:
                output("ChatGPT 已注销，远端会话撤销已确认。")
            else:
                output("ChatGPT 本地凭据已删除，但未确认远端撤销。")
            return 0

        if command == "chatgpt-smoke-test":
            if manager.credentials is None:
                raise ChatGPTAuthError("请先运行 chatgpt-login")
            response = await smoke_runner()
            if not response:
                raise ChatGPTAuthError("ChatGPT smoke test did not complete")
            output(f"ChatGPT 连接测试成功：{response}")
            return 0

        raise ChatGPTAuthError(f"Unknown ChatGPT command: {command}")
    finally:
        await manager.close()
    logger.info(
        f"API Server: {'启用' if enable_api_server else '禁用'}",
        module="Main",
    )


def run_services(xiaozhi_mode: bool = False):
    """统一的服务启动入口

    Args:
        xiaozhi_mode: 是否启动小智 AI 完整服务（包括 VAD/KWS/GUI）
    """
    global main_app_instance, enable_api_server, enable_openclaw, enable_openai, enable_qwenpaw

    # 统一使用 MainApp 管理所有服务
    main_app_instance = MainApp.instance(
        enable_xiaozhi=xiaozhi_mode,
        enable_openclaw=enable_openclaw,
        enable_openai=enable_openai,
        enable_qwenpaw=enable_qwenpaw,
    )
    main_app_instance.run(enable_api_server=enable_api_server)

    # 主线程保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main(command=None):
    global enable_xiaozhi
    if command in ADMIN_COMMANDS:
        return asyncio.run(run_chatgpt_admin(command))
    run_services(xiaozhi_mode=enable_xiaozhi)
    return 0


def setup_graceful_shutdown():
    def signal_handler(_sig, _frame):
        global main_app_instance

        # 关闭 MainApp（包含 API Server）
        if main_app_instance:
            main_app_instance.shutdown()

        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    selected_command = setup_config()
    if selected_command is None:
        setup_graceful_shutdown()
    sys.exit(main(selected_command))
