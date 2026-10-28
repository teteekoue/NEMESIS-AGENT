from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Any
from .tool_kind import ToolKind, ToolNamespace, ToolDefinition, ToolIdentity
from .template_renderer import TemplateRenderer


@dataclass
class ToolRegistration:
    definition: ToolDefinition
    handler: Callable
    param_names: Dict[str, str] = field(default_factory=dict)
    risk: str = "medium"


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolRegistration] = {}
        self._renderer: Optional[TemplateRenderer] = None

    def register(
        self,
        name: str,
        kind: ToolKind,
        handler: Callable,
        description: str,
        params_schema: dict,
        namespace: ToolNamespace = ToolNamespace.GROK_BUILD,
        param_names: Optional[Dict[str, str]] = None,
        risk: Optional[str] = None,
    ) -> "ToolRegistry":
        self._tools[name] = ToolRegistration(
            definition=ToolDefinition(
                name=name,
                description=description,
                kind=kind,
                namespace=namespace,
                params_schema=params_schema,
            ),
            handler=handler,
            param_names=param_names or {},
            risk=risk or self._default_risk(kind, name),
        )
        return self

    @staticmethod
    def _default_risk(kind: ToolKind, name: str) -> str:
        if kind.is_read_only():
            return "read"
        if name in {"delete_file", "mcp_call", "kill_task"}:
            return "high"
        if name in {"bash", "apply_patch", "delegate_task"}:
            return "medium"
        return "medium"

    def risk_for(self, name: str) -> str:
        reg = self._tools.get(name)
        return reg.risk if reg else "high"

    def finalize(self) -> "ToolRegistry":
        tools_map: Dict[ToolKind, str] = {}
        params_map: Dict[ToolKind, Dict[str, str]] = {}

        for name, reg in self._tools.items():
            tools_map[reg.definition.kind] = name
            if reg.param_names:
                params_map[reg.definition.kind] = reg.param_names
            else:
                params_map[reg.definition.kind] = {
                    p: p for p in reg.definition.params_schema.get("properties", {}).keys()
                }

        self._renderer = TemplateRenderer(tools_map, params_map)

        for name, reg in self._tools.items():
            rendered_desc = self._renderer.render(reg.definition.description)
            reg.definition.description = rendered_desc
            schema = dict(reg.definition.params_schema)
            self._renderer.render_schema_descriptions(schema)
            reg.definition.params_schema = schema

        return self

    @property
    def renderer(self) -> Optional[TemplateRenderer]:
        return self._renderer

    def get_renderer(self) -> TemplateRenderer:
        if self._renderer is None:
            raise RuntimeError("Registry not finalized. Call .finalize() first.")
        return self._renderer

    def get_tool(self, name: str) -> Optional[ToolRegistration]:
        return self._tools.get(name)

    def get_definitions(self) -> List[ToolDefinition]:
        return [reg.definition for reg in self._tools.values()]

    def get_openai_functions(self) -> List[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": reg.definition.name,
                    "description": reg.definition.description,
                    "parameters": reg.definition.params_schema,
                },
            }
            for reg in self._tools.values()
        ]

    def call(self, name: str, params: dict) -> Any:
        reg = self._tools.get(name)
        if not reg:
            raise ValueError(f"Unknown tool: {name}")

        ok, cleaned, error = self.validate_call(name, params)
        if not ok:
            raise ValueError(error)
        return reg.handler(**cleaned)

    def validate_call(self, name: str, params: dict):
        """Valide les parametres contre le schema de l'outil.

        Retourne (ok, cleaned_params, error):
        - Les cles inconnues sont supprimees (jamais passees au handler).
        - Les parametres requis manquants provoquent une erreur explicite.
        """
        reg = self._tools.get(name)
        if not reg:
            return False, {}, f"Outil inconnu: {name}"

        if not isinstance(params, dict):
            return False, {}, f"Parametres invalides pour '{name}' (dict attendu)."

        schema = reg.definition.params_schema or {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Quelques modeles utilisent `file`, `target_file` ou `files` alors que le
        # catalogue documente `path` / `file_path` / `paths`. Normaliser ici evite
        # un faux "Parametres requis pour 'read_file'" sur un appel pourtant valide.
        normalized = dict(params)
        for alias in ("file", "target_file", "target", "name"):
            if alias not in normalized:
                continue
            for canonical in ("file_path", "path", "target_file"):
                if canonical in properties and canonical not in normalized:
                    normalized[canonical] = normalized.pop(alias)
                    break
        if (
            "files" in normalized
            and "paths" in properties
            and "paths" not in normalized
        ):
            normalized["paths"] = normalized.pop("files")
        params = normalized

        cleaned = {}
        for key, value in params.items():
            if key in properties:
                cleaned[key] = value

        def matches_type(value, expected):
            if expected == "string":
                return isinstance(value, str)
            if expected == "integer":
                return isinstance(value, int) and not isinstance(value, bool)
            if expected == "number":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            if expected == "boolean":
                return isinstance(value, bool)
            if expected == "array":
                return isinstance(value, list)
            if expected == "object":
                return isinstance(value, dict)
            return True

        missing = [r for r in required if r not in cleaned]
        if missing:
            return (
                False,
                {},
                f"Parametres requis manquants pour '{name}': {', '.join(missing)}",
            )

        any_of = schema.get("anyOf") or []
        if any_of and not any(
            all(key in cleaned for key in option.get("required", []))
            for option in any_of if isinstance(option, dict)
        ):
            choices = [" + ".join(option.get("required", [])) for option in any_of]
            return False, {}, f"Parametres requis pour '{name}': {' ou '.join(choices)}"

        for key, value in cleaned.items():
            expected = properties.get(key, {}).get("type")
            if expected and not matches_type(value, expected):
                return False, {}, f"Parametre '{key}' invalide pour '{name}': type {expected} attendu"

        return True, cleaned, ""

    def tool_for_kind(self, kind: ToolKind) -> Optional[str]:
        if self._renderer:
            # Several tools can share a kind (for example grep and glob).
            # Keep the first canonical registration stable.
            for name, reg in self._tools.items():
                if reg.definition.kind == kind:
                    return name
            return self._renderer.tool_for_kind(kind)
        for name, reg in self._tools.items():
            if reg.definition.kind == kind:
                return name
        return None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.items())
