# Vibe Coding Agent - 配置管理
# Copyright (c) 2026 xingluosama

import os
import json
import base64
from pathlib import Path
from typing import Any, Dict, Optional

import win32crypt
import keyring

from agent_shared import is_loopback_url

KEYRING_SERVICE = "vibe_agent"
KEYRING_USER = "api_key"


# ★ 不再硬编码「已知模型」白名单 —— 项目可接入任意 OpenAI 兼容服务
# （deepseek / chatgpt / qwen / 本地 ollama 等），模型列表由前端通过
# /models 端点动态拉取。此处仅保留一个历史常量用于兼容，不再用于强制校验。
VALID_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}


class ConfigManager:

    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self.config_path = os.path.join(app_dir, "config.json")
        self.key_path = os.path.join(app_dir, "base.env")
        Path(app_dir).mkdir(parents=True, exist_ok=True)
        self.defaults = {
            "language": "zh_CN",
            "model": "deepseek-v4-pro",
            "use_responses_api": False,
            "encryption_method": "keyring",  # ★ 默认使用 keyring（Windows 凭据管理器），比 win32crypt+base64 文件更安全
            "api_base": "https://api.deepseek.com",
            "project_root": os.path.join(os.path.expanduser("~"), "vibe_workspace"),
            "queue_max_size": 2000,
            "max_steps": 128,
            "enable_web_search": False,
            "confirm_write_delete": True,
            "temperature": 1.0,
            "think_level": "高",
            "max_tokens": 32767,
            "task_timeout": 0,

            # ★ API 请求单次最大等待时间（秒）：超过此时长仍未收到任何 chunk，
            # 视为网络挂起并打断本次请求。可由用户在 30s ~ 3600s（60 分钟）之间选择，
            # 默认 180s（3 分钟）。
            "api_request_timeout": 180,

            "memory": False,
            "memory_mode": "full",
            "max_rounds": 10,

            # Plugin system
            "plugins_enabled": True,
            "plugin_dirs": [],

            # Plugin security
            # ★ P0-2 修复：README 曾宣称默认 block / strict，但实际默认为 warn / off，
            #   导致「默认只警告不拦截、默认不限制导入」。现已统一为 block / strict，
            #   与文档一致，默认即拦截危险插件、默认限制导入。
            "plugin_security_audit": "block",
            "plugin_security_import_restrict": "strict",
            "plugin_security_require_permissions": True,   # ★ 默认开启：插件必须声明权限
            "plugin_security_resource_limit": False,

            # ★ P0-1 进程级隔离：插件严禁直接挂载到主进程。
            #   "process"（默认）= 在独立子进程加载并执行插件；
            #   "inprocess" = 旧行为（仅限开发调试，不推荐）。
            "plugin_isolation": "process",

            # ★ P0-5 签名/来源校验：默认开启，可在设置里关闭。
            "plugin_signature_verify": True,
            # 用户自定义信任公钥（Ed25519 hex），追加到内置官方公钥之后。
            "plugin_trusted_keys": [],

            # ★ P0-4 SSRF 防护：插件网络策略四粒度。
            #   deny（默认）/ audited_public / public_only / allow_all
            "plugin_network_policy": "deny",
            "plugin_network_url_allowlist": [],
            "plugin_network_domain_allowlist": [],

            # ★ P0-8 人工审批进入安全层（职责拆分）：
            #   原生工具确认（设置面板）：写入/替换、删除、命令执行三级，默认开启。
            "native_confirm_enabled": True,
            "native_confirm_write": True,
            "native_confirm_delete": True,
            "native_confirm_exec": True,
            #   插件工具调用审批（插件控制面板）：开启后所有插件工具调用均需人工确认。
            "approval_enabled": True,

            # 全量读取大文件开关（默认关闭）
            # 关闭时，read_file 在不指定行范围的情况下读取 >100KB 文件将返回
            # "文件过大，仅能部分读取" 并拒绝全量返回
            "allow_full_read_large_files": False,

            # 异步架构：沙箱池 & 文件IO队列
            "sandbox_pool_max": 8,
            "sandbox_network_enabled": False,
            "file_io_queue_enabled": True,
            "lifecycle_zombie_scan_seconds": 5,
            "resource_terminal_reserved_pct": 40,

            # 自定义系统提示词
            "custom_system_prompt_enabled": False,
            "custom_system_prompt": "",
            "custom_system_prompt_file": "",

            # 越狱/提示词注入防护
            "jailbreak_guard_enabled": True,    # ★ 默认开启越狱检测
            "jailbreak_guard_action": "block",  # "block" = 拦截恶意输入；"warn" = 仅日志警告

            # NORP 安全系统
            "norp_safe_enabled": True,  # ★ 默认开启安全系统（危险命令/UAC/路径越界拦截）

            # 关闭按钮行为：minimize_to_tray（最小化到任务栏托盘）/ exit（直接退出程序）
            "close_button_behavior": "minimize_to_tray",

            # 视觉 API（开放接口）：开发者自行接入多模态视觉模型，
            # 对图片 / 视频流做视觉理解。vision_enabled 开启后，
            # 上传的图片/视频文件会交给视觉层处理（本地回调或外部服务）。
            "vision_enabled": False,
            # 外部视觉服务地址（POST JSON，见 vision.py 的协议约定）。
            # 留空表示仅使用本地注册的回调（register_vision_handler）。
            "vision_service_url": "",

            # 视觉 API（内置 provider，优先级高于本地回调与外部服务）：
            #   vision_provider 取值：openai_compatible | anthropic | llama_cpp
            #   空字符串 = 不启用内置 provider，走旧的「本地回调 / 外部服务」逻辑。
            "vision_provider": "",
            "vision_model": "",          # 视觉模型名（如 gpt-4o / qwen-vl-max / claude-3-5-sonnet）
            "vision_api_key": "",        # 视觉服务 API key（云端 provider；TODO: 接入 keyring）
            "vision_base_url": "",       # 视觉服务 base URL（本地 llama.cpp / Ollama / vLLM 等）
            "vision_max_tokens": 1024,   # 视觉描述最大输出 token
            "vision_temperature": 0.2,   # 视觉描述温度（偏低 = 更确定）
            "vision_timeout": 120,       # 视觉请求超时（秒）
            "vision_prompt": "",         # 默认视觉指令 prompt（空 = 用内置默认「请详细描述图片内容」）

            # 视觉/操作外挂安全参数（SafetyArbiter 裁决器，见 vision_safety.py）：
            # 风险分级由工具名固定（vision_click 等 L2 工具需用户审批弹窗显式确认），
            # 以下参数控制熔断 / 在场检测 / delegate 让渡的阈值。
            "vision_idle_timeout_sec": 150,   # 用户空闲多久判定「离开」（默认 150s）
            "vision_idle_allow_operate": False,  # 空闲后是否允许 Agent 继续操作（默认锁死）
            "vision_delegate_window_sec": 300,   # delegate 让渡（预授权）时长
            "vision_delegate_scope": "window",   # 让渡范围：window | app | session
            "vision_delegate_max_risk": "L2",    # 让渡最多覆盖到哪一级（L3 永不免确认）
            "vision_cooldown_sec": 60,           # 熔断冷却时长（冷却期内不可复位）
            "vision_max_failures": 3,            # 同一操作连续失败 N 次 → 停手上报
            "vision_use_framesource": False,     # 动作-验证-收敛用 capture_worker 驻留模式（高频循环更快；默认冷启动更稳）
        }

    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in self.defaults.items():
                cfg.setdefault(k, v)
            self._sanitize(cfg)
            return cfg
        return self.defaults.copy()

    def _sanitize(self, cfg: Dict[str, Any]):
        """修复被污染 / 为空的配置项，回退到默认值。"""
        model = cfg.get("model", "")
        # ★ 放开模型硬校验：任何非空、长度 >=2 的模型名都保留，
        #   以便接入 deepseek / chatgpt / qwen / 本地 ollama 等任意服务。
        if not model or model.strip() == "." or model.strip() == "":
            cfg["model"] = self.defaults["model"]
        else:
            stripped = model.strip()
            if len(stripped) < 2:
                cfg["model"] = self.defaults["model"]
            else:
                cfg["model"] = stripped

        api_base = cfg.get("api_base", "")
        if not api_base or not api_base.strip():
            cfg["api_base"] = self.defaults["api_base"]

        timeout = cfg.get("task_timeout", 0)
        if not isinstance(timeout, (int, float)) or timeout < 0:
            cfg["task_timeout"] = 0

        # API 请求超时：强制收敛到 30 ~ 3600 秒（60 分钟）区间，默认 180 秒
        api_timeout = cfg.get("api_request_timeout", 180)
        try:
            api_timeout = int(api_timeout)
        except (TypeError, ValueError):
            api_timeout = 180
        cfg["api_request_timeout"] = max(30, min(3600, api_timeout))

        # 插件网络策略：非法值回退到最安全的 deny
        if cfg.get("plugin_network_policy") not in (
                "deny", "audited_public", "public_only", "allow_all"):
            cfg["plugin_network_policy"] = self.defaults["plugin_network_policy"]

        # 插件隔离模式：非法值回退到 process（进程级隔离）
        if cfg.get("plugin_isolation") not in ("process", "inprocess"):
            cfg["plugin_isolation"] = self.defaults["plugin_isolation"]

        # 插件审计级别：非法值回退到 block（最严格）
        if cfg.get("plugin_security_audit") not in ("off", "warn", "block"):
            cfg["plugin_security_audit"] = self.defaults["plugin_security_audit"]

        # 插件导入限制：非法值回退到 strict（最严格）
        if cfg.get("plugin_security_import_restrict") not in ("off", "safe", "strict"):
            cfg["plugin_security_import_restrict"] = self.defaults["plugin_security_import_restrict"]

    def save(self, config: Dict[str, Any]):
        self._sanitize(config)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def get_api_key(self) -> Optional[str]:
        cfg = self.load()
        method = cfg.get("encryption_method", "keyring")
        if method == "keyring":
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        else:
            # win32crypt 模式：从 base.env 文件解密
            if not os.path.exists(self.key_path):
                return None
            with open(self.key_path, "rb") as f:
                encrypted = base64.b64decode(f.read())
            decrypted = win32crypt.CryptUnprotectData(
                encrypted, None, None, None, 0
            )
            key = decrypted[1].decode("utf-8")
            # ★ 自动迁移：将 win32crypt 存储的 Key 迁移到 keyring，
            #   迁移后删除 base.env 文件，避免文件被直接复制窃取
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
                if os.path.exists(self.key_path):
                    os.remove(self.key_path)
                cfg["encryption_method"] = "keyring"
                self.save(cfg)
            except Exception:
                pass  # keyring 不可用时静默回退，保留 win32crypt 模式
            return key

    def set_api_key(self, key: str):
        cfg = self.load()
        method = cfg.get("encryption_method", "keyring")
        if method == "keyring":
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            if os.path.exists(self.key_path):
                os.remove(self.key_path)
        else:
            encrypted = win32crypt.CryptProtectData(
                key.encode("utf-8"), None, None, None, None, 0
            )
            with open(self.key_path, "wb") as f:
                f.write(base64.b64encode(encrypted))

    def is_first_run(self) -> bool:
        """检查是否config.json 不存在"""
        return not os.path.exists(self.config_path)

    def reset_to_defaults(self) -> Dict[str, Any]:
        """将所有配置重置为默认值并保存。"""
        defaults = self.defaults.copy()
        self.save(defaults)
        return defaults

    def validate_api_key(self, api_key: str, base_url: str = "https://api.deepseek.com") -> bool:
        # 本地部署模式（BaseURL 指向回环地址）：Ollama 等本地服务无需鉴权，
        # 跳过 /models 校验（本地服务通常没有该端点或不需要认证）。
        if is_loopback_url(base_url):
            return True
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            client.models.list()
            return True
        except Exception:
            return False
