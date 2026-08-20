# Vibe Coding Agent - 工具定义
# Copyright (c) 2026 xingluosama

BUILTIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容。可指定行范围只读取需要的代码片段，节省 token。⚠️ 全量读取 >100KB 文件默认被拒绝（返回「文件过大，仅能部分读取」），须用 start_line/end_line 按范围读取，或请求用户开启全量读取开关。大文件先用 search_large_file / surgical_scan 定位行号。📷 也支持读取图片（png/jpg/webp 等）返回视觉描述：需在设置中开启视觉 API 并配置 provider（openai_compatible/anthropic/llama_cpp）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始），可选。用于只读取代码片段，节省 token。"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（含），可选。配合 start_line 实现片段读取。"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖文件。覆盖前建议先调用 read_file 备份原内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容"
                    }
                },
                "required": ["path", "content"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "替换文件中的指定文本片段。old_str 必须精确匹配文件中唯一一处。若匹配多处则报错，需提供更多上下文以唯一确定。用于针对性修改，避免重写整个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "要被替换的原始文本片段，必须与文件中的内容精确匹配（含缩进和换行）"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "替换后的新文本片段"
                    }
                },
                "required": ["path", "old_str", "new_str"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容，用于了解项目结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，相对于工作区根目录。默认 '.' 表示根目录。"
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": "在文件中搜索匹配的文本模式。仅适合小型项目全局搜索（结果上限 50 条，不支持正则）。大文件请用 search_large_file 或 search_files（需先 index_workspace）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "要搜索的文本或正则表达式"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索范围，可以是文件路径或目录。默认 '.' 搜索整个项目。"
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除文件或目录。不可逆操作，执行前应请求用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件或目录路径"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exec_cmd",
            "description": "执行 shell 命令并返回输出。禁止执行 sudo、rm -rf / 等危险操作。对不确定的命令先加 --dry-run 预览。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                        "default": 30
                    }
                },
                "required": ["command"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "init_project",
            "description": "脚手架初始化新项目，自动创建目录结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "项目类型，如 python、web、node 等"
                    },
                    "name": {
                        "type": "string",
                        "description": "项目名称"
                    }
                },
                "required": ["type", "name"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "install_dependency",
            "description": "安装项目依赖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "包名，如 flask、requests"
                    },
                    "manager": {
                        "type": "string",
                        "description": "包管理器，如 pip、npm。默认自动检测。"
                    }
                },
                "required": ["package"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "提交所有变更到 Git 仓库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "提交信息，使用约定式提交格式，如 feat: add user auth"
                    }
                },
                "required": ["message"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提问或请求确认。当需要用户做出选择、澄清需求、或确认危险操作时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "向用户提出的问题。使用 Markdown 格式，用 ## 标题突出要点。"
                    }
                },
                "required": ["question"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": "标记任务完成。完成后会自动将任务摘要和代码路径记录到 .agent_history.json。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "任务完成总结，包括创建/修改了哪些文件、实现了什么功能"
                    },
                    "code_path": {
                        "type": "string",
                        "description": "本次任务涉及的主要代码路径或目录"
                    }
                },
                "required": ["summary"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息，适用于需要最新数据的场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "用系统默认程序打开文件。用户说「打开某个文件」时调用此工具。支持所有常见文件类型（图片、文档、网页等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，相对于工作区根目录"
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": "读取系统剪贴板中的文本内容。用户说「读取剪贴板」「粘贴」「看看剪贴板里有什么」时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_clipboard",
            "description": "将文本写入系统剪贴板。用户说「复制到剪贴板」「拷贝这段文字」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要写入剪贴板的文本内容"
                    }
                },
                "required": ["text"],
                "additionalProperties": False
            }
        }
    },
    {"type": "function", "function": {"name": "index_workspace", "description": "扫描并索引工作区（或指定目录）内的文件内容，建立 SQLite FTS5 全文索引。支持增量更新：仅重新索引 size/mtime 变化的文件。索引后可用 search_files 做毫秒级内容精确检索。⚠️ 使用时机：① 需要反复检索工作区文件内容（代码库、日志、文档）时先建立索引；② 检索前若不确定索引是否最新，重新调用本工具即可（自动增量）。", "parameters": {"type": "object", "properties": {"directory": {"type": "string", "description": "要扫描的目录。留空则扫描当前工作区根目录 (project_root)。支持绝对路径或相对路径。"}, "include_patterns": {"type": "array", "items": {"type": "string"}, "description": "只索引匹配的文件名 glob 模式，如 ['*.py','*.md','*.log']。留空表示索引所有文本文件（二进制自动跳过）。"}, "exclude_dirs": {"type": "array", "items": {"type": "string"}, "description": "跳过的目录名（按目录名匹配，任意层级）。默认排除 .git、node_modules、__pycache__、.venv、venv、dist、build、.idea、.vscode、output、indexes。"}, "max_file_mb": {"type": "number", "description": "内容索引的文件大小上限（MB）。超过此大小的文件只登记文件名（仍可被 find_files 找到），不索引内容。默认 256。", "default": 256}, "force": {"type": "boolean", "description": "为 true 时忽略 mtime/size 缓存，强制重新索引全部文件。默认 false。", "default": False}}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_files", "description": "在已索引的工作区文件中执行内容精确检索：默认按完整短语（连续字面）匹配，返回每个命中文件的路径、精确行号和上下文。⚠️ 使用时机：① 需要知道『哪个文件、哪一行』包含某段代码/配置/日志文本时；② 重复性内容检索（比内置 search_in_files 快得多）。若索引为空会提示，可先调用 index_workspace 建立索引；单个超大文件（如 1GB 日志）请改用 search_large_file。", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "要检索的文本。默认作为完整短语（连续出现）匹配，如 'api_key'、'def main'。"}, "path": {"type": "string", "description": "限定检索范围：目录或具体文件路径。留空检索全部已索引文件。"}, "file_pattern": {"type": "string", "description": "按文件名 glob 过滤，如 '*.py'、'*.log'。留空不过滤。"}, "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 false。", "default": False}, "exact_phrase": {"type": "boolean", "description": "true 表示完整短语连续匹配（推荐）；false 表示关键词 AND 匹配。默认 true。", "default": True}, "top_k": {"type": "integer", "description": "最多返回的命中块数量，默认 10，最大 50。", "default": 10}, "max_lines_per_file": {"type": "integer", "description": "每个文件最多展示的命中行数，默认 5。", "default": 5}, "line_context": {"type": "integer", "description": "每个命中行附带显示的上下文行数（前后各 N 行），默认 1。", "default": 1}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "find_files", "description": "按文件名/路径模糊检索工作区文件（支持 glob 通配符 * ?）。无论文件是否索引过内容都能找到（含二进制、超大文件）。⚠️ 使用时机：需要定位『文件名像什么』的文件时，如 find_files('*config*')。", "parameters": {"type": "object", "properties": {"name_pattern": {"type": "string", "description": "文件名/路径 glob 模式，支持 * 和 ?，如 '*test*'、'*.py'、'config.json'。"}, "path": {"type": "string", "description": "限定搜索目录。留空搜索整个索引根目录。"}, "top_k": {"type": "integer", "description": "最多返回条数，默认 30，最大 100。", "default": 30}}, "required": ["name_pattern"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_large_file", "description": "对单个超大文件（最高 1GB+）执行流式精确检索，无需建索引、内存占用恒定。逐行扫描并返回精确行号、行内容与上下文，支持正则模式。⚠️ 使用时机：日志/数据/导出文件等超大文件的即时检索；文件未索引或不想建立索引时。小文件（<10MB）也可用，但已索引文件建议用 search_files。", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "目标文件路径（绝对路径或相对工作区路径）。"}, "query": {"type": "string", "description": "检索文本。默认按字面精确匹配；regex=true 时按正则匹配。"}, "regex": {"type": "boolean", "description": "是否将 query 作为正则表达式，默认 false。", "default": False}, "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 false。", "default": False}, "line_context": {"type": "integer", "description": "每个命中行附带显示的上下文行数（前后各 N 行），默认 2，最大 10。", "default": 2}, "max_matches": {"type": "integer", "description": "最多返回命中数（达到即提前停止扫描），默认 30，最大 100。", "default": 30}, "encoding": {"type": "string", "description": "文件编码。留空自动探测（utf-8 → gbk → latin-1）。可显式指定如 'utf-8'、'gbk'。"}}, "required": ["path", "query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "workspace_index_status", "description": "查看工作区文件索引的统计信息：索引根目录、文件数、内容索引状态分布、索引块数、总字符数、数据库大小、各扩展名分布等。⚠️ 使用时机：不确定索引是否建立、或想知道哪些文件已索引时先调用本工具。", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "clear_workspace_index", "description": "清理工作区文件索引。可全部清空，或按文件/目录清除特定记录的索引。", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要清除索引的文件或目录路径。留空清空全部索引。"}}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "surgical_replace", "description": "「分子手术刀」精确修改替换超大文件（最大 1GB）中的某一行。支持三种定位模式：行号模式、搜索模式、搜索+行号双重定位。支持五种操作类型：replace（替换）、insert_before（前插）、insert_after（后插）、delete（删除）、replace_all（全量替换）。采用流式读写，处理 1GB 文件时内存占用 < 50MB。dry_run 模式可预览修改效果而不实际更改文件。", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "要操作的文件路径（相对于工作区根目录）"}, "line_number": {"type": "integer", "description": "目标行号（从 1 开始）。与 old_content 可二选一或同时指定。同时指定时：只在 line_number 行匹配 old_content，双重保险。"}, "old_content": {"type": "string", "description": "要匹配的原始行内容。支持精确文本匹配或正则表达式。与 line_number 可二选一或同时指定（双重保险）。注意：仅在单行内匹配，不跨行。如需精确匹配整行，可在内容前后加 ^ 和 $ 锚点并启用 use_regex。"}, "new_content": {"type": "string", "description": "替换后的新内容。可以是单行或多行（含 \\n）。delete 模式时忽略此参数。"}, "mode": {"type": "string", "description": "操作模式：'replace'（默认）、'insert_before'、'insert_after'、'delete'、'replace_all'", "default": "replace"}, "use_regex": {"type": "boolean", "description": "是否将 old_content 作为正则表达式解析。默认 false（精确文本匹配）。", "default": False}, "count": {"type": "integer", "description": "替换次数（仅搜索模式有效）：1=只替换第一个匹配（默认），-1=替换所有匹配，N=替换前 N 个匹配。", "default": 1}, "dry_run": {"type": "boolean", "description": "预览模式。为 true 时只显示将要做的更改（含上下文），不实际修改文件。强烈建议在正式操作前先用 dry_run 确认。", "default": False}, "context_lines": {"type": "integer", "description": "在 dry_run 预览中显示目标行上下各多少行。默认 2。", "default": 2}, "backup": {"type": "boolean", "description": "是否在修改前自动备份原文件为 .bak 后缀。默认 false。", "default": False}, "encoding": {"type": "string", "description": "文件编码。默认 'utf-8'。常见值：'utf-8'、'gbk'、'latin-1'。", "default": "utf-8"}}, "required": ["file_path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "surgical_scan", "description": "「手术前扫描」在超大文件中搜索匹配的行，帮助定位目标。返回匹配行的行号、内容预览和上下文。可指定搜索范围。支持正则表达式。适合在「下刀」前确认目标位置。", "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "要扫描的文件路径（相对于工作区根目录）"}, "pattern": {"type": "string", "description": "搜索模式。支持精确文本或正则表达式（use_regex=true 时）。"}, "use_regex": {"type": "boolean", "description": "是否将 pattern 作为正则表达式解析。默认 false。", "default": False}, "line_start": {"type": "integer", "description": "搜索起始行号（从 1 开始），默认从文件开头。"}, "line_end": {"type": "integer", "description": "搜索结束行号（含），默认到文件末尾。"}, "context_lines": {"type": "integer", "description": "每个匹配行周围显示的上下文行数。默认 1。", "default": 1}, "max_matches": {"type": "integer", "description": "最多返回多少个匹配。默认 20，最大 200。", "default": 20}, "encoding": {"type": "string", "description": "文件编码。默认 'utf-8'。", "default": "utf-8"}}, "required": ["file_path", "pattern"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "index_context", "description": "将文本内容或当前对话历史添加到检索引擎的倒排索引中，以便后续用 search_context 进行精确检索。支持两种模式：直接传入 content 字符串；或传入 source='conversation' 自动索引最近 N 轮对话。适用于构建超长上下文的可搜索知识库。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "要索引的文本内容。如果为空，则使用 source 参数自动获取内容。可以是代码、文档、日志、对话记录等任意文本。"}, "source": {"type": "string", "description": "数据来源标签，用于过滤搜索结果。例如 'conversation', 'codebase', 'documentation', 'api_docs'。默认 'manual'。", "default": "manual"}, "title": {"type": "string", "description": "文档标题，可选。用于结果展示和排序加权。"}, "chunk_size": {"type": "integer", "description": "分块大小（字符数），默认 500。越小检索越精确但可能丢失上下文。", "default": 500}, "chunk_overlap": {"type": "integer", "description": "块重叠字符数，默认 100。防止关键信息被截断在块边界。", "default": 100}}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_context", "description": "在已索引的上下文库中执行 BM25 精确检索，支持多关键词联合查询、短语精确匹配奖励、结果上下文扩展（返回匹配块的前后邻接块）。⚠️ 使用时机：① 用户问题涉及早期对话/历史工具输出，而当前上下文中没有这些内容时；② 需要回忆很久以前讨论过的细节（约定、配置、决策）时。此类情况应优先检索而不是猜测，也不要重复执行旧工具。若索引为空会返回提示，可先用 index_context 建立索引。", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索查询，支持自然语言或关键词。多个词空格分隔为 AND 逻辑。"}, "top_k": {"type": "integer", "description": "返回最相关的结果数量，默认 5，最大 20。", "default": 5}, "min_score": {"type": "number", "description": "最低相关性分数阈值（0.0~∞），低于此分数的结果会被过滤。默认 0.1。设为 0 返回所有匹配。", "default": 0.1}, "source_filter": {"type": "string", "description": "按来源过滤，只搜索指定 source 标签的内容。如 'codebase' 只搜索代码索引。留空则搜索全部。"}, "expand_context": {"type": "boolean", "description": "是否扩展上下文窗口。开启后每个匹配块会附带前后各 1 个邻接块，保留完整语义上下文。默认 true。", "default": True}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "clear_index", "description": "清空检索引擎中的所有已索引内容。可按 source 过滤清除特定来源，或清空全部。", "parameters": {"type": "object", "properties": {"source_filter": {"type": "string", "description": "只清除指定 source 的索引。留空则清空全部索引。"}}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "index_stats", "description": "查看检索引擎的统计信息：已索引文档数、总字符数、各来源分布、词库大小等。⚠️ 使用时机：不确定索引中是否有内容、或不知道有哪些可用来源时，先调用本工具确认，再决定是否检索或建立索引。", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "抓取指定 URL 的网页内容，提取纯文本后返回。适用于阅读在线文档、博客文章、API 响应、新闻页面等。会自动去除 HTML 标签、脚本、样式，只保留正文文本。用户说「打开这个网页」「抓取这个链接」「读取这个 URL 的内容」时调用。", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "要抓取的网页 URL。必须是完整的 http:// 或 https:// 地址。例如 'https://example.com/article'"}, "max_chars": {"type": "integer", "description": "最大返回字符数。超过此限制的文本会被截断。默认 8000，范围 500-50000。", "default": 8000}, "timeout": {"type": "integer", "description": "请求超时秒数，默认 15，范围 5-60。", "default": 15}}, "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "web_extract_links", "description": "提取指定网页中所有的超链接（<a href>），返回链接列表。自动将相对链接转为绝对 URL，并按「同域内链」和「外链」分组。适用于：浏览目录页/索引页后挑感兴趣的链接用 web_fetch 深入抓取。典型用法：先用 web_extract_links 看有哪些子页面，再对感兴趣的 URL 调用 web_fetch 获取正文。", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "要提取链接的网页 URL。必须是完整的 http:// 或 https:// 地址。例如 'https://example.com/docs/'"}, "same_domain_only": {"type": "boolean", "description": "是否只返回与当前 URL 同域名的链接。默认 false（同时返回内链和外链）。设为 true 可过滤掉外部链接，减少噪音。", "default": False}, "max_links": {"type": "integer", "description": "最大返回链接数，默认 50，范围 10-200。超出后按链接文本质量排序，优先返回有意义的链接。", "default": 50}, "timeout": {"type": "integer", "description": "请求超时秒数，默认 15，范围 5-60。", "default": 15}}, "required": ["url"], "additionalProperties": False}}}
]