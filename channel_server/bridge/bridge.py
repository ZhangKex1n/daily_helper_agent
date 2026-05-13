from models.bot_factory import create_bot
from bridge.context import Context
from bridge.reply import Reply, ReplyType
from common import const
from common.log import logger
from common.singleton import singleton
from config import conf, get_appdata_dir
import json
import os
import threading

import requests


@singleton
class Bridge(object):
    def __init__(self):
        self.btype = {
            "chat": const.OPENAI,
            "voice_to_text": conf().get("voice_to_text", "openai"),
            "text_to_voice": conf().get("text_to_voice", "google"),
            "translate": conf().get("translate", "baidu"),
        }
        # 这边取配置的模型
        bot_type = conf().get("bot_type")
        if bot_type:
            self.btype["chat"] = bot_type
        else:
            model_type = conf().get("model") or const.GPT_41_MINI
            
            # Ensure model_type is string to prevent AttributeError when using startswith()
            # This handles cases where numeric model names (e.g., "1") are parsed as integers from YAML
            if not isinstance(model_type, str):
                logger.warning(f"[Bridge] model_type is not a string: {model_type} (type: {type(model_type).__name__}), converting to string")
                model_type = str(model_type)
            
            if model_type in ["text-davinci-003"]:
                self.btype["chat"] = const.OPEN_AI
            if conf().get("use_azure_chatgpt", False):
                self.btype["chat"] = const.CHATGPTONAZURE
            if model_type in ["wenxin", "wenxin-4"]:
                self.btype["chat"] = const.BAIDU
            if model_type in ["xunfei"]:
                self.btype["chat"] = const.XUNFEI
            if model_type in [const.QWEN, const.QWEN_TURBO, const.QWEN_PLUS, const.QWEN_MAX]:
                self.btype["chat"] = const.QWEN_DASHSCOPE
            if model_type and (model_type.startswith("qwen") or model_type.startswith("qwq") or model_type.startswith("qvq")):
                self.btype["chat"] = const.QWEN_DASHSCOPE
            if model_type and model_type.startswith("gemini"):
                self.btype["chat"] = const.GEMINI
            if model_type and model_type.startswith("claude"):
                self.btype["chat"] = const.CLAUDEAPI

            if model_type in [const.MOONSHOT, "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]:
                self.btype["chat"] = const.MOONSHOT
            if model_type and model_type.startswith("kimi"):
                self.btype["chat"] = const.MOONSHOT

            if model_type and model_type.startswith("doubao"):
                self.btype["chat"] = const.DOUBAO

            if model_type and model_type.startswith("deepseek"):
                self.btype["chat"] = const.DEEPSEEK

            if model_type in [const.MODELSCOPE]:
                self.btype["chat"] = const.MODELSCOPE
            
            # MiniMax models
            if model_type and (model_type in ["abab6.5-chat", "abab6.5"] or model_type.lower().startswith("minimax")):
                self.btype["chat"] = const.MiniMax

            if conf().get("use_linkai") and conf().get("linkai_api_key"):
                self.btype["chat"] = const.LINKAI
                if not conf().get("voice_to_text") or conf().get("voice_to_text") in ["openai"]:
                    self.btype["voice_to_text"] = const.LINKAI
                if not conf().get("text_to_voice") or conf().get("text_to_voice") in ["openai", const.TTS_1, const.TTS_1_HD]:
                    self.btype["text_to_voice"] = const.LINKAI

        self.bots = {}
        self.chat_bots = {}
        self._agent_bridge = None
        self._session_map_lock = threading.Lock()
        self._session_map_path = os.path.join(get_appdata_dir(), "channel_session_map.json")
        self._session_map_cache: dict[str, str] | None = None

    # 模型对应的接口
    def get_bot(self, typename):
        if self.bots.get(typename) is None:
            logger.info("create bot {} for {}".format(self.btype[typename], typename))
            if typename == "text_to_voice":
                from voice.factory import create_voice

                self.bots[typename] = create_voice(self.btype[typename])
            elif typename == "voice_to_text":
                from voice.factory import create_voice

                self.bots[typename] = create_voice(self.btype[typename])
            elif typename == "chat":
                self.bots[typename] = create_bot(self.btype[typename])
            elif typename == "translate":
                from translate.factory import create_translator

                self.bots[typename] = create_translator(self.btype[typename])
        return self.bots[typename]

    def get_bot_type(self, typename):
        return self.btype[typename]

    def fetch_reply_content(self, query, context: Context) -> Reply:
        return self.get_bot("chat").reply(query, context)

    def fetch_voice_to_text(self, voiceFile) -> Reply:
        return self.get_bot("voice_to_text").voiceToText(voiceFile)

    def fetch_text_to_voice(self, text) -> Reply:
        return self.get_bot("text_to_voice").textToVoice(text)

    def fetch_translate(self, text, from_lang="", to_lang="en") -> Reply:
        return self.get_bot("translate").translate(text, from_lang, to_lang)

    def find_chat_bot(self, bot_type: str):
        if self.chat_bots.get(bot_type) is None:
            self.chat_bots[bot_type] = create_bot(bot_type)
        return self.chat_bots.get(bot_type)

    def reset_bot(self):
        """
        重置bot路由
        """
        self.__init__()

    def get_agent_bridge(self):
        return None

    def _load_session_map(self) -> dict[str, str]:
        if self._session_map_cache is not None:
            return self._session_map_cache
        if not os.path.exists(self._session_map_path):
            self._session_map_cache = {}
            return self._session_map_cache
        try:
            with open(self._session_map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._session_map_cache = {str(k): str(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning("[Bridge] Failed to load session map, fallback empty: %s", exc)
            self._session_map_cache = {}
        return self._session_map_cache

    def _save_session_map(self, session_map: dict[str, str]) -> None:
        try:
            os.makedirs(os.path.dirname(self._session_map_path), exist_ok=True)
            with open(self._session_map_path, "w", encoding="utf-8") as f:
                json.dump(session_map, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[Bridge] Failed to save session map: %s", exc)

    @staticmethod
    def _build_user_key(context: Context | None) -> str:
        if context is None:
            return "default:default"
        channel_type = str(context.get("channel_type") or "unknown")
        user_id = (
            context.get("session_id")
            or context.get("from_user_id")
            or context.get("receiver")
            or "default"
        )
        return f"{channel_type}:{user_id}"

    def _resolve_session_id(self, api_base: str, context: Context | None) -> str:
        user_key = self._build_user_key(context)
        with self._session_map_lock:
            session_map = self._load_session_map()
            existing = session_map.get(user_key)
            if existing:
                return existing

            create_url = f"{api_base}/api/sessions"
            try:
                resp = requests.post(
                    create_url,
                    json={"title": f"channel:{user_key}"},
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json() if resp.content else {}
                session_id = str(payload.get("id", "")).strip()
                if not session_id:
                    raise RuntimeError("empty session id from /api/sessions")
            except Exception as exc:
                logger.warning(
                    "[Bridge] create session failed for user_key=%s, fallback deterministic key: %s",
                    user_key,
                    exc,
                )
                session_id = user_key

            session_map[user_key] = session_id
            self._save_session_map(session_map)
            return session_id

    def fetch_agent_reply(self, query: str, context: Context = None,
                          on_event=None, clear_history: bool = False) -> Reply:
        # Route channel-side "agent mode" to miniOpenClaw api_server,
        # so all memory logic stays in shared memory_module_v2.
        api_base = str(conf().get("miniopenclaw_api_base", "http://127.0.0.1:8002")).rstrip("/")
        chat_url = f"{api_base}/api/chat"

        session_id = self._resolve_session_id(api_base, context)
        channel_type = ""
        if context:
            channel_type = str(context.get("channel_type") or "")

        try:
            resp = requests.post(
                chat_url,
                json={
                    "message": query,
                    "session_id": str(session_id),
                    "stream": True,
                    "channel_type": channel_type,
                },
                stream=True,
                timeout=300,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[Bridge] api_server /api/chat request failed: %s", exc)
            return Reply(ReplyType.ERROR, f"Agent API request failed: {exc}")

        current_event = ""
        token_parts: list[str] = []
        done_content = ""

        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("event: "):
                current_event = raw[len("event: "):].strip()
                continue
            if not raw.startswith("data: "):
                continue

            try:
                payload = json.loads(raw[len("data: "):])
            except Exception:
                payload = {}

            if current_event == "token":
                token_parts.append(str(payload.get("content", "")))
            elif current_event == "done":
                done_content = str(payload.get("content", "") or "")
            elif current_event == "error":
                message = str(payload.get("error", "unknown error"))
                return Reply(ReplyType.ERROR, message)

        content = done_content.strip() or "".join(token_parts).strip()
        if not content:
            content = "Empty response from agent."
        return Reply(ReplyType.TEXT, content)
