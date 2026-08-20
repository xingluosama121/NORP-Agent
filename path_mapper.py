# Vibe Coding Agent - 路径映射器 (Path Mapper)
# 解决沙箱看不见文件的冲突：将宿主路径映射为沙箱可见路径
# Copyright (c) 2026 xingluosama

import os
from pathlib import Path
from typing import Dict, Optional, Tuple


class PathMapper:
    """双向路径映射管理器。

    解决的问题：
    - 沙箱内执行命令时，沙箱有自己的文件系统视图
    - 插件运行在宿主环境，看到的路径是宿主的
    - 需要双向转换，确保两者一致

    用法：
        mapper = PathMapper()
        mapper.add_mapping("/host/project", "/sandbox/project")
        sandbox_path = mapper.to_sandbox("/host/project/src/main.py")
        # => "/sandbox/project/src/main.py"
        host_path = mapper.to_host("/sandbox/project/src/main.py")
        # => "/host/project/src/main.py"
    """

    def __init__(self):
        # 宿主路径 -> 沙箱路径 映射表
        self._host_to_sandbox: Dict[str, str] = {}
        # 沙箱路径 -> 宿主路径 反向映射
        self._sandbox_to_host: Dict[str, str] = {}
        # 锁（用于多线程安全，也可被异步上下文使用）
        self._frozen = False  # 冻结后不可修改

    def add_mapping(self, host_path: str, sandbox_path: str):
        """添加一对路径映射。"""
        if self._frozen:
            return
        host_path = os.path.normpath(os.path.abspath(host_path))
        sandbox_path = os.path.normpath(sandbox_path)
        self._host_to_sandbox[host_path] = sandbox_path
        self._sandbox_to_host[sandbox_path] = host_path

    def add_mappings(self, mappings: Dict[str, str]):
        """批量添加路径映射。"""
        for host, sandbox in mappings.items():
            self.add_mapping(host, sandbox)

    def remove_mapping(self, host_path: str):
        """移除路径映射。"""
        if self._frozen:
            return
        host_path = os.path.normpath(os.path.abspath(host_path))
        if host_path in self._host_to_sandbox:
            sandbox_path = self._host_to_sandbox.pop(host_path)
            self._sandbox_to_host.pop(sandbox_path, None)

    def to_sandbox(self, host_path: str) -> str:
        """将宿主路径转换为沙箱内路径。

        查找最长前缀匹配的映射。
        若无映射，返回原路径（直通模式）。
        """
        host_path = os.path.normpath(os.path.abspath(host_path))

        # 精确匹配
        if host_path in self._host_to_sandbox:
            return self._host_to_sandbox[host_path]

        # 最长前缀匹配
        best_prefix = ""
        best_sandbox = ""
        for host_prefix, sandbox_prefix in self._host_to_sandbox.items():
            if host_path.startswith(host_prefix + os.sep) or host_path == host_prefix:
                if len(host_prefix) > len(best_prefix):
                    best_prefix = host_prefix
                    best_sandbox = sandbox_prefix

        if best_prefix:
            rel = os.path.relpath(host_path, best_prefix)
            return os.path.normpath(os.path.join(best_sandbox, rel))

        return host_path  # 直通

    def to_host(self, sandbox_path: str) -> str:
        """将沙箱内路径转换为宿主路径。"""
        sandbox_path = os.path.normpath(sandbox_path)

        if sandbox_path in self._sandbox_to_host:
            return self._sandbox_to_host[sandbox_path]

        best_prefix = ""
        best_host = ""
        for sandbox_prefix, host_prefix in self._sandbox_to_host.items():
            if sandbox_path.startswith(sandbox_prefix + os.sep) or sandbox_path == sandbox_prefix:
                if len(sandbox_prefix) > len(best_prefix):
                    best_prefix = sandbox_prefix
                    best_host = host_prefix

        if best_prefix:
            rel = os.path.relpath(sandbox_path, best_prefix)
            return os.path.normpath(os.path.join(best_host, rel))

        return sandbox_path

    def get_mappings(self) -> Dict[str, str]:
        """获取所有映射的副本。"""
        return dict(self._host_to_sandbox)

    def freeze(self):
        """冻结映射表，禁止修改。"""
        self._frozen = True

    def unfreeze(self):
        """解除冻结。"""
        self._frozen = False

    def clear(self):
        """清除所有映射。"""
        if self._frozen:
            return
        self._host_to_sandbox.clear()
        self._sandbox_to_host.clear()


class PluginPathMapper(PathMapper):
    """专为插件设计的路径映射器。

    额外能力：
    - 插件只能访问其声明的路径
    - 自动从插件 manifest 中提取允许的路径
    - 越权访问自动拒绝
    """

    def __init__(self, plugin_id: str):
        super().__init__()
        self.plugin_id = plugin_id
        self._allowed_host_paths: set = set()
        self._denied_host_paths: set = set()
        self._strict_mode = False  # 严格模式：只允许白名单路径

    def allow_path(self, host_path: str):
        """添加允许的宿主路径（白名单）。"""
        self._allowed_host_paths.add(os.path.normpath(os.path.abspath(host_path)))

    def deny_path(self, host_path: str):
        """添加禁止的宿主路径（黑名单）。"""
        self._denied_host_paths.add(os.path.normpath(os.path.abspath(host_path)))

    def set_strict(self, enabled: bool):
        """设置严格模式。"""
        self._strict_mode = enabled

    def is_path_allowed(self, host_path: str) -> bool:
        """检查路径是否被允许访问。"""
        host_path = os.path.normpath(os.path.abspath(host_path))

        # 黑名单优先
        for denied in self._denied_host_paths:
            if host_path.startswith(denied + os.sep) or host_path == denied:
                return False

        # 严格模式：必须在白名单中
        if self._strict_mode:
            for allowed in self._allowed_host_paths:
                if host_path.startswith(allowed + os.sep) or host_path == allowed:
                    return True
            return False

        return True

    def to_sandbox_safe(self, host_path: str) -> Tuple[str, bool]:
        """安全地转换路径，返回 (沙箱路径, 是否允许)。"""
        if not self.is_path_allowed(host_path):
            return host_path, False
        return self.to_sandbox(host_path), True
