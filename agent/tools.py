"""
工具系统：注册机制 + 内置工具。

使用 @tool 装饰器注册工具，工具元信息（name/description/schema）内聚在函数上。
ToolContext 在执行时传入，工具可通过它读写运行时状态（当前工作目录等）。
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .skills import SkillRegistry


# ---------------------------------------------------------------------------
# ToolContext
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """运行时状态，在工具执行时传入。"""
    work_dir: Path          # 当前工作目录（可被 change_dir 修改）
    session_id: str = ""
    skill_registry: "SkillRegistry | None" = None
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def resolve(self, path: str) -> Path:
        """将相对路径解析为绝对路径（相对于 work_dir）。"""
        p = Path(path)
        if p.is_absolute():
            return p
        return (self.work_dir / p).resolve()


# ---------------------------------------------------------------------------
# 工具注册
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]          # OpenAI function parameters schema
    func: Callable[[dict, ToolContext], str]

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


TOOL_REGISTRY: dict[str, ToolDef] = {}


def tool(
    name: str,
    description: str,
    schema: dict[str, Any],
) -> Callable:
    """装饰器：将函数注册为工具。函数签名必须为 (args: dict, ctx: ToolContext) -> str。"""
    def decorator(func: Callable) -> Callable:
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            schema=schema,
            func=func,
        )
        return func
    return decorator


def get_enabled_tools(enabled_names: list[str]) -> list[ToolDef]:
    """返回已启用的工具列表。"""
    return [TOOL_REGISTRY[n] for n in enabled_names if n in TOOL_REGISTRY]


def get_openai_schemas(enabled_names: list[str]) -> list[dict[str, Any]]:
    """返回 OpenAI function calling 格式的工具 schema 列表。"""
    return [t.to_openai_schema() for t in get_enabled_tools(enabled_names)]


def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> str:
    """
    执行工具。

    Returns:
        工具执行结果字符串；出错时返回错误描述（不抛出异常）。
    """
    tool_def = TOOL_REGISTRY.get(name)
    if tool_def is None:
        return f"[错误] 未知工具：{name}"
    try:
        return tool_def.func(arguments, ctx)
    except Exception as e:
        return f"[错误] 工具 {name} 执行失败：{e}"


# ---------------------------------------------------------------------------
# 内置工具实现
# ---------------------------------------------------------------------------

@tool(
    name="skill_read",
    description="按 Skill 名称读取对应 SKILL.md 的完整说明。使用前必须从 <available_skills> 中选择 Skill 名称。",
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要读取的 Skill 名称，必须来自 <available_skills>",
            },
        },
        "required": ["name"],
    },
)
def skill_read(args: dict, ctx: ToolContext) -> str:
    registry = ctx.skill_registry
    if registry is None:
        return "[错误] Skill 系统未初始化"
    name = str(args.get("name", "")).strip()
    if not name:
        return "[错误] 缺少 Skill 名称"

    from .skills import read_skill_markdown
    return read_skill_markdown(name, registry)


@tool(
    name="read_file",
    description="读取文件内容并返回。支持指定起始行和最大行数。",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于工作目录或绝对路径）",
            },
            "encoding": {
                "type": "string",
                "description": "文件编码，默认 utf-8",
                "default": "utf-8",
            },
            "start_line": {
                "type": "integer",
                "description": "从第几行开始读取（从 1 计），默认 1",
                "default": 1,
            },
            "max_lines": {
                "type": "integer",
                "description": "最多读取多少行，默认不限制",
            },
        },
        "required": ["path"],
    },
)
def read_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    encoding = args.get("encoding", "utf-8")
    start_line = max(1, int(args.get("start_line", 1)))
    max_lines = args.get("max_lines")

    if not path.exists():
        return f"[错误] 文件不存在：{path}"
    if not path.is_file():
        return f"[错误] 路径不是文件：{path}"

    with open(path, encoding=encoding, errors="replace") as f:
        lines = f.readlines()

    selected = lines[start_line - 1:]
    if max_lines is not None:
        selected = selected[:int(max_lines)]

    if not selected:
        return "(文件为空或指定范围无内容)"
    return "".join(selected)


@tool(
    name="write_file",
    description="将内容写入文件。默认覆盖，可选追加模式。",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对于工作目录或绝对路径）",
            },
            "content": {
                "type": "string",
                "description": "要写入的内容",
            },
            "append": {
                "type": "boolean",
                "description": "是否追加模式，默认 false（覆盖）",
                "default": False,
            },
            "encoding": {
                "type": "string",
                "description": "文件编码，默认 utf-8",
                "default": "utf-8",
            },
        },
        "required": ["path", "content"],
    },
)
def write_file(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    content = args["content"]
    append = args.get("append", False)
    encoding = args.get("encoding", "utf-8")

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode, encoding=encoding) as f:
        f.write(content)

    action = "追加写入" if append else "写入"
    return f"已{action} {len(content)} 个字符到 {path}"


@tool(
    name="list_dir",
    description="列出目录下的文件和子目录。",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径，默认为当前工作目录",
                "default": ".",
            },
            "show_hidden": {
                "type": "boolean",
                "description": "是否显示以 . 开头的隐藏文件，默认 false",
                "default": False,
            },
        },
        "required": [],
    },
)
def list_dir(args: dict, ctx: ToolContext) -> str:
    path_str = args.get("path", ".")
    path = ctx.resolve(path_str)
    show_hidden = args.get("show_hidden", False)

    if not path.exists():
        return f"[错误] 路径不存在：{path}"
    if not path.is_dir():
        return f"[错误] 路径不是目录：{path}"

    entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        if entry.is_dir():
            lines.append(f"[目录] {entry.name}/")
        else:
            size = entry.stat().st_size
            lines.append(f"[文件] {entry.name}  ({_human_size(size)})")

    if not lines:
        return f"目录为空：{path}"
    return f"目录：{path}\n" + "\n".join(lines)


@tool(
    name="change_dir",
    description="切换当前工作目录。后续工具调用的相对路径将基于新目录。",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标目录路径（相对或绝对）",
            },
        },
        "required": ["path"],
    },
)
def change_dir(args: dict, ctx: ToolContext) -> str:
    new_dir = ctx.resolve(args["path"])
    if not new_dir.exists():
        return f"[错误] 目录不存在：{new_dir}"
    if not new_dir.is_dir():
        return f"[错误] 路径不是目录：{new_dir}"
    old_dir = ctx.work_dir
    ctx.work_dir = new_dir
    return f"工作目录已从 {old_dir} 切换到 {new_dir}"


@tool(
    name="shell",
    description="在当前工作目录执行 shell 命令，返回标准输出和标准错误。",
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30",
                "default": 30,
            },
        },
        "required": ["command"],
    },
)
def shell(args: dict, ctx: ToolContext) -> str:
    command = args["command"]
    timeout = int(args.get("timeout", 30))

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ctx.work_dir),
        )
        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr}")
        if result.returncode != 0:
            output_parts.append(f"[退出码] {result.returncode}")
        return "\n".join(output_parts) if output_parts else "(命令执行成功，无输出)"
    except subprocess.TimeoutExpired:
        return f"[错误] 命令超时（{timeout}s）：{command}"


@tool(
    name="search_files",
    description="在目录中递归搜索匹配文件名 glob 模式的文件，可选在文件内容中搜索关键词。",
    schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "文件名 glob 模式，如 '*.py' 或 'config*'",
            },
            "path": {
                "type": "string",
                "description": "搜索根目录，默认为当前工作目录",
                "default": ".",
            },
            "content_keyword": {
                "type": "string",
                "description": "可选：在文件内容中搜索的关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回结果数，默认 50",
                "default": 50,
            },
        },
        "required": ["pattern"],
    },
)
def search_files(args: dict, ctx: ToolContext) -> str:
    pattern = args["pattern"]
    root = ctx.resolve(args.get("path", "."))
    keyword = args.get("content_keyword")
    max_results = int(args.get("max_results", 50))

    if not root.exists():
        return f"[错误] 路径不存在：{root}"

    matched: list[str] = []
    for dirpath, _dirs, filenames in os.walk(root):
        for filename in filenames:
            if fnmatch.fnmatch(filename, pattern):
                full_path = Path(dirpath) / filename
                if keyword:
                    try:
                        text = full_path.read_text(encoding="utf-8", errors="replace")
                        if keyword not in text:
                            continue
                    except Exception:
                        continue
                matched.append(str(full_path))
                if len(matched) >= max_results:
                    break
        if len(matched) >= max_results:
            break

    if not matched:
        desc = f"模式 '{pattern}'"
        if keyword:
            desc += f" 且包含 '{keyword}'"
        return f"未找到匹配文件（{desc}）"

    header = f"找到 {len(matched)} 个文件（搜索根目录：{root}）"
    if len(matched) >= max_results:
        header += f"（已达上限 {max_results}，可能有更多）"
    return header + "\n" + "\n".join(matched)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size //= 1024
    return f"{size:.0f} TB"
