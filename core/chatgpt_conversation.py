"""ChatGPT SIWC continuous conversation controller."""

from core.chatgpt import ChatGPTManager
from core.external_conversation import ExternalConversationController


class ChatGPTConversationController(ExternalConversationController):
    CONFIG_PREFIX = "chatgpt"
    BACKEND_NAME = "ChatGPT"
    LOG_MODULE = "ChatGPT Conv"
    WAKEUP_SOURCE = "chatgpt"
    MANAGER = ChatGPTManager
