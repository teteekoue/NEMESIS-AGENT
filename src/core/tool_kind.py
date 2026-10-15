from enum import Enum, auto
from collections.abc import Callable
from typing import Optional, Any


class ToolNamespace(str, Enum):
    GROK_BUILD = "grok_build"
    OPENCODE = "opencode"
    MCP = "mcp"

    def __str__(self) -> str:
        return self.value


class ToolKind(str, Enum):

    READ = "read"
    EDIT = "edit"
    DELETE = "delete"
    LIST_DIR = "list_dir"
    WRITE = "write"
    MOVE = "move"
    SEARCH = "search"
    LSP = "lsp"
    EXECUTE = "execute"
    PLAN = "plan"
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    BACKGROUND_TASK_ACTION = "background_task_action"
    WAIT_TASKS_ACTION = "wait_tasks_action"
    KILL_TASK_ACTION = "kill_task_action"
    LIST = "list"
    SKILL = "skill"
    MEMORY_SEARCH = "memory_search"
    MEMORY_GET = "memory_get"
    TASK = "task"
    ENTER_PLAN = "enter_plan"
    EXIT_PLAN = "exit_plan"
    ASK_USER = "ask_user"
    IMAGE_GEN = "image_gen"
    VIDEO_GEN = "video_gen"
    IMAGE_TO_VIDEO = "image_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"
    DEPLOY_APP = "deploy_app"
    SEARCH_TOOL = "search_tool"
    USE_TOOL = "use_tool"
    MONITOR = "monitor"
    GOAL_UPDATE = "goal_update"
    WORKFLOW = "workflow"
    OTHER = "other"

    def presentation_name(self) -> str:
        return _PRESENTATION_NAMES[self]

    def is_read_only(self) -> bool:
        return self in _READ_ONLY_KINDS

    @property
    def as_key(self) -> str:
        return self.value


_PRESENTATION_NAMES = {
    ToolKind.READ: "Read",
    ToolKind.EDIT: "Edit",
    ToolKind.DELETE: "Delete",
    ToolKind.WRITE: "Write",
    ToolKind.MOVE: "Move",
    ToolKind.LIST_DIR: "List Files",
    ToolKind.LIST: "List Files",
    ToolKind.SEARCH: "Search",
    ToolKind.LSP: "Code Intelligence",
    ToolKind.EXECUTE: "Run Command",
    ToolKind.PLAN: "Plan",
    ToolKind.WEB_SEARCH: "Web Search",
    ToolKind.WEB_FETCH: "Web Fetch",
    ToolKind.BACKGROUND_TASK_ACTION: "Background Task",
    ToolKind.WAIT_TASKS_ACTION: "Wait for Tasks",
    ToolKind.KILL_TASK_ACTION: "Kill Task",
    ToolKind.SKILL: "Skill",
    ToolKind.MEMORY_SEARCH: "Memory Search",
    ToolKind.MEMORY_GET: "Memory Read",
    ToolKind.TASK: "Subagent",
    ToolKind.ENTER_PLAN: "Enter Plan Mode",
    ToolKind.EXIT_PLAN: "Exit Plan Mode",
    ToolKind.ASK_USER: "Ask User",
    ToolKind.IMAGE_GEN: "Generate Image",
    ToolKind.VIDEO_GEN: "Generate Video",
    ToolKind.IMAGE_TO_VIDEO: "Generate Video",
    ToolKind.REFERENCE_TO_VIDEO: "Generate Video",
    ToolKind.DEPLOY_APP: "Deploy App",
    ToolKind.SEARCH_TOOL: "Search Tools",
    ToolKind.USE_TOOL: "Use Tool",
    ToolKind.MONITOR: "Monitor",
    ToolKind.GOAL_UPDATE: "Update Goal",
    ToolKind.WORKFLOW: "Workflow",
    ToolKind.OTHER: "Tool",
}

_READ_ONLY_KINDS = {
    ToolKind.READ,
    ToolKind.SEARCH,
    ToolKind.LSP,
    ToolKind.LIST_DIR,
    ToolKind.LIST,
    ToolKind.MEMORY_SEARCH,
    ToolKind.MEMORY_GET,
    ToolKind.WEB_SEARCH,
    ToolKind.WEB_FETCH,
    ToolKind.ENTER_PLAN,
    ToolKind.EXIT_PLAN,
    ToolKind.ASK_USER,
}


class ToolIdentity:
    __slots__ = ("tool_kind", "namespace", "presentation_name", "read_only")

    def __init__(self, tool_kind: ToolKind, namespace: ToolNamespace):
        self.tool_kind = tool_kind
        self.namespace = namespace
        self.presentation_name = tool_kind.presentation_name()
        self.read_only = tool_kind.is_read_only()


class ToolDefinition:
    __slots__ = ("name", "description", "kind", "namespace", "params_schema", "read_only")

    def __init__(
        self,
        name: str,
        description: str,
        kind: ToolKind,
        namespace: ToolNamespace,
        params_schema: dict,
        read_only: Optional[bool] = None,
    ):
        self.name = name
        self.description = description
        self.kind = kind
        self.namespace = namespace
        self.params_schema = params_schema
        self.read_only = read_only if read_only is not None else kind.is_read_only()

    def to_openai_function(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }
