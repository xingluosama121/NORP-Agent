# -*- coding: utf-8 -*-
"""
NORP Git 插件（对齐 dsh-git-graph + dsh-aionui-panel 的 SCM 面板）
==================================================================
把版本控制能力结构化地交给 Agent，避免依赖 exec_cmd 手敲 git 命令：

· git_status    —— 工作区状态（分支 + 变更文件）
· git_log       —— 提交历史（支持 --graph 提交图 / --all 全部分支）
· git_diff      —— 差异（工作区 / 已暂存）
· git_branch    —— 分支的增删查改
· git_stage / git_unstage —— 暂存 / 取消暂存
· git_commit_staged —— 提交已暂存内容
· git_checkout  —— 切换 / 新建分支
· git_pull / git_push —— 拉取 / 推送
· git_summary   —— 一屏概览（分支 + 前后差异 + 变更 + 最近提交）

所有命令都在当前工作区（context.project_root）执行，命令参数以列表形式
传给 git（不经过 shell 拼接），规避 shell 注入。
"""

PLUGIN_NAME = "NORP Git"
PLUGIN_PUBLISHER = "norp-community"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "Git 版本控制：状态/日志/提交图/差异/分支/暂存/提交/检出/推拉/概览，"
    "对齐 dsh-git-graph 与 aionui-panel SCM 能力"
)

import os
import subprocess
import shutil


def _hidden_flags():
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _git_bin():
    try:
        return shutil.which("git") or "git"
    except Exception:
        return "git"


def _git(context, *args, timeout=60, input_text=None):
    """在 project_root 下执行 git，返回 (rc, out, err)。"""
    startupinfo, creationflags = _hidden_flags()
    try:
        proc = subprocess.run(
            [_git_bin()] + list(args),
            cwd=context.project_root, capture_output=True, text=True,
            timeout=timeout, input=input_text,
            startupinfo=startupinfo, creationflags=creationflags)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "git 命令超时（%ds）" % timeout
    except FileNotFoundError:
        return -1, "", "未找到 git.exe，请先安装 Git"
    except Exception as e:
        return -1, "", "执行异常：%s" % e


def _is_repo(context):
    rc, out, _ = _git(context, "rev-parse", "--is-inside-work-tree", timeout=15)
    return rc == 0 and out.strip() == "true"


def _truncate(s, n=8000):
    if not s:
        return s
    if len(s) > n:
        return s[:n] + "\n…（输出过长，已截断）"
    return s


def _ok_or_err(alias, rc, out, err):
    if rc == 0:
        body = _truncate(out) if out else "(无输出)"
        return "✅ %s\n```\n%s\n```" % (alias, body)
    detail = _truncate((out or "") + ("\n" + err if err else "")) or "(无错误信息)"
    return "❌ %s 失败（exit %s）\n```\n%s\n```" % (alias, rc, detail)


# ----------------------------------------------------------------------
# 工具定义
# ----------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看工作区 Git 状态：当前分支、与远程的领先/落后、变更文件列表（含未跟踪/已暂存/已修改）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "查看提交历史，可开启 ASCII 提交图与全部分支。",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "只看某分支的提交（可选）"},
                    "count": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    "graph": {"type": "boolean", "description": "是否显示提交图（--graph），默认 true"},
                    "all": {"type": "boolean", "description": "是否包含全部分支（--all），默认 false"}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看差异：工作区未暂存的改动，或已暂存（staged）的改动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "true=看已暂存差异；false=看未暂存差异（默认）"},
                    "path": {"type": "string", "description": "只看某文件的差异（可选）"}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "分支管理：列出 / 新建 / 切换 / 删除分支。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "switch", "delete"], "description": "操作类型"},
                    "name": {"type": "string", "description": "分支名（create/switch/delete 必填）"}
                },
                "required": ["action"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_stage",
            "description": "暂存文件（git add）。paths 传 . 表示暂存全部。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "string", "description": "逗号分隔的文件路径，或 . 表示全部；默认 ."}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_unstage",
            "description": "取消暂存文件（git restore --staged）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "string", "description": "逗号分隔的文件路径，或 . 表示全部；默认 ."}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit_staged",
            "description": "提交已暂存的内容（约定式提交信息）。只提交 staged，未暂存的不提交。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "提交信息，如 feat: add xxx"}
                },
                "required": ["message"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_checkout",
            "description": "切换到已有分支/提交，或新建并切换到新分支。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "分支名或提交哈希"},
                    "create": {"type": "boolean", "description": "true=新建并切换到该分支（git checkout -b）"}
                },
                "required": ["target"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "从远程拉取并合并（git pull）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "远程名，默认 origin"},
                    "branch": {"type": "string", "description": "远程分支名（可选）"}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "推送到远程（git push）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "远程名，默认 origin"},
                    "branch": {"type": "string", "description": "本地分支名（可选，默认当前分支）"}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_summary",
            "description": "一屏 Git 概览：分支、与远程的领先/落后、变更文件数、最近提交（类似 SCM 面板首页）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    }
]


# ----------------------------------------------------------------------
# 工具调度
# ----------------------------------------------------------------------
def execute(tool_name, args, context):
    try:
        if not _is_repo(context):
            return "❌ 当前工作区不是 Git 仓库（%s）" % context.project_root
        if tool_name == "git_status":
            return _cmd_status(context)
        if tool_name == "git_log":
            return _cmd_log(context, args)
        if tool_name == "git_diff":
            return _cmd_diff(context, args)
        if tool_name == "git_branch":
            return _cmd_branch(context, args)
        if tool_name == "git_stage":
            return _cmd_stage(context, args.get("paths"))
        if tool_name == "git_unstage":
            return _cmd_unstage(context, args.get("paths"))
        if tool_name == "git_commit_staged":
            return _cmd_commit(context, args.get("message"))
        if tool_name == "git_checkout":
            return _cmd_checkout(context, args)
        if tool_name == "git_pull":
            return _cmd_pull(context, args)
        if tool_name == "git_push":
            return _cmd_push(context, args)
        if tool_name == "git_summary":
            return _cmd_summary(context)
        return "Unknown tool: %s" % tool_name
    except Exception as e:
        return "❌ 工具执行异常：%s" % e


# ----------------------------------------------------------------------
# 各工具实现
# ----------------------------------------------------------------------
def _cmd_status(context):
    rc, out, err = _git(context, "status", "--porcelain=v1", "-b")
    if rc != 0:
        return _ok_or_err("git status", rc, out, err)
    lines = out.splitlines()
    if not lines:
        return "✅ 工作区干净，无变更。"
    header = lines[0]
    body = "\n".join(lines[1:])
    return "📊 **Git 状态**\n分支：%s\n```\n%s\n```" % (header, _truncate(body, 6000))


def _cmd_log(context, args):
    count = int(args.get("count") or 20)
    count = max(1, min(count, 100))
    cmd = ["log"]
    if args.get("graph", True):
        cmd.append("--graph")
    if args.get("all"):
        cmd.append("--all")
    cmd += ["--oneline", "--decorate", "--date=short", "-n", str(count)]
    if args.get("branch"):
        cmd.append(args["branch"])
    rc, out, err = _git(context, *cmd)
    return _ok_or_err("git log", rc, out, err)


def _cmd_diff(context, args):
    cmd = ["diff"]
    if args.get("staged"):
        cmd.append("--staged")
    cmd.append("--")
    if args.get("path"):
        cmd.append(args["path"])
    rc, out, err = _git(context, *cmd)
    if rc == 0 and not out:
        return "✅ 没有差异。"
    return _ok_or_err("git diff", rc, out, err)


def _cmd_branch(context, args):
    action = args.get("action") or "list"
    name = (args.get("name") or "").strip()
    if action == "list":
        rc, out, err = _git(context, "branch", "-vv")
        return _ok_or_err("git branch", rc, out, err)
    if action == "create":
        if not name:
            return "❌ create 需要 name"
        rc, out, err = _git(context, "branch", name)
        return _ok_or_err("创建分支 %s" % name, rc, out, err)
    if action == "switch":
        if not name:
            return "❌ switch 需要 name"
        rc, out, err = _git(context, "checkout", name)
        return _ok_or_err("切换到 %s" % name, rc, out, err)
    if action == "delete":
        if not name:
            return "❌ delete 需要 name"
        rc, out, err = _git(context, "branch", "-d", name)
        return _ok_or_err("删除分支 %s" % name, rc, out, err)
    return "❌ 未知 action：%s" % action


def _split_paths(paths):
    if not paths or paths.strip() in ("", "."):
        return ["."]
    return [p.strip() for p in paths.split(",") if p.strip()]


def _cmd_stage(context, paths):
    rc, out, err = _git(context, "add", "--", *_split_paths(paths))
    return _ok_or_err("git add", rc, out, err)


def _cmd_unstage(context, paths):
    rc, out, err = _git(context, "restore", "--staged", "--", *_split_paths(paths))
    return _ok_or_err("git restore --staged", rc, out, err)


def _cmd_commit(context, message):
    if not message or not message.strip():
        return "❌ 提交信息不能为空"
    rc, out, err = _git(context, "commit", "-m", message.strip())
    return _ok_or_err("git commit", rc, out, err)


def _cmd_checkout(context, args):
    target = (args.get("target") or "").strip()
    if not target:
        return "❌ target 不能为空"
    if args.get("create"):
        rc, out, err = _git(context, "checkout", "-b", target)
        return _ok_or_err("新建并切换到 %s" % target, rc, out, err)
    rc, out, err = _git(context, "checkout", target)
    return _ok_or_err("checkout %s" % target, rc, out, err)


def _cmd_pull(context, args):
    remote = args.get("remote") or "origin"
    cmd = ["pull", remote]
    if args.get("branch"):
        cmd.append(args["branch"])
    rc, out, err = _git(context, *cmd, timeout=180)
    return _ok_or_err("git pull", rc, out, err)


def _cmd_push(context, args):
    remote = args.get("remote") or "origin"
    cmd = ["push", remote]
    if args.get("branch"):
        cmd.append(args["branch"])
    rc, out, err = _git(context, *cmd, timeout=180)
    return _ok_or_err("git push", rc, out, err)


def _cmd_summary(context):
    rc, out, _ = _git(context, "status", "--porcelain=v1", "-b")
    branch_line = out.splitlines()[0] if out else "(未知)"
    n_changed = max(0, len(out.splitlines()) - 1)

    rc2, log_out, _ = _git(context, "log", "--oneline", "--decorate", "-n", "5")
    recent = log_out or "(无提交)"

    return (
        "📊 **Git 概览**\n\n"
        "**分支/同步**\n```\n%s\n```\n"
        "**变更文件数**：%d 个\n\n"
        "**最近提交**\n```\n%s\n```"
    ) % (branch_line, n_changed, _truncate(recent, 2000))


# ----------------------------------------------------------------------
# 钩子
# ----------------------------------------------------------------------
def on_agent_init(context):
    if _is_repo(context):
        context.logger.info("NORP Git 就绪，工作区为 Git 仓库")
    else:
        context.logger.info("NORP Git 就绪（当前工作区不是 Git 仓库）")
