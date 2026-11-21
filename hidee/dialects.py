"""Hook dialects. Hidee's checks speak Claude Code's: tool names such as Bash or WebSearch, and replies with
`permissionDecision`. Gemini CLI names its tools differently and expects `decision` replies, so its calls are
translated in and Hidee's replies translated out. The rules, the checkpoint, and the learn-mode timeline stay the
same for every agent."""
from __future__ import annotations

import re

URL = re.compile(r"https?://[^\s\"'<>]+")

# Gemini tool -> Claude Code tool
GEMINI_TOOLS = {
    "run_shell_command": "Bash", "google_web_search": "WebSearch", "web_fetch": "WebFetch",
    "read_file": "Read", "read_many_files": "Read", "write_file": "Write", "replace": "Edit",
    "glob": "Glob", "search_file_content": "Grep", "list_directory": "Glob",
}


def gemini_mcp_name(tool: str, mcp_context) -> str:
    """Gemini CLI names an MCP tool by its bare name (send_email) and puts the server in mcp_context. Hidee checks
    MCP calls by Claude Code's name, mcp__<server>__<tool>; without this, Gemini's MCP calls looked local."""
    if isinstance(mcp_context, dict) and mcp_context.get("server_name") and not tool.startswith("mcp__"):
        return f"mcp__{mcp_context['server_name']}__{mcp_context.get('tool_name') or tool}"
    return tool


def gemini_call(tool: str, args: dict) -> tuple[str, dict]:
    """A Gemini tool call as the Claude Code call Hidee knows how to check."""
    args = dict(args or {})
    name = GEMINI_TOOLS.get(tool, tool)              # MCP tools (mcp__server__tool) keep their names
    if name == "Bash":
        return name, {"command": str(args.get("command", ""))}
    if name == "WebSearch":
        return name, {"query": str(args.get("query", ""))}
    if name == "WebFetch":
        prompt = str(args.get("prompt", ""))
        found = URL.search(prompt)
        return name, {"url": found.group(0) if found else "", "prompt": prompt}
    if name in ("Read", "Write", "Edit"):
        path = args.get("file_path") or args.get("absolute_path") or args.get("path") or \
            " ".join(args.get("paths") or args.get("include") or [])
        out = {**{k: v for k, v in args.items() if k not in ("absolute_path", "path", "paths", "include")},
               "file_path": str(path)}
        return name, out
    if name == "Glob":
        return name, {"pattern": str(args.get("pattern") or args.get("dir_path") or args.get("path") or "")}
    if name == "Grep":
        return name, {"pattern": str(args.get("pattern", "")), "path": str(args.get("dir_path") or "")}
    return name, args


def _back(tool: str, original: dict, updated: dict) -> dict:
    """A shorter Claude Code input as Gemini arguments."""
    if tool == "Bash":
        return {"command": updated.get("command", "")}
    if tool == "WebSearch":
        return {"query": updated.get("query", "")}
    if tool == "WebFetch":
        prompt = str(original.get("prompt", ""))
        old, new = str(original.get("url", "")), str(updated.get("url", ""))
        prompt = prompt.replace(old, new) if old and old in prompt else f"{new} {updated.get('prompt', prompt)}"
        return {"prompt": prompt}
    return updated


def gemini_reply(tool: str, claude_input: dict, reply: dict) -> dict:
    """Hidee's Claude Code reply as a Gemini BeforeTool reply."""
    out = (reply or {}).get("hookSpecificOutput") or {}
    decision = out.get("permissionDecision")
    if not decision:
        return {}
    if decision == "deny":
        return {"decision": "deny", "reason": out.get("permissionDecisionReason", "Hidee stopped this call.")}
    answer = {"decision": "allow"}
    if "updatedInput" in out:
        answer["hookSpecificOutput"] = {"tool_input": _back(tool, claude_input, out["updatedInput"])}
        answer["systemMessage"] = "Hidee sent a shorter version: sensitive parts were removed."
    return answer


def gemini_event(body: dict) -> dict:
    """A Gemini lifecycle event as the Claude Code event the learn-mode timeline records."""
    event = body.get("hook_event_name")
    if event == "BeforeAgent":
        return {"hook_event_name": "UserPromptSubmit", "prompt": body.get("prompt", "")}
    if event == "AfterAgent":
        return {"hook_event_name": "Stop", "last_assistant_message": body.get("prompt_response", "")}
    if event == "AfterTool":
        tool, args = gemini_call(str(body.get("tool_name", "")), body.get("tool_input") or {})
        response = body.get("tool_response") or {}
        if isinstance(response, dict) and response.get("error"):
            return {"hook_event_name": "PostToolUseFailure", "tool_name": tool, "tool_input": args,
                    "error": response["error"]}
        content = response.get("llmContent", response.get("returnDisplay", "")) if isinstance(response, dict) \
            else response
        return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": args, "tool_response": content}
    return {}
