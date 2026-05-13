from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Any


def get_root() -> str:
    return str(Path(__file__).resolve().parent.parent)


available_setting: dict[str, Any] = {
    "debug": False,
    "channel_type": ["weixin"],
    "web_console": False,
    "web_port": 9899,
    "web_password": "",
    "web_session_expire_days": 30,
    "plugin_trigger_prefix": "$",
    "model": "glm-5",
    "bot_type": "",
    "temperature": 0.9,
    "top_p": 1,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "request_timeout": 180,
    "open_ai_api_key": "",
    "open_ai_api_base": "",
    "deepseek_api_key": "",
    "deepseek_api_base": "",
    "dashscope_api_key": "",
    "zhipu_ai_api_key": "",
    "moonshot_api_key": "",
    "ark_api_key": "",
    "agent": True,
    "agent_workspace": "",  # filled in load_config()
    "agent_max_context_tokens": 50000,
    "agent_max_context_turns": 20,
    "agent_max_steps": 20,
    "enable_thinking": False,
    "knowledge": True,
    "conversation_persistence": True,
    "clear_memory_commands": ["#清除记忆"],
    "single_chat_prefix": ["bot", "@bot"],
    "subscribe_msg": "",
    "appdata_dir": "",
    "miniopenclaw_api_base": "http://127.0.0.1:8002",
}


class Config(dict):
    def __init__(self, payload: dict[str, Any] | None = None):
        super().__init__()
        self.user_datas: dict[str, dict[str, Any]] = {}
        if payload:
            for key, value in payload.items():
                self[key] = value

    def get_user_data(self, user: str) -> dict[str, Any]:
        if user not in self.user_datas:
            self.user_datas[user] = {}
        return self.user_datas[user]

    def load_user_datas(self) -> None:
        path = Path(get_appdata_dir()) / "user_datas.pkl"
        if not path.exists():
            return
        try:
            self.user_datas = pickle.loads(path.read_bytes())
        except Exception:
            self.user_datas = {}

    def save_user_datas(self) -> None:
        path = Path(get_appdata_dir()) / "user_datas.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self.user_datas))


config = Config()
plugin_config: dict[str, Any] = {}
global_config: dict[str, Any] = {"admin_users": []}


def _project_root() -> Path:
    return Path(get_root())


def _default_agent_workspace() -> str:
    return str((_project_root() / "data").resolve())


def _channel_config_path() -> Path:
    return _project_root() / "config.json"


def conf() -> Config:
    return config


def load_config() -> None:
    global config
    path = _channel_config_path()
    defaults = dict(available_setting)
    defaults["agent_workspace"] = _default_agent_workspace()

    payload: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    merged = dict(defaults)
    merged.update(payload)

    for env_key, value in os.environ.items():
        key = env_key.lower()
        if key in merged:
            merged[key] = value

    config = Config(merged)
    config.load_user_datas()
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def get_appdata_dir() -> str:
    configured = conf().get("appdata_dir", "")
    if configured:
        path = (Path(get_root()) / configured).resolve()
    else:
        path = (Path(get_root()) / "data" / "storage" / "channel_appdata").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def subscribe_msg() -> str:
    trigger_prefix = conf().get("single_chat_prefix", [""])[0]
    msg = conf().get("subscribe_msg", "")
    return str(msg).format(trigger_prefix=trigger_prefix)


def write_plugin_config(pconf: dict[str, Any]) -> None:
    for key, value in pconf.items():
        plugin_config[key.lower()] = value


def remove_plugin_config(name: str) -> None:
    plugin_config.pop(name.lower(), None)


def pconf(plugin_name: str) -> dict[str, Any] | None:
    return plugin_config.get(plugin_name.lower())
