# NORP Vibe Coding Agent — Plugin Development Guide

**Version 1.0 — August 2026**  
Copyright © 2026 xingluosama. All rights reserved.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Plugin Structure](#2-plugin-structure)
   - [2.1 Single-File Plugin](#21-single-file-plugin)
   - [2.2 Package Plugin with manifest.json](#22-package-plugin-with-manifestjson)
3. [Plugin Metadata](#3-plugin-metadata)
4. [Tool Registration](#4-tool-registration)
   - [4.1 OpenAI Function Schema Format](#41-openai-function-schema-format)
   - [4.2 Complete TOOLS Example](#42-complete-tools-example)
5. [The execute() Function](#5-the-execute-function)
   - [5.1 Signature](#51-signature)
   - [5.2 Dispatching Pattern](#52-dispatching-pattern)
   - [5.3 Return Value](#53-return-value)
6. [Lifecycle Hooks](#6-lifecycle-hooks)
   - [6.1 Hook Categories](#61-hook-categories)
   - [6.2 L1 — Lifecycle Hooks](#62-l1--lifecycle-hooks)
   - [6.3 L2 — Task Hooks](#63-l2--task-hooks)
   - [6.4 L3 — Step Hooks (Mutating)](#64-l3--step-hooks-mutating)
   - [6.5 L4 — Streaming Event Hooks](#65-l4--streaming-event-hooks)
   - [6.6 Mutating vs Non-Mutating Hooks](#66-mutating-vs-non-mutating-hooks)
7. [PluginContext API](#7-plugincontext-api)
   - [7.1 Attributes](#71-attributes)
   - [7.2 SimpleLogger](#72-simplelogger)
8. [Security System](#8-security-system)
   - [8.1 AST Source Audit](#81-ast-source-audit)
   - [8.2 Import Restriction](#82-import-restriction)
   - [8.3 Permission Declaration](#83-permission-declaration)
   - [8.4 Resource Limits](#84-resource-limits)
   - [8.5 Security Configuration Summary](#85-security-configuration-summary)
9. [manifest.json Reference](#9-manifestjson-reference)
10. [Step-by-Step Tutorial](#10-step-by-step-tutorial)
    - [10.1 Create the File](#101-create-the-file)
    - [10.2 Declare Metadata](#102-declare-metadata)
    - [10.3 Register a Tool](#103-register-a-tool)
    - [10.4 Implement execute()](#104-implement-execute)
    - [10.5 Add Lifecycle Hooks](#105-add-lifecycle-hooks)
    - [10.6 Test Your Plugin](#106-test-your-plugin)
11. [Best Practices](#11-best-practices)
12. [Troubleshooting](#12-troubleshooting)
13. [Appendix](#13-appendix)
    - [13.1 Complete Hook Reference](#131-complete-hook-reference)
    - [13.2 Dangerous Pattern Registry](#132-dangerous-pattern-registry)
    - [13.3 PluginContext Quick Reference](#133-plugincontext-quick-reference)
    - [13.4 Official Plugins Source Reference](#134-official-plugins-source-reference)

---

## 1. Overview

The NORP Vibe Coding Agent plugin system allows developers to extend the Agent's capabilities by registering custom tools, listening to lifecycle hooks, and accessing session context. Plugins are loaded at application startup from configurable plugin directories and run within a security-audited environment.

### What Plugins Can Do

- Register custom OpenAI function-schema tools that the AI Agent can invoke.
- Listen to **15 lifecycle hooks** across 4 layers (lifecycle, task, step, streaming).
- Read read-only session context (project root, config snapshot, token usage).
- Persist arbitrary data in per-plugin storage across hooks within a session.
- Log messages via a built-in per-plugin logger.
- Mutate data flow through designated hooks (`before_step`, `before_tool_call`, `after_tool_call`).

### What Plugins Cannot Do

- Execute arbitrary shell commands (blocked by security audit).
- Import dangerous modules such as `subprocess`, `ctypes`, or `socket` (blocked by import restrictor).
- Access files outside the workspace (path boundary enforcement).
- Modify system-level configuration or other plugins' storage.
- Crash the Agent — all hook exceptions are silently caught.

---

## 2. Plugin Structure

Plugins can be placed in any directory listed under `plugin_dirs` in `config.json`. The `PluginManager` discovers plugins by scanning these directories and supports two layout styles:

### 2.1 Single-File Plugin

The simplest form. A single `.py` file placed directly in a plugin directory. The file name (without `.py`) becomes the default plugin name.

**Directory layout:**

```
plugins/
  my_tool.py          # single-file plugin
```

**Notes:**
- The file must **not** be named `__init__.py` (these are skipped).
- If the file defines `PLUGIN_NAME`, that value takes priority over the file name.

### 2.2 Package Plugin with manifest.json

For more complex plugins, use a dedicated subdirectory containing a `manifest.json` and an entry-point `.py` file.

**Directory layout:**

```
plugins/
  fancy_tool/
    manifest.json       # metadata (required for detection)
    plugin.py            # entry point (default, can be customized)
```

The `manifest.json` file is **required** for the `PluginManager` to recognize the directory as a plugin package. Without it, the directory is silently skipped.

---

## 3. Plugin Metadata

Every plugin **must** declare its identity through module-level constants. These are read by `PluginManager._load_from_file()` after the module is imported.

### Required Constants

| Constant | Type | Default | Description |
|---|---|---|---|
| `PLUGIN_NAME` | `str` | *(required)* | Human-readable plugin name shown in the UI. |
| `PLUGIN_PUBLISHER` | `str` | *(required)* | Author or organization name. |
| `PLUGIN_VERSION` | `str` | `"0.0.0"` | Semantic version string. `manifest.json` takes priority if both present. |
| `PLUGIN_DESCRIPTION` | `str` | `""` | Short description shown in the plugin list UI. |

**Example:**

```python
PLUGIN_NAME = "My Awesome Tool"
PLUGIN_PUBLISHER = "Your Name"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Does something amazing with AI agent workflows."
```

> **Important:** If `manifest.json` provides a version or description, those values take priority over the module-level constants. Use `manifest.json` as the single source of truth for packaged plugins.

---

## 4. Tool Registration

Plugins expose functionality to the AI Agent by defining a `TOOLS` list. Each entry follows the OpenAI Function Calling schema format. The Agent uses these schema definitions to decide when and how to invoke your tool.

### 4.1 OpenAI Function Schema Format

Each tool entry has this structure:

```json
{
    "type": "function",
    "function": {
        "name": "my_tool_name",           // unique identifier
        "description": "What this tool does",  // helps the AI decide when to call
        "parameters": {
            "type": "object",
            "properties": {
                "param_name": {
                    "type": "string",     // string / integer / boolean / ...
                    "description": "What this parameter is for"
                }
            },
            "required": ["param_name"],    // list of required parameters
            "additionalProperties": false
        }
    }
}
```

### Tool Naming Rules

- Names must be **unique** across all plugins and built-in tools. Duplicate names cause a `RuntimeError`.
- Use `snake_case` naming: `generate_uuid`, `code_review`, `save_note`.
- Avoid names that conflict with the 14 built-in tools (`read_file`, `write_file`, `exec_cmd`, etc.).

### 4.2 Complete TOOLS Example

This example registers a simple echo tool:

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echoes back the input message. Useful for testing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to echo back"
                    },
                    "repeat": {
                        "type": "integer",
                        "description": "Number of times to repeat (default 1)",
                        "default": 1
                    }
                },
                "required": ["message"],
                "additionalProperties": false
            }
        }
    }
]
```

---

## 5. The execute() Function

When the AI Agent decides to call one of your tools, the `PluginManager` dispatches the call to your plugin's `execute()` function. This is the core interface between the Agent and your plugin.

### 5.1 Signature

```python
def execute(tool_name: str, args: dict, context: PluginContext) -> str:
```

**Parameters:**

- **`tool_name`** (`str`): The name of the tool being invoked. Use this to dispatch calls when your plugin registers multiple tools.
- **`args`** (`dict`): A dictionary of parameter values. Keys match the `"properties"` defined in your `TOOLS` schema. Required parameters are guaranteed to be present.
- **`context`** (`PluginContext`): A read-only context object providing access to project root, config snapshot, storage, and a logger. See [Chapter 7](#7-plugincontext-api) for details.

### 5.2 Dispatching Pattern

Best practice for multi-tool plugins:

```python
def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "my_tool_a":
        return _handle_tool_a(args, context)
    if tool_name == "my_tool_b":
        return _handle_tool_b(args)
    return f"Unknown tool: {tool_name}"
```

### 5.3 Return Value

The `execute()` function **must** return a string. This string is passed back to the AI model as the tool call result and becomes part of the conversation context.

**Tips:**

- Use **Markdown formatting** for readability (the frontend renders Markdown).
- Keep responses concise. Very long outputs consume tokens.
- On error, return a descriptive message prefixed with ❌ (e.g., `"❌ File not found: path/to/file"`).
- **Never** raise unhandled exceptions. They are caught but produce ugly tracebacks.

---

## 6. Lifecycle Hooks

Plugins can register callback functions for **15 lifecycle hooks** spanning 4 layers. The `PluginManager` automatically discovers hooks by scanning the module for functions whose names match the hook list. Simply define a function with the correct name and signature, and it will be called automatically.

### 6.1 Hook Categories

| Layer | Hooks | Purpose |
|---|---|---|
| **L1 — Lifecycle** | `on_agent_init`, `on_agent_shutdown` | Plugin initialization and cleanup |
| **L2 — Task** | `on_task_start`, `on_task_done`, `on_task_error`, `on_task_stopped`, `on_task_timeout` | Task-level events |
| **L3 — Step** | `before_step`, `after_step`, `before_tool_call`, `after_tool_call`, `on_user_input_required` | Per-step and tool-call events |
| **L4 — Streaming** | `on_reasoning`, `on_content`, `on_event`, `on_usage_update` | Real-time streaming events |

### 6.2 L1 — Lifecycle Hooks

#### `on_agent_init(context)`

Called once when the Agent starts. Use this to initialize storage, load data files, or set up counters. This is the ideal place for one-time setup.

```python
def on_agent_init(context):
    context.storage["counter"] = 0
    context.storage["session_start"] = time.time()
    context.logger.info("My plugin loaded!")
```

#### `on_agent_shutdown(context)`

Called once when the Agent shuts down. Use this to persist state, write summary statistics, or clean up resources.

```python
def on_agent_shutdown(context):
    total = context.storage.get("counter", 0)
    context.logger.info(f"Shutting down. Total operations: {total}")
```

### 6.3 L2 — Task Hooks

#### `on_task_start(task_text: str, context)`

Called when the user submits a new task. Receives the full task text. Use this to detect task keywords and prepare context.

#### `on_task_done(summary: str, final_reply: str, context)`

Called when a task completes successfully. Receives the task summary and the final AI reply. Use this to auto-save notes or record completion statistics.

#### `on_task_error(error_msg: str, context)`

Called when a task fails. Receives the error message. Use this to log errors or trigger recovery actions.

#### `on_task_stopped(context)`

Called when the user manually stops a running task.

#### `on_task_timeout(elapsed: float, context)`

Called when a task exceeds the configured timeout. Receives the elapsed seconds. Take care: this hook itself has a **5-second hard timeout** (`HOOK_TIMEOUT`).

### 6.4 L3 — Step Hooks (Mutating)

These hooks **can modify the data flow**. Their return values are used by the Agent.

#### `before_step(step: int, messages: list, context) → list | None`

Called before each ReAct reasoning step. Receives the current step number and the messages list being sent to the model. Return a modified list to alter the conversation context, or `None` to make no changes.

```python
def before_step(step: int, messages: list, context):
    """Inject a system reminder every 10 steps."""
    if step % 10 == 0:
        messages.append({"role": "system", "content": "Remember to save progress!"})
    return messages  # return modified list
```

#### `after_step(step: int, reasoning: str, content: str, tool_calls: list, context)`

Called after each ReAct step completes. **Non-mutating** — return value is ignored. Use this for analytics or logging.

#### `before_tool_call(tool_name: str, args: dict, context) → dict | None`

Called right before **any** tool (built-in or plugin) executes. Return a modified `args` dict to alter the parameters, or `None` to allow the call to proceed unchanged.

> **Important:** If no plugin registers this hook, the tool call is **never blocked** — the short-circuit optimizes for the common case.

```python
def before_tool_call(tool_name: str, args: dict, context):
    """Log and time every tool call."""
    context.storage["_tool_start"] = time.time()
    context.logger.info(f"Tool call: {tool_name}")
    return args  # pass through unchanged
```

#### `after_tool_call(tool_name: str, args: dict, result: str, context) → str | None`

Called after a tool completes. Receives the original `args` and the `result` string. Return a modified result string to alter what the AI sees, or `None` to leave it unchanged.

```python
def after_tool_call(tool_name: str, args: dict, result: str, context):
    """Anonymize sensitive data in tool results."""
    result = result.replace("API_KEY_12345", "***REDACTED***")
    return result
```

#### `on_user_input_required(question: str, context)`

Called when the Agent pauses and waits for user input (via `ask_user`). Use this to notify external systems or log pending questions.

### 6.5 L4 — Streaming Event Hooks

| Hook | Description |
|---|---|
| `on_reasoning(token: str, context)` | Called for each reasoning token (the AI's "inner monologue"). Fires frequently. |
| `on_content(token: str, context)` | Called for each content token in the AI's final response. Fires **very** frequently — keep handlers lightweight. |
| `on_event(event_type: str, data: str, context)` | Called for structured events. `event_type` is a single character prefix (`T`, `R`, `C`, `Q`, `D`, `E`, `U`, `F`). See the Event Stream Protocol. |
| `on_usage_update(usage: dict, context)` | Called when token usage statistics are updated. `usage` contains `input_tokens`, `output_tokens`, `tool_call_tokens`. |

### 6.6 Mutating vs Non-Mutating Hooks

Three hooks can **mutate** the data flow: `before_step`, `before_tool_call`, and `after_tool_call`. For these hooks, the **first non-None return value** from any plugin wins. All other hooks are **fire-and-forget** — their return values are ignored.

**Hook timeout:** Every hook callback has a hard timeout of **5 seconds** (`HOOK_TIMEOUT`). If a hook takes longer, the calling thread is abandoned (not joined) to prevent blocking the Agent. Keep hook implementations fast and non-blocking.

---

## 7. PluginContext API

Every hook and the `execute()` function receive a `PluginContext` object. This is your window into the Agent's environment. The context is read-only except for the `storage` dict and the `logger`.

### 7.1 Attributes

| Attribute | Type | Description |
|---|---|---|
| `project_root` | `str` | Absolute path of the current workspace / project root. All file operations should be scoped under this path. |
| `app_dir` | `str` | Application data directory (config, memories, logs). Use for plugin-private data files. |
| `config` | `dict` | Read-only snapshot of the current `config.json`. Updated before each hook call. |
| `storage` | `dict` | Per-plugin key-value store. Survives across hooks within one Agent session. Cleared on restart. |
| `logger` | `SimpleLogger` | A pre-configured logger that writes to both stdout and a `plugin.log` file in `app_dir`. |
| `current_step` | `int` | The current ReAct step number. Updated before each step hook. |
| `total_usage` | `dict` | Cumulative token usage: `{"input_tokens": N, "output_tokens": N, "tool_call_tokens": N}`. |

**Using storage:**

```python
# Store arbitrary data (survives across hooks)
context.storage["my_counter"] = context.storage.get("my_counter", 0) + 1

# Read config
max_steps = context.config.get("max_steps", 128)

# Build a safe file path
full_path = os.path.join(context.project_root, "relative/path.txt")
full_path = os.path.normpath(full_path)
```

### 7.2 SimpleLogger

Each plugin gets a `SimpleLogger` instance pre-configured with the plugin name. Messages are printed to stdout and appended to `plugin.log` in the app directory.

```python
# Available methods:
context.logger.info("Informational message")
context.logger.warn("Warning message")
context.logger.error("Error message")
context.logger.debug("Debug message")

# Output format:
# [2026-08-01 17:32:33] [INFO] [My Plugin] Informational message
```

---

## 8. Security System

Vibe Coding Agent employs a **multi-layer security system** to protect the host environment from potentially malicious or buggy plugins. All security features are individually togglable via `config.json` keys.

### 8.1 AST Source Audit

Before a plugin module is loaded, its source code is parsed into an **AST** (Abstract Syntax Tree) and walked to detect dangerous patterns. The audit checks for:

- **Shell/process execution:** `os.system()`, `subprocess.run()`, etc. — **CRITICAL**
- **Code execution:** `eval()`, `exec()`, `compile()`, `__import__()` — **CRITICAL**
- **Native code loading:** `ctypes`, `cffi` — **CRITICAL**
- **File deletion:** `os.remove()`, `shutil.rmtree()` — **WARNING**
- **Network access:** `socket`, `requests`, `urllib` — **WARNING**
- **Deserialization:** `pickle`, `marshal`, `yaml.unsafe_load()` — **WARNING**
- **System manipulation:** `sys.modules`, `sys.setprofile()`, `builtins` override — **WARNING**
- **Process termination:** `os._exit()`, `os.kill()` — **CRITICAL**

The `audit_level` configuration controls behavior:

| Value | Behavior |
|---|---|
| `"off"` | Skip source audit entirely. |
| `"warn"` *(default)* | Log warnings but allow loading. |
| `"block"` | Reject plugins with any CRITICAL findings. |

### 8.2 Import Restriction

After the source audit, import blockers are installed in `sys.meta_path` to prevent plugin modules from importing dangerous modules at runtime. The blockers use `inspect.stack()` to **only** restrict imports originating from plugin code (modules with names starting with `vibe_plugin_`).

Three restriction levels:

| Value | Behavior |
|---|---|
| `"off"` *(default)* | No import restrictions. |
| `"safe"` | Block all known dangerous modules (`subprocess`, `ctypes`, `socket`, `pickle`, `marshal`, `telnetlib`, `ftplib`, `smtplib`). |
| `"strict"` | Only allow a whitelist of safe modules (`json`, `re`, `datetime`, `math`, `random`, `collections`, `itertools`, `pathlib`, etc.). All other imports are blocked for plugin code. |

### 8.3 Permission Declaration

When `require_permissions` is enabled in `config.json`, plugins using `manifest.json` must declare required permissions. The security system cross-references declared permissions with audit findings and blocks loading if there is a mismatch.

**Supported permission values:**

- `"process"` — Required for plugins that spawn processes or execute code.
- `"network"` — Required for plugins that access the network.
- `"file_write"` — Required for plugins that delete or move files.
- `"file_read"` — Required for plugins that read files.

**Example `manifest.json` permission declaration:**

```json
{
    "name": "Network Monitor",
    "permissions": ["network", "file_read"]
}
```

### 8.4 Resource Limits

When `resource_limit` is enabled, each plugin module load is wrapped with `ResourceLimiter` which enforces:

- **CPU time limit:** 30 seconds (Unix: `signal.SIGALRM`, Windows: Timer thread)
- **Memory limit:** 512 MB (Unix: `resource.setrlimit RLIMIT_AS`)

These limits only apply during **module loading**. Once loaded, hook execution already has the 5-second `HOOK_TIMEOUT` protection.

### 8.5 Security Configuration Summary

| Config Key | Default | Values |
|---|---|---|
| `plugin_security_audit` | `"warn"` | `"off"` / `"warn"` / `"block"` |
| `plugin_security_import_restrict` | `"off"` | `"off"` / `"safe"` / `"strict"` |
| `plugin_security_require_permissions` | `false` | `true` / `false` |
| `plugin_security_resource_limit` | `false` | `true` / `false` |

---

## 9. manifest.json Reference

The `manifest.json` file is **required** for package-style plugins. It provides metadata and configuration that is read before the Python module is loaded.

### Complete Schema

```json
{
    "name": "My Plugin",
    "version": "1.0.0",
    "publisher": "Your Name",
    "author": "Your Name",
    "description": "A concise description of what the plugin does.",
    "entry": "plugin.py",
    "enabled": true,
    "permissions": [
        "file_read",
        "file_write"
    ]
}
```

### Field Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | **Yes** | Plugin display name. Takes priority over `PLUGIN_NAME` constant. |
| `version` | `string` | No | Semantic version. Takes priority over `PLUGIN_VERSION`. |
| `publisher` | `string` | No | Publisher name. `"author"` is an accepted alias. |
| `description` | `string` | No | Plugin description. Takes priority over `PLUGIN_DESCRIPTION`. |
| `entry` | `string` | No | Entry Python file relative to the plugin directory. Default: `"plugin.py"`. |
| `enabled` | `boolean` | No | Whether the plugin is enabled. Default: `true`. Set `false` to disable without deleting. |
| `permissions` | `string[]` | No | List of permission strings. Only checked when `require_permissions` is enabled. |

---

## 10. Step-by-Step Tutorial

This tutorial walks you through creating a complete plugin from scratch. We'll build a **"Weather Logger"** plugin that records weather observations and generates simple summaries.

### 10.1 Create the File

Create a new file named `weather_logger.py` in one of your plugin directories. You can find your plugin directories in `config.json` under `plugin_dirs`.

```python
# weather_logger.py
# A simple plugin for logging weather observations
```

### 10.2 Declare Metadata

Add the required module-level constants:

```python
PLUGIN_NAME = "Weather Logger"
PLUGIN_PUBLISHER = "Your Name"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Logs weather observations and generates summaries."
```

### 10.3 Register a Tool

Define the `TOOLS` list with an OpenAI function schema:

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "log_weather",
            "description": "Record a weather observation with temperature and conditions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or location name"
                    },
                    "temperature_c": {
                        "type": "number",
                        "description": "Temperature in Celsius"
                    },
                    "conditions": {
                        "type": "string",
                        "description": "Weather conditions, e.g. sunny, rainy, cloudy"
                    }
                },
                "required": ["location", "temperature_c"],
                "additionalProperties": false
            }
        }
    }
]
```

### 10.4 Implement execute()

Write the `execute()` function to handle the tool call:

```python
def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "log_weather":
        location = args["location"]
        temp = args["temperature_c"]
        conditions = args.get("conditions", "unknown")

        # Store the observation
        obs = context.storage.get("observations", [])
        obs.append({
            "location": location,
            "temp": temp,
            "conditions": conditions,
            "time": __import__("datetime").datetime.now().isoformat()
        })
        context.storage["observations"] = obs

        context.logger.info(f"Weather logged: {location} {temp}°C {conditions}")

        return (
            f"✅ Weather recorded for **{location}**:\n"
            f"  • Temperature: {temp}°C\n"
            f"  • Conditions: {conditions}\n"
            f"  • Total observations: {len(obs)}"
        )

    return f"Unknown tool: {tool_name}"
```

### 10.5 Add Lifecycle Hooks

Add hooks for initialization and task completion:

```python
def on_agent_init(context):
    """Initialize the observation log."""
    context.storage["observations"] = []
    context.logger.info("Weather Logger ready! ☀️")


def on_task_done(summary: str, final_reply: str, context):
    """Generate a summary when tasks complete."""
    obs = context.storage.get("observations", [])
    if not obs:
        return

    latest = obs[-1]
    context.logger.info(
        f"Task done. Last weather: {latest['location']} "
        f"{latest['temp']}°C ({latest['conditions']})"
    )
```

### 10.6 Test Your Plugin

After saving the file:

1. Restart the Vibe Coding Agent application.
2. Open Settings and verify your plugin appears in the plugin list.
3. Check that no security audit errors are shown.
4. Try a task like: *"Log the weather in Tokyo: 22°C, partly cloudy"*.
5. The Agent should invoke your `log_weather` tool and display the result.

---

## 11. Best Practices

### Keep Hooks Lightweight

Hook callbacks have a **5-second timeout**. Avoid blocking I/O, long computations, or infinite loops. If you need to do heavy work, consider spawning a background thread (but be aware of the security implications).

### Use context.storage for State

The `storage` dict is your per-plugin state container. It survives across all hooks within a session. Initialize default values in `on_agent_init`.

### Validate Tool Arguments

Even though the schema defines types, always validate critical arguments (file paths exist, numbers are in range, strings are not empty). The AI can occasionally pass unexpected values.

### Scope File Operations

Always build file paths using `os.path.join(context.project_root, relative_path)` followed by `os.path.normpath()`. Never access files outside the project root.

### Use Descriptive Tool Descriptions

The AI model uses the tool description to decide which tool to call. Write clear, specific descriptions that include when the tool should and should **not** be used.

### Handle Errors Gracefully

Catch exceptions in `execute()` and return user-friendly error messages. Never let exceptions propagate — they result in ugly tracebacks in the conversation.

### Log Strategically

Use `context.logger.info()` for significant events and `context.logger.debug()` for detailed tracing. This makes debugging easier without flooding the logs.

### Test with Security Enabled

Test your plugin with `plugin_security_audit` set to `"block"` to ensure it passes all security checks. This prevents surprises when users enable strict security.

### Document Your Plugin

Include a clear `PLUGIN_DESCRIPTION`. For package plugins, add a `README.md` alongside `manifest.json`. Users will appreciate good documentation.

### Watch for Tool Name Conflicts

Choose unique tool names. Check the 14 built-in tools and other installed plugins to avoid name collisions, which cause `RuntimeError` during loading.

---

## 12. Troubleshooting

### Plugin not appearing in the UI

- Verify the plugin directory is listed in `config.json` → `plugin_dirs`.
- Check that the file is **not** named `__init__.py`.
- For package plugins, ensure `manifest.json` exists and is valid JSON.
- Check that `PLUGIN_NAME` is defined in the module.

### "Security audit blocked" error

- Your plugin uses dangerous patterns (e.g., `os.system`, `subprocess`).
- Temporarily set `plugin_security_audit` to `"warn"` to see the full audit report.
- Refactor to use safe alternatives (e.g., use `context.logger` instead of subprocess).
- If the pattern is necessary, add appropriate permissions to `manifest.json`.

### "Import blocked" error

- Your plugin imports a restricted module (`subprocess`, `ctypes`, `socket`, etc.).
- Set `plugin_security_import_restrict` to `"off"` if you trust the plugin source.
- For strict mode, add your module to `STRICT_SAFE_MODULES` in `security.py`.

### Tool call returns "Unknown tool"

- The `execute()` function does not handle the `tool_name` being dispatched.
- Check for typos in the tool name string comparison.
- Verify the tool is registered in the `TOOLS` list with the correct name.

### Hook not being called

- Ensure the function name **exactly** matches one of the 15 hook names.
- Check the function signature — wrong parameter count causes silent failure.
- Verify the plugin loaded successfully (check plugin list in Settings).

### Plugin crashes the Agent

- Unhandled exceptions in hooks are silently caught.
- Exceptions in `execute()` are caught and returned as error strings.
- Check `plugin.log` in the app directory for error traces.
- Use `try`/`except` blocks around risky operations.

---

## 13. Appendix

### 13.1 Complete Hook Reference

| Hook | Signature | Mutating | Layer |
|---|---|---|---|
| `on_agent_init` | `(context)` | No | L1 Lifecycle |
| `on_agent_shutdown` | `(context)` | No | L1 Lifecycle |
| `on_task_start` | `(task_text, context)` | No | L2 Task |
| `on_task_done` | `(summary, final_reply, context)` | No | L2 Task |
| `on_task_error` | `(error_msg, context)` | No | L2 Task |
| `on_task_stopped` | `(context)` | No | L2 Task |
| `on_task_timeout` | `(elapsed, context)` | No | L2 Task |
| `before_step` | `(step, messages, context) → list` | **Yes** | L3 Step |
| `after_step` | `(step, reasoning, content, tool_calls, context)` | No | L3 Step |
| `before_tool_call` | `(tool_name, args, context) → dict` | **Yes** | L3 Step |
| `after_tool_call` | `(tool_name, args, result, context) → str` | **Yes** | L3 Step |
| `on_user_input_required` | `(question, context)` | No | L3 Step |
| `on_reasoning` | `(token, context)` | No | L4 Streaming |
| `on_content` | `(token, context)` | No | L4 Streaming |
| `on_event` | `(event_type, data, context)` | No | L4 Streaming |
| `on_usage_update` | `(usage, context)` | No | L4 Streaming |

### 13.2 Dangerous Pattern Registry

The following patterns are flagged by the AST source audit. Plugins using these will trigger warnings or be blocked depending on the `audit_level`.

| Pattern | Severity | Category |
|---|---|---|
| `os.system()`, `os.popen()` | CRITICAL | `shell_exec` |
| `subprocess.call()`, `run()`, `Popen()`, `check_output()` | CRITICAL | `shell_exec` |
| `eval()`, `exec()`, `compile()` | CRITICAL | `code_exec` |
| `__import__()`, `importlib.import_module()` | CRITICAL | `import_bypass` |
| `ctypes`, `cffi` | CRITICAL | `native_exec` |
| `os._exit()`, `os.kill()` | CRITICAL | `process_terminate` |
| `os.remove()`, `os.unlink()`, `os.rmdir()` | WARNING | `file_delete` |
| `shutil.rmtree()`, `shutil.move()` | WARNING | `file_delete` / `file_move` |
| `socket`, `http`, `urllib`, `requests` | WARNING | `network` |
| `ftplib`, `smtplib`, `poplib`, `telnetlib` | WARNING | `network` |
| `pickle`, `marshal`, `yaml.unsafe_load()` | WARNING | `deserialization` |
| `sys.modules`, `sys.setprofile()`, `sys.settrace()` | WARNING | `sys_manipulation` |
| `builtins` override, `sys.exit()` | WARNING | `builtin_override` / `sys_manipulation` |

### 13.3 PluginContext Quick Reference

```python
# PluginContext attributes
context.project_root     # str  — workspace root path
context.app_dir          # str  — app data directory
context.config           # dict — config.json snapshot (read-only)
context.storage          # dict — per-plugin key-value store
context.logger           # SimpleLogger — .info() .warn() .error() .debug()
context.current_step     # int  — current ReAct step
context.total_usage      # dict — {"input_tokens", "output_tokens", "tool_call_tokens"}

# Safe file path construction
import os
full_path = os.path.normpath(os.path.join(context.project_root, rel_path))
```

### 13.4 Official Plugins Source Reference

Study the official plugins for complete, working examples:

| Plugin | File | Description |
|---|---|---|
| Code Reviewer | `official_plugins/code_reviewer.py` | AST-based code quality review |
| Dev Utilities | `official_plugins/dev_utilities.py` | UUID, password, hash, timestamp, line count |
| Note Manager | `official_plugins/note_manager.py` | Persistent notes with tags and full-text search |
| Time Tracker | `official_plugins/time_tracker.py` | Task timing, tool call profiling, efficiency reports |

---

*— End of Document —*

*Generated: August 01, 2026 | NORP Vibe Coding Agent v1.0*
