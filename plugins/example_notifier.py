# ──────────────────────────────────────────────────────────────
# Plugin: Example Notifier
# Publisher: xingluosama
# Version: 1.0.0
# Description: Demonstrates hooks and tool registration.
#   Copy this file into any directory listed in plugin_dirs to activate.
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Example Notifier"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Demonstrates hooks and tool registration."

import json
import os
import time

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plugin_hello",
            "description": "A demo tool registered by the example plugin. Returns a greeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to greet (default: World)"
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    }
]


def execute(tool_name: str, args: dict, context) -> str:
    """Handle tool calls registered by this plugin."""
    if tool_name == "plugin_hello":
        name = args.get("name", "World")
        return f"Hello, {name}! — from example plugin v1.0"


# ── L1: Lifecycle hooks ────────────────────────────────────────────

def on_agent_init(context):
    context.logger.info("Example plugin initialised")
    context.storage["start_time"] = time.time()


def on_agent_shutdown(context):
    elapsed = time.time() - context.storage.get("start_time", time.time())
    context.logger.info(f"Agent session lasted {elapsed:.0f}s")


# ── L2: Task hooks ─────────────────────────────────────────────────

def on_task_start(task_text: str, context):
    context.logger.info(f"Task started: {task_text[:100]}")


def on_task_done(summary: str, final_reply: str, context):
    context.logger.info(f"Task completed: {summary}")


def on_task_error(error_msg: str, context):
    context.logger.error(f"Task error: {error_msg}")


def on_task_stopped(context):
    context.logger.info("Task was stopped by user")


def on_task_timeout(elapsed: float, context):
    context.logger.warn(f"Task timed out after {elapsed:.0f}s")


# ── L3: Step hooks ─────────────────────────────────────────────────

def before_step(step: int, messages: list, context):
    """Log step count; return messages (or modified list)."""
    context.logger.debug(f"Step {step}: {len(messages)} messages")
    return messages  # returning the list = no modification


def after_step(step: int, reasoning: str, content: str,
               tool_calls: list, context):
    if tool_calls:
        names = [tc.get("name", tc.get("function", {}).get("name", "?"))
                 for tc in tool_calls]
        context.logger.debug(f"Step {step} → tools: {names}")


def before_tool_call(tool_name: str, args: dict, context):
    """Log every tool call. Return args to proceed, None to block."""
    context.logger.debug(f"→ {tool_name}({json.dumps(args, ensure_ascii=False)[:200]})")
    return args  # allow


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """Log tool results. Return (modified) result."""
    short = result[:200].replace("\n", " ")
    context.logger.debug(f"← {tool_name}: {short}")
    return result  # no modification


def on_user_input_required(question: str, context):
    context.logger.info(f"Agent is asking user: {question[:100]}")


# ── L4: Streaming event hooks ──────────────────────────────────────

def on_reasoning(token: str, context):
    pass  # called per token — keep lightweight


def on_content(token: str, context):
    pass  # called per token


def on_event(event_type: str, data: str, context):
    pass  # all event-queue events


def on_usage_update(usage: dict, context):
    context.logger.debug(
        f"Tokens: {usage.get('input_tokens',0)} in / "
        f"{usage.get('output_tokens',0)} out")
