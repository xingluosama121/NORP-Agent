# ──────────────────────────────────────────────────────────────
# Plugin: Code Reviewer
# Publisher: xingluosama
# Version: 2.0.0
# Description: 全面的代码质量审查插件。
#   检查项目包括：
#     - 文档字符串覆盖率和质量
#     - 异常处理规范
#     - 圈复杂度分析
#     - 函数/方法长度
#     - 命名规范检查
#     - TODO/FIXME/HACK 标记
#     - 安全隐患（硬编码密钥、危险函数、注入风险等）
#     - 代码格式（行长、尾随空格、TAB 缩进）
#     - 嵌套深度
#     - 函数参数数量
#     - 导入分析
#   返回结构化审查报告，包含严重性分级、评分和具体改进建议。
#   支持 Python、JavaScript、TypeScript、Go、Rust、Java 等常见语言。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Code Reviewer"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "2.0.0"
PLUGIN_DESCRIPTION = (
    "全面的代码质量审查：文档字符串、异常处理、圈复杂度、命名规范、"
    "安全隐患、代码格式等。支持 Python/JS/TS/Go/Rust/Java 等。"
)

import os
import re
import ast
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "code_review",
            "description": (
                "对指定的源代码文件执行代码质量审查。"
                "检查项目包括：函数/类文档字符串、异常处理、代码复杂度、"
                "命名规范、TODO/FIXME 标记、安全隐患等。"
                "返回结构化的审查报告，包含问题计数和具体建议。"
                "适用于 Python、JavaScript、TypeScript、Go、Rust 等常见语言。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要审查的文件路径（相对于工作区根目录）"
                    },
                    "strictness": {
                        "type": "string",
                        "description": (
                            "审查严格程度：'lenient'（宽松）、"
                            "'normal'（正常，默认）、'strict'（严格）"
                        ),
                        "default": "normal"
                    }
                },
                "required": ["file_path"],
                "additionalProperties": False
            }
        }
    }
]

# ═══════════════════════════════════════════════════════════════
# 2. 语言定义 & 配置
# ═══════════════════════════════════════════════════════════════

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".swift", ".kt", ".rb",
    ".php", ".sh", ".bat", ".ps1", ".sql", ".cs",
}

# ── 每种语言的语法特征 ──
_LANG_FEATURES = {
    ".py": {
        "name": "Python",
        "function": r"^\s*def\s+(\w+)\s*\([^)]*\)",
        "class": r"^\s*class\s+(\w+)",
        "comment": r"#",
        "docstring": r'^\s*(?:"{3}|\'{3})',
        "import": r"^\s*(?:import\s+|from\s+\S+\s+import\s+)",
        "line_comment": "#",
        "block_comment_start": '"""',
        "block_comment_end": '"""',
    },
    ".js": {
        "name": "JavaScript",
        "function": (
            r"^\s*(?:function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*"
            r"(?:async\s*)?\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?"
            r"function)"
        ),
        "class": r"^\s*class\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*(?:import\s+|require\s*\()",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".ts": {
        "name": "TypeScript",
        "function": (
            r"^\s*(?:function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*"
            r"(?:async\s*)?\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?"
            r"function|(?:public|private|protected)?\s*(?:static\s*)?"
            r"(?:async\s*)?(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{)"
        ),
        "class": r"^\s*(?:export\s+)?class\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*import\s+",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".go": {
        "name": "Go",
        "function": r"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(",
        "class": r"^\s*type\s+(\w+)\s+struct",
        "comment": r"//",
        "import": r"^\s*(?:import\s+|package\s+)",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".rs": {
        "name": "Rust",
        "function": r"^\s*(?:pub\s+)?fn\s+(\w+)\s*[<(]",
        "class": r"^\s*(?:pub\s+)?struct\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*(?:use\s+|mod\s+|extern\s+crate\s+)",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".java": {
        "name": "Java",
        "function": (
            r"^\s*(?:public|private|protected|static|\s)+"
            r"\S+\s+(\w+)\s*\([^)]*\)\s*(?:\{|throws)"
        ),
        "class": r"^\s*(?:public\s+)?class\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*import\s+",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".c": {
        "name": "C",
        "function": r"^\s*\S+\s+(\w+)\s*\([^)]*\)\s*\{",
        "comment": r"//",
        "import": r"^\s*#include\s+",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".cpp": {
        "name": "C++",
        "function": (
            r"^\s*(?:\S+\s+)+(?:(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{|"
            r"(\w+)::(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{)"
        ),
        "class": r"^\s*(?:class|struct)\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*#include\s+",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".h": {
        "name": "C/C++ Header",
        "function": r"^\s*\S+\s+(\w+)\s*\([^)]*\)\s*;",
        "comment": r"//",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".cs": {
        "name": "C#",
        "function": (
            r"^\s*(?:public|private|protected|internal|static|\s)+"
            r"\S+\s+(\w+)\s*\([^)]*\)\s*\{"
        ),
        "class": r"^\s*(?:public\s+)?class\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*using\s+",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".rb": {
        "name": "Ruby",
        "function": r"^\s*def\s+(\w+)",
        "class": r"^\s*class\s+(\w+)",
        "comment": r"#",
        "import": r"^\s*(?:require\s+|include\s+)",
        "line_comment": "#",
    },
    ".php": {
        "name": "PHP",
        "function": r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)\s*\(",
        "class": r"^\s*class\s+(\w+)",
        "comment": r"//",
        "import": r"^\s*(?:require|include|use\s+)",
        "line_comment": "//",
        "block_comment_start": "/*",
        "block_comment_end": "*/",
    },
    ".sh": {
        "name": "Shell",
        "function": r"^\s*(?:function\s+)?(\w+)\s*\(\s*\)",
        "comment": r"#",
        "line_comment": "#",
    },
    ".sql": {
        "name": "SQL",
        "comment": r"--",
        "line_comment": "--",
    },
}

# ── 圈复杂度关键词（每种语言的分支关键词） ──
_CYCLOMATIC_KEYWORDS = {
    ".py": [
        r"\bif\b", r"\belif\b", r"\bfor\b", r"\bwhile\b",
        r"\band\b(?!\s*\w+\s*=)", r"\bor\b(?!\s*\w+\s*=)",
        r"\bexcept\b", r"\bwith\b", r"\bassert\b",
    ],
    ".js": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b(?!.*:)", r"\bcatch\b", r"\b\?\s*[^:]+\s*:",
        r"\&\&", r"\|\|",
    ],
    ".ts": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b(?!.*:)", r"\bcatch\b", r"\b\?\s*[^:]+\s*:",
        r"\&\&", r"\|\|",
    ],
    ".go": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b",
        r"\bcase\b", r"\bswitch\b", r"\bselect\b",
    ],
    ".rs": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bmatch\b", r"\b\?\s*[^:]+\s*:",
    ],
    ".java": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b(?!.*:)", r"\bcatch\b",
    ],
    ".c": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b", r"\bswitch\b",
    ],
    ".cpp": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b", r"\bswitch\b", r"\bcatch\b",
    ],
    ".cs": [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b(?!.*:)", r"\bcatch\b",
    ],
    ".rb": [
        r"\bif\b", r"\belsif\b", r"\bunless\b", r"\bfor\b",
        r"\bwhile\b", r"\buntil\b", r"\bwhen\b", r"\brescue\b",
    ],
    ".php": [
        r"\bif\b", r"\belseif\b", r"\bfor\b", r"\bforeach\b",
        r"\bwhile\b", r"\bcase\b", r"\bcatch\b",
    ],
    ".sh": [
        r"\bif\b", r"\belif\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b",
    ],
}

# ── 严格程度配置 ──
_MAX_LINE_LENGTH = {"lenient": 150, "normal": 120, "strict": 100}
_MAX_FUNCTION_LINES = {"lenient": 80, "normal": 50, "strict": 30}
_MAX_CYCLOMATIC = {"lenient": 20, "normal": 12, "strict": 8}
_MAX_PARAMS = {"lenient": 8, "normal": 5, "strict": 3}
_MAX_NESTING = {"lenient": 6, "normal": 4, "strict": 3}
_MIN_DOCSTRING_RATIO = {"lenient": 0.1, "normal": 0.3, "strict": 0.5}
_MIN_COMMENT_RATIO = {"lenient": 0.03, "normal": 0.05, "strict": 0.10}

# ═══════════════════════════════════════════════════════════════
# 3. 安全检测模式
# ═══════════════════════════════════════════════════════════════

# ── 通用危险模式 ──
_DANGER_PATTERNS = [
    # Python 危险函数
    (r"\beval\s*\(", "critical", "eval() 可执行任意代码，存在代码注入风险", {".py"}),
    (r"\bexec\s*\(", "critical", "exec() 可执行任意代码，存在代码注入风险", {".py"}),
    (r"\b__import__\s*\(", "warning", "动态导入 __import__() 可能被滥用", {".py"}),
    (r"\bcompile\s*\(.*\)", "warning", "compile() + exec/eval 组合是危险信号", {".py"}),
    (r"\bctypes\b", "warning", "ctypes 绕过 Python 安全边界", {".py"}),
    (r"\bsubprocess\.(?:call|Popen|run|check_output)\s*\(", "warning",
     "subprocess 调用外部命令，确保参数已清理", {".py"}),
    (r"\bos\.system\s*\(", "critical", "os.system() 存在命令注入风险", {".py"}),
    (r"\bos\.popen\s*\(", "warning", "os.popen() 存在命令注入风险", {".py"}),
    (r"\bpickle\.(?:loads?|dump)\s*\(", "warning", "pickle 反序列化不可信数据不安全", {".py"}),
    (r"\byaml\.load\s*\(", "warning", "yaml.load() 不安全，请使用 yaml.safe_load()", {".py"}),
    (r"\binput\s*\(\s*\)", "info", "Python 2 的 input() 存在风险，Python 3 中安全", {".py"}),

    # SQL 注入
    (r"\"\s*SELECT\s+.+\s*FROM.*\".*%", "critical", "SQL 字符串拼接 → SQL 注入风险",
     {".py", ".js", ".ts", ".java", ".php", ".rb"}),
    (r"f\".*SELECT\s+", "critical", "f-string SQL → SQL 注入风险", {".py"}),
    (r"\.execute\s*\(\s*['\"].*%\s*", "warning", "参数化查询优于字符串拼接", {".py"}),

    # XSS / HTML 注入
    (r"\.innerHTML\s*=", "warning", "innerHTML 赋值存在 XSS 风险", {".js", ".ts"}),
    (r"dangerouslySetInnerHTML", "warning", "dangerouslySetInnerHTML 存在 XSS 风险", {".jsx", ".tsx"}),
    (r"\bdocument\.write\s*\(", "warning", "document.write() 存在 XSS 风险", {".js", ".ts"}),

    # 路径遍历
    (r"\.\./.*\+", "warning", "可能存在路径遍历漏洞", set()),

    # 密码学弱算法
    (r"\bhashlib\.md5\s*\(", "info", "MD5 已不安全，建议使用 SHA256+", {".py"}),
    (r"\bhashlib\.sha1\s*\(", "info", "SHA1 已不安全，建议使用 SHA256+", {".py"}),

    # 硬编码密钥（通用）
    (r"(?i)(api_?key|secret|password|token|passwd|credential)\s*=\s*[\"']"
     r"[^\"']{6,}[\"']",
     "critical", "可能存在硬编码的敏感信息", set()),

    # 禁用 TLS 验证
    (r"(?i)verify\s*=\s*False", "warning", "TLS 证书验证被禁用", {".py"}),
    (r"(?i)rejectUnauthorized\s*:\s*false", "warning", "TLS 证书验证被禁用",
     {".js", ".ts"}),

    # 权限过宽
    (r"chmod\s*\(\s*0?o?777\b", "warning", "文件权限 777 过于宽松", {".py", ".sh"}),
    (r"chmod\s+777\b", "warning", "文件权限 777 过于宽松", {".sh"}),
]

# ── 硬编码密钥误报过滤 ──
_KEY_FALSE_POSITIVES = [
    r"(?i)YOUR_.*_HERE",
    r"(?i)REPLACE_.*",
    r"\$\{[A-Z_]+\}",
    r"os\.(environ|getenv)\s*\[",
    r"os\.(environ|getenv)\.get",
    r"(?i)process\.env\.",
    r"=\s*None\s*$",
    r"=\s*\"\"\s*$",
    r"=\s*''\s*$",
    r"(?i)example|sample|demo|test",
    r"#.*[Nn][Oo][Qq][Aa]",
    r"(?i)getenv\s*\(",
    r"(?i)get_env\s*\(",
    r"config\[.+(?:key|secret|token|password)",
    r"(?i)\.env\.",
]

# ── 通用不良模式 ──
_BAD_PATTERNS = [
    (r"(TODO|FIXME|HACK|XXX|BUG|WORKAROUND|KLUDGE)",
     "warning", "发现待办/临时标记", set()),
    (r"except\s*:", "critical", "裸 except 会捕获所有异常（含 KeyboardInterrupt）",
     {".py"}),
    (r"except\s+Exception\s*:", "info", "捕获太宽泛，建议捕获更具体的异常类型",
     {".py"}),
    (r"pass\s*$", "info", "空的 except/pass 块吞掉了异常", {".py"}),
    (r"^\s*print\s*\(.*\)\s*$", "info", "可能存在调试用 print 语句，建议使用 logging",
     {".py"}),
    (r"console\.log\s*\(.*\)\s*;?\s*$", "info", "可能存在调试用 console.log",
     {".js", ".ts"}),
    (r"System\.out\.println", "info", "可能存在调试用打印语句", {".java"}),
    (r"var_dump\s*\(|echo\s+['\"].*['\"]\s*;", "info", "可能存在调试输出", {".php"}),
    (r"puts\s+['\"]", "info", "可能存在调试输出", {".rb"}),
    (r"println!\s*\(.*\)", "info", "可能存在调试输出", {".rs"}),
    (r"fmt\.Println", "info", "可能存在调试输出", {".go"}),
    # 异常处理反模式
    (r"catch\s*\(\s*\)", "warning", "空 catch 块吞掉异常", {".js", ".ts"}),
    (r"catch\s*\(\s*Exception\b", "info", "捕获太宽泛", {".java"}),
    # 空代码块
    (r"\{\s*\}", "info", "空代码块", {".js", ".ts", ".java", ".go", ".rs"}),
    # 资源泄漏
    (r"(?:open|connect)\s*\(.*\)(?!.*(?:with|close|finally))",
     "warning", "资源可能未正确关闭，建议使用上下文管理器", {".py"}),
]

# ═══════════════════════════════════════════════════════════════
# 4. 命名规范
# ═══════════════════════════════════════════════════════════════

# Python 命名规范 (PEP 8)
_PYTHON_NAMING = {
    "function": r"^[a-z][a-z0-9_]*$",       # snake_case
    "class": r"^[A-Z][a-zA-Z0-9]*$",        # PascalCase
    "variable": r"^[a-z][a-z0-9_]*$",       # snake_case
    "constant": r"^[A-Z][A-Z0-9_]*$",       # UPPER_CASE
}

_SHORT_NAMES = {"i", "j", "k", "x", "y", "z", "n", "m", "id", "ok", "fn", "cb",
                "op", "e", "ex", "ch", "s", "f", "g", "a", "b", "c", "d", "t",
                "el", "it", "ev", "el", "vm", "db", "ui", "ai", "io", "os",
                "re", "os"}


# ═══════════════════════════════════════════════════════════════
# 5. 核心审查逻辑
# ═══════════════════════════════════════════════════════════════

def _review_file(file_path: str, strictness: str) -> str:
    """执行完整的代码审查并返回 Markdown 报告。"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in _CODE_EXTENSIONS:
        return (
            f"⚠️ 不支持的文件类型：`{ext}`。\n"
            f"支持的类型：{', '.join(sorted(_CODE_EXTENSIONS))}"
        )

    # ── 读文件 ──
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            lines = content.split("\n")
    except Exception as e:
        return f"❌ 无法读取文件：{e}"

    issues: list = []
    lang = _LANG_FEATURES.get(ext, {})
    lang_name = lang.get("name", ext)
    func_pat = lang.get("function")
    class_pat = lang.get("class")
    line_comment = lang.get("line_comment", "#")

    # ── 严格度参数 ──
    max_line = _MAX_LINE_LENGTH.get(strictness, 120)
    max_func_lines = _MAX_FUNCTION_LINES.get(strictness, 50)
    max_cyclomatic = _MAX_CYCLOMATIC.get(strictness, 12)
    max_params = _MAX_PARAMS.get(strictness, 5)
    max_nesting = _MAX_NESTING.get(strictness, 4)
    min_comment_ratio = _MIN_COMMENT_RATIO.get(strictness, 0.05)

    # ═════════════════════════════════════════════════════
    # 5a. 基础统计
    # ═════════════════════════════════════════════════════
    total_lines = len(lines)
    non_empty_lines = [l for l in lines if l.strip()]
    non_empty = len(non_empty_lines)
    blank_lines = total_lines - non_empty

    # 注释行统计
    comment_lines = 0
    in_block_comment = False
    bs = lang.get("block_comment_start", "")
    be = lang.get("block_comment_end", "")
    for raw in lines:
        stripped = raw.strip()
        if bs and be and bs == be:
            # 用相同符号的块注释（如 Python """..."""）
            if stripped.startswith(bs) and len(stripped) >= 3:
                in_block_comment = not in_block_comment
                comment_lines += 1
                continue
        elif bs and be:
            if in_block_comment:
                comment_lines += 1
                if be in stripped:
                    in_block_comment = False
                continue
            if bs in stripped:
                in_block_comment = True
                comment_lines += 1
                if be in stripped:
                    in_block_comment = False
                continue
        if in_block_comment:
            comment_lines += 1
            continue
        if line_comment and stripped.startswith(line_comment):
            comment_lines += 1

    # 函数/类计数
    func_count = 0
    func_names = []
    class_count = 0
    class_names = []

    if func_pat:
        for l in lines:
            m = re.search(func_pat, l)
            if m:
                name = m.group(1) or m.group(2) or m.group(3) or "?"
                if name not in _SHORT_NAMES:
                    func_names.append(name)
                func_count += 1

    if class_pat:
        for l in lines:
            m = re.search(class_pat, l)
            if m:
                class_names.append(m.group(1))
                class_count += 1

    # ═════════════════════════════════════════════════════
    # 5b. 逐行检查
    # ═════════════════════════════════════════════════════
    for i, raw in enumerate(lines, 1):
        stripped = raw.rstrip()

        # ── 行长度 ──
        if len(raw) > max_line:
            preview = raw[:60].strip()
            issues.append({
                "line": i, "severity": "info", "category": "行长度",
                "message": f"行长度 {len(raw)} 超过限制 {max_line}（`{preview}...`）"
            })

        # ── 尾随空格 (strict) ──
        if strictness == "strict" and raw != stripped:
            issues.append({
                "line": i, "severity": "info", "category": "格式",
                "message": "行尾有尾随空格"
            })

        # ── TAB 缩进 (strict) ──
        if strictness == "strict" and "\t" in raw:
            issues.append({
                "line": i, "severity": "info", "category": "格式",
                "message": "使用了 TAB 缩进，建议使用空格"
            })

    # ═════════════════════════════════════════════════════
    # 5c. 模式匹配（安全 + 不良模式）
    # ═════════════════════════════════════════════════════
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()

        # ── 危险模式 ──
        for pattern, sev, msg, filter_exts in _DANGER_PATTERNS:
            if filter_exts and ext not in filter_exts:
                continue
            if re.search(pattern, raw, re.IGNORECASE):
                # 硬编码密钥误报过滤
                if sev == "critical" and "敏感信息" in msg:
                    if any(re.search(fp, raw, re.IGNORECASE)
                           for fp in _KEY_FALSE_POSITIVES):
                        continue
                issues.append({
                    "line": i, "severity": sev,
                    "category": "🔒 安全",
                    "message": msg
                })

        # ── 不良模式 ──
        for pattern, sev, msg, filter_exts in _BAD_PATTERNS:
            if filter_exts and ext not in filter_exts:
                continue
            if re.search(pattern, stripped):
                # 跳过已由安全检测捕获的项
                issues.append({
                    "line": i, "severity": sev,
                    "category": "⚠️ 代码质量",
                    "message": msg
                })

    # ═════════════════════════════════════════════════════
    # 5d. 函数/方法分析
    # ═════════════════════════════════════════════════════
    functions = _extract_functions(lines, ext, func_pat)

    for fn in functions:
        fn_start = fn["start"]
        fn_end = fn["end"]
        fn_name = fn["name"]
        fn_lines = fn_end - fn_start + 1
        fn_body = lines[fn_start - 1:fn_end]

        # ── 函数长度 ──
        if fn_lines > max_func_lines:
            issues.append({
                "line": fn_start, "severity": "warning",
                "category": "函数长度",
                "message": (
                    f"`{fn_name}()` 共 {fn_lines} 行，超过建议的 "
                    f"{max_func_lines} 行，考虑拆分"
                )
            })

        # ── 圈复杂度 ──
        cyclo = _compute_cyclomatic(fn_body, ext)
        if cyclo > max_cyclomatic:
            issues.append({
                "line": fn_start, "severity": "warning",
                "category": "圈复杂度",
                "message": (
                    f"`{fn_name}()` 圈复杂度 ≈ {cyclo}，"
                    f"超过建议的 {max_cyclomatic}，考虑简化"
                )
            })

        # ── 参数数量 ──
        params = _count_params(lines[fn_start - 1], ext)
        if params > max_params:
            issues.append({
                "line": fn_start, "severity": "info",
                "category": "参数数量",
                "message": (
                    f"`{fn_name}()` 有 {params} 个参数，"
                    f"超过建议的 {max_params} 个"
                )
            })

        # ── 嵌套深度 ──
        depth = _compute_nesting(fn_body, ext)
        if depth > max_nesting:
            issues.append({
                "line": fn_start, "severity": "info",
                "category": "嵌套深度",
                "message": (
                    f"`{fn_name}()` 最大嵌套深度 {depth}，"
                    f"超过建议的 {max_nesting}，考虑重构"
                )
            })

        # ── 文档字符串 (Python) ──
        if ext == ".py":
            if not _has_docstring(fn_body):
                issues.append({
                    "line": fn_start, "severity": "info",
                    "category": "文档字符串",
                    "message": f"`{fn_name}()` 缺少文档字符串"
                })

    # ═════════════════════════════════════════════════════
    # 5e. 类分析 (Python)
    # ═════════════════════════════════════════════════════
    if ext == ".py" and class_pat:
        classes = _extract_classes(lines, class_pat)
        for cls in classes:
            if not _has_docstring(lines[cls["start"] - 1:cls["end"]]):
                issues.append({
                    "line": cls["start"], "severity": "info",
                    "category": "文档字符串",
                    "message": f"类 `{cls['name']}` 缺少文档字符串"
                })

    # ═════════════════════════════════════════════════════
    # 5f. 命名检查 (Python)
    # ═════════════════════════════════════════════════════
    if ext == ".py" and strictness in ("normal", "strict"):
        for name in func_names:
            if name.startswith("_"):
                continue
            if not re.match(r"^[a-z][a-z0-9_]*$", name) and name not in _SHORT_NAMES:
                issues.append({
                    "line": 0, "severity": "info",
                    "category": "命名规范",
                    "message": f"函数名 `{name}` 不符合 snake_case 规范"
                })

        for name in class_names:
            if not re.match(r"^[A-Z][a-zA-Z0-9]*$", name):
                issues.append({
                    "line": 0, "severity": "info",
                    "category": "命名规范",
                    "message": f"类名 `{name}` 不符合 PascalCase 规范"
                })

    # ═════════════════════════════════════════════════════
    # 5g. 深度分析 — Python AST
    # ═════════════════════════════════════════════════════
    if ext == ".py":
        try:
            tree = ast.parse(content)
            ast_issues = _analyze_python_ast(tree, file_path, strictness)
            issues.extend(ast_issues)
        except SyntaxError:
            issues.append({
                "line": 1, "severity": "critical",
                "category": "🔴 语法",
                "message": "文件包含语法错误，无法进行 AST 级别分析"
            })

    # ═════════════════════════════════════════════════════
    # 5h. 构建报告
    # ═════════════════════════════════════════════════════
    return _build_report(
        file_path=file_path,
        lang_name=lang_name,
        strictness=strictness,
        total_lines=total_lines,
        non_empty=non_empty,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        func_count=func_count,
        class_count=class_count,
        issues=issues,
        min_comment_ratio=min_comment_ratio,
    )


# ═══════════════════════════════════════════════════════════════
# 6. 辅助函数
# ═══════════════════════════════════════════════════════════════

def _extract_functions(lines: list, ext: str, func_pat) -> list:
    """提取文件中所有函数的起止行号。"""
    functions = []
    if not func_pat:
        return functions

    in_func = False
    func_start = 0
    func_name = "?"
    brace_depth = 0
    indent_level = -1

    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()

        # 检测函数开始
        if re.search(func_pat, raw) and not in_func:
            # 确保是函数定义（不是函数调用）
            if ext == ".py" and not raw.strip().startswith("def "):
                continue
            match = re.search(func_pat, raw)
            name = match.group(1) or match.group(2) or match.group(3) or "?"
            in_func = True
            func_start = i
            func_name = name
            indent_level = len(raw) - len(raw.lstrip())

            if ext == ".py":
                brace_depth = 0
                continue

            # 对于大括号语言
            if "{" in stripped:
                brace_depth += stripped.count("{") - stripped.count("}")

            if brace_depth == 0 and "{" not in stripped:
                # 可能在下一行
                pass
            continue

        if not in_func:
            continue

        # 检测函数结束
        if ext == ".py":
            # Python: 根据缩进判断
            if stripped == "":
                continue
            current_indent = len(raw) - len(raw.lstrip())
            if current_indent <= indent_level:
                functions.append({
                    "name": func_name, "start": func_start, "end": i - 1
                })
                in_func = False
                func_start = 0
                func_name = "?"
                indent_level = -1
                # 当前行可能是下一个函数的开始
                if re.search(func_pat, raw):
                    m = re.search(func_pat, raw)
                    name = m.group(1) or m.group(2) or m.group(3) or "?"
                    in_func = True
                    func_start = i
                    func_name = name
                    indent_level = current_indent
        else:
            # 大括号语言
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                functions.append({
                    "name": func_name, "start": func_start, "end": i
                })
                in_func = False
                func_start = 0
                func_name = "?"
                brace_depth = 0

    # 处理最后一个函数（文件末尾）
    if in_func:
        functions.append({
            "name": func_name, "start": func_start, "end": len(lines)
        })

    return functions


def _extract_classes(lines: list, class_pat) -> list:
    """提取 Python 类的起止行号。"""
    classes = []
    if not class_pat:
        return classes

    in_class = False
    class_start = 0
    class_name = "?"
    class_indent = -1

    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()

        if re.search(class_pat, raw) and not in_class:
            m = re.search(class_pat, raw)
            in_class = True
            class_start = i
            class_name = m.group(1)
            class_indent = len(raw) - len(raw.lstrip())
            continue

        if not in_class:
            continue

        if stripped == "":
            continue

        current_indent = len(raw) - len(raw.lstrip())
        if current_indent <= class_indent:
            classes.append({
                "name": class_name, "start": class_start, "end": i - 1
            })
            in_class = False
            class_start = 0
            class_name = "?"
            class_indent = -1

            if re.search(class_pat, raw):
                m = re.search(class_pat, raw)
                in_class = True
                class_start = i
                class_name = m.group(1)
                class_indent = current_indent

    if in_class:
        classes.append({
            "name": class_name, "start": class_start, "end": len(lines)
        })

    return classes


def _compute_cyclomatic(fn_body: list, ext: str) -> int:
    """计算函数的近似圈复杂度。"""
    keywords = _CYCLOMATIC_KEYWORDS.get(ext, [r"\bif\b", r"\bfor\b", r"\bwhile\b"])
    count = 1  # 基础路径
    for line in fn_body:
        # 跳过注释
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        for kw in keywords:
            if re.search(kw, stripped):
                count += 1
    return count


def _compute_nesting(fn_body: list, ext: str) -> int:
    """计算函数内的最大嵌套深度。"""
    max_depth = 0
    current_depth = 0

    if ext == ".py":
        indent_step = None
        for line in fn_body:
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())
            if indent_step is None and indent > 0:
                indent_step = indent
            if indent_step and indent_step > 0:
                current_depth = indent // indent_step
                max_depth = max(max_depth, current_depth)
    else:
        for line in fn_body:
            current_depth += line.count("{") - line.count("}")
            max_depth = max(max_depth, current_depth)

    return max_depth


def _count_params(func_def_line: str, ext: str) -> int:
    """计算函数定义的参数数量。"""
    # 提取括号内的内容
    m = re.search(r"\((.*?)\)", func_def_line)
    if not m:
        return 0
    params_str = m.group(1).strip()
    if not params_str or params_str in ("void",):
        return 0

    # 简单按逗号分割（不完全准确，但对大多数情况有效）
    depth = 0
    count = 1
    for ch in params_str:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def _has_docstring(fn_body: list) -> bool:
    """检查函数/类是否有文档字符串。"""
    if len(fn_body) < 2:
        return False

    # 跳过装饰器
    idx = 0
    while idx < len(fn_body) and fn_body[idx].strip().startswith("@"):
        idx += 1

    # 找到函数定义行
    if idx >= len(fn_body):
        return False
    idx += 1  # 下一行

    # 检查下一行是否是文档字符串
    if idx < len(fn_body):
        stripped = fn_body[idx].strip()
        if (stripped.startswith('"""') or stripped.startswith("'''") or
                stripped.startswith('r"""') or stripped.startswith("r'''")):
            return True
    return False


def _analyze_python_ast(tree: ast.AST, file_path: str, strictness: str) -> list:
    """使用 Python AST 进行深度分析。"""
    issues = []

    for node in ast.walk(tree):
        # ── 函数分析 ──
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 检查是否有 return 语句但可能返回 None
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            has_empty_return = any(
                r.value is None for r in returns
            ) if returns else False

            if has_empty_return and len(returns) > 1:
                issues.append({
                    "line": node.lineno, "severity": "info",
                    "category": "返回一致性",
                    "message": (
                        f"`{node.name}()` 混合了有/无返回值，"
                        f"建议统一返回类型"
                    )
                })

            # 检查过多 local 变量
            local_vars = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    local_vars.add(child.id)
            if len(local_vars) > 15:
                issues.append({
                    "line": node.lineno, "severity": "info",
                    "category": "局部变量",
                    "message": (
                        f"`{node.name}()` 有 {len(local_vars)} 个局部变量，"
                        f"建议控制在 15 以内"
                    )
                })

        # ── 类分析 ──
        if isinstance(node, ast.ClassDef):
            # 数直接子方法
            direct_methods = [
                item for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if len(direct_methods) > 20 and strictness != "lenient":
                issues.append({
                    "line": node.lineno, "severity": "warning",
                    "category": "类大小",
                    "message": (
                        f"类 `{node.name}` 有 {len(direct_methods)} 个方法，"
                        f"可能承担太多职责，考虑拆分"
                    )
                })

        # ── Try/Except ──
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is None:
                    # 裸 except (已由模式匹配检测)
                    pass
                elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                    pass  # 已检测

        # ── Import 分析 ──
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            pass  # 导入分析可以后续添加

    return issues


# ═══════════════════════════════════════════════════════════════
# 7. 报告构建
# ═══════════════════════════════════════════════════════════════

def _build_report(
    file_path: str,
    lang_name: str,
    strictness: str,
    total_lines: int,
    non_empty: int,
    blank_lines: int,
    comment_lines: int,
    func_count: int,
    class_count: int,
    issues: list,
    min_comment_ratio: float,
) -> str:
    """构建结构化的 Markdown 审查报告。"""
    basename = os.path.basename(file_path)

    # 分类统计
    critical = [i for i in issues if i["severity"] == "critical"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]

    comment_ratio = comment_lines / max(non_empty, 1)

    # ── 头部 ──
    parts = [
        f"# 📋 代码审查报告",
        f"",
        f"**文件**: `{basename}` | **语言**: {lang_name} | "
        f"**严格度**: `{strictness}`",
        f"",
        "---",
        f"",
        f"## 📊 文件统计",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总行数 | {total_lines} |",
        f"| 非空行 | {non_empty} |",
        f"| 空行 | {blank_lines} |",
        f"| 注释行 | {comment_lines} ({comment_ratio:.1%}) |",
        f"| 函数/方法 | {func_count} |",
        f"| 类 | {class_count} |",
        f"",
    ]

    # ── 问题概览 ──
    parts.append("---")
    parts.append("")
    parts.append("## 🔍 发现问题")
    parts.append("")

    total_issues = len(issues)
    if total_issues == 0:
        parts.append("✅ **恭喜！未发现任何问题。**")
    else:
        parts.append(
            f"共发现 **{total_issues}** 个问题："
            f" 🔴 {len(critical)} 严重 · "
            f"🟠 {len(warnings)} 警告 · "
            f"ℹ️ {len(infos)} 提示"
        )
        parts.append("")

    # ── 严重问题 ──
    if critical:
        parts.append("### 🔴 严重问题")
        parts.append("")
        for item in sorted(critical, key=lambda x: x["line"])[:15]:
            loc = f"L{item['line']}" if item["line"] > 0 else ""
            parts.append(
                f"- **{loc}** `[{item['category']}]` {item['message']}"
            )
        if len(critical) > 15:
            parts.append(
                f"  > … 及其他 {len(critical) - 15} 个严重问题"
            )
        parts.append("")

    # ── 警告 ──
    if warnings:
        parts.append("### 🟠 警告")
        parts.append("")
        for item in sorted(warnings, key=lambda x: x["line"])[:15]:
            loc = f"L{item['line']}" if item["line"] > 0 else ""
            parts.append(
                f"- **{loc}** `[{item['category']}]` {item['message']}"
            )
        if len(warnings) > 15:
            parts.append(
                f"  > … 及其他 {len(warnings) - 15} 个警告"
            )
        parts.append("")

    # ── 提示 ──
    if infos:
        parts.append("### ℹ️ 提示")
        parts.append("")
        for item in sorted(infos, key=lambda x: x["line"])[:10]:
            loc = f"L{item['line']}" if item["line"] > 0 else ""
            parts.append(
                f"- **{loc}** `[{item['category']}]` {item['message']}"
            )
        if len(infos) > 10:
            parts.append(
                f"  > … 及其他 {len(infos) - 10} 个提示"
            )
        parts.append("")

    # ── 评分 ──
    parts.append("---")
    parts.append("")
    parts.append("## 🏆 综合评分")
    parts.append("")

    score, grade = _compute_score(critical, warnings, infos, strictness)
    parts.append(f"**评分**: {score} 分 → **{grade}**")
    parts.append(f"")
    parts.append(
        f"*基于 {total_issues} 个发现，严格度 `{strictness}`，"
        f"权重：严重×5 + 警告×2 + 提示×1*"
    )
    parts.append("")

    # ── 改进建议 ──
    parts.append("---")
    parts.append("")
    parts.append("## 💡 改进建议")
    parts.append("")

    suggestions = _generate_suggestions(
        critical, warnings, infos, comment_ratio,
        min_comment_ratio, func_count, non_empty
    )

    if suggestions:
        for s in suggestions:
            parts.append(f"- {s}")
    else:
        parts.append("✅ 代码质量良好，暂无改进建议。")

    parts.append("")
    return "\n".join(parts)


def _compute_score(critical: list, warnings: list, infos: list,
                   strictness: str) -> tuple:
    """计算加权评分并返回 (score, grade)。"""
    weighted = len(critical) * 5 + len(warnings) * 2 + len(infos) * 1

    # 严格模式下扣分更重
    if strictness == "strict":
        weighted = int(weighted * 1.3)
    elif strictness == "lenient":
        weighted = int(weighted * 0.7)

    max_score = 100
    score = max(0, max_score - weighted)

    if score >= 95:
        grade = "⭐ A+ (卓越)"
    elif score >= 85:
        grade = "✅ A (非常干净)"
    elif score >= 75:
        grade = "👍 B (良好)"
    elif score >= 60:
        grade = "📝 C (需要改进)"
    elif score >= 40:
        grade = "⚠️ D (问题较多)"
    else:
        grade = "🔴 F (需要重写)"

    return score, grade


def _generate_suggestions(
    critical: list, warnings: list, infos: list,
    comment_ratio: float, min_comment_ratio: float,
    func_count: int, non_empty: int,
) -> list:
    """生成改进建议列表。"""
    suggestions = []

    if critical:
        critical_cats = set(i["category"] for i in critical)
        if any("安全" in c for c in critical_cats):
            suggestions.append(
                "🔒 **安全优先**: 立即处理硬编码密钥和危险函数调用"
            )
        if any("异常" in c.lower() for c in critical_cats) or \
           any("裸 except" in i["message"] for i in critical):
            suggestions.append(
                "⚠️ **异常处理**: 将裸 `except:` 替换为具体的异常类型"
            )

    if comment_ratio < min_comment_ratio and non_empty > 50:
        suggestions.append(
            f"📝 **增加注释**: 当前注释率 {comment_ratio:.1%}，"
            f"建议至少达到 {min_comment_ratio:.0%}"
        )

    # 函数长度问题
    long_funcs = [
        i for i in warnings + infos
        if i["category"] == "函数长度"
    ]
    if long_funcs:
        suggestions.append(
            f"✂️ **拆分长函数**: {len(long_funcs)} 个函数超过建议长度，"
            f"考虑提取子函数"
        )

    # 复杂度问题
    complex_funcs = [
        i for i in warnings + infos
        if i["category"] == "圈复杂度"
    ]
    if complex_funcs:
        suggestions.append(
            f"🧩 **降低复杂度**: {len(complex_funcs)} 个函数圈复杂度过高，"
            f"考虑使用早返回、提取条件逻辑等重构手段"
        )

    # 文档字符串
    missing_docs = [
        i for i in infos
        if i["category"] == "文档字符串"
    ]
    if missing_docs:
        suggestions.append(
            f"📖 **补充文档**: {len(missing_docs)} 个函数/类缺少文档字符串"
        )

    # TODO/FIXME
    todos = [
        i for i in warnings
        if "TODO" in i.get("message", "") or "FIXME" in i.get("message", "")
    ]
    if todos:
        suggestions.append(
            f"📋 **处理标记**: {len(todos)} 个 TODO/FIXME 标记待处理"
        )

    # 嵌套深度
    deep_nests = [
        i for i in infos + warnings
        if i["category"] == "嵌套深度"
    ]
    if deep_nests:
        suggestions.append(
            "🪜 **减少嵌套**: 使用早返回 (guard clauses) 或提取方法降低嵌套"
        )

    return suggestions


# ═══════════════════════════════════════════════════════════════
# 8. 工具执行入口
# ═══════════════════════════════════════════════════════════════

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "code_review":
        file_path = args.get("file_path", "")
        strictness = args.get("strictness", "normal")

        # 安全：路径必须在工作区内
        full_path = os.path.join(context.project_root, file_path)
        full_path = os.path.normpath(full_path)

        if not os.path.exists(full_path):
            return f"❌ 文件不存在：`{file_path}`"

        # 确保路径在工作区内
        project_root = os.path.normpath(context.project_root)
        if not full_path.startswith(project_root):
            return f"❌ 安全限制：不允许访问工作区外的文件"

        context.logger.info(
            f"Reviewing {file_path} (strictness={strictness})"
        )

        # 更新统计
        s = context.storage
        s["reviews_count"] = s.get("reviews_count", 0) + 1

        return _review_file(full_path, strictness)

    return f"Unknown tool: {tool_name}"


# ═══════════════════════════════════════════════════════════════
# 9. 生命周期钩子
# ═══════════════════════════════════════════════════════════════

def on_agent_init(context):
    """初始化审查计数器和统计。"""
    context.storage["reviews_count"] = 0
    context.storage["plugin_started"] = datetime.now().isoformat()
    context.storage["total_issues_found"] = 0
    context.logger.info("Code Reviewer v2.0.0 loaded — 全面代码审查已就绪！")


def on_agent_shutdown(context):
    """会话结束时输出统计。"""
    reviews = context.storage.get("reviews_count", 0)
    issues_found = context.storage.get("total_issues_found", 0)
    context.logger.info(
        f"Code Reviewer shutting down. "
        f"Reviewed {reviews} file(s), found ~{issues_found} issue(s) this session."
    )


def on_task_start(task_text: str, context):
    """检测用户是否请求代码审查。"""
    keywords = [
        "审查", "review", "检查代码", "code review", "代码质量",
        "代码规范", "代码风格", "lint", "代码检查"
    ]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Code review task detected: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成时记录审查计数。"""
    pass  # 保持轻量
