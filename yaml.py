"""项目内置的最小 YAML 读取器。

仅支持 layout.yaml 使用的嵌套映射与标量，接口兼容 yaml.safe_load。
"""

from __future__ import annotations

from typing import Any, TextIO


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return {}
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def safe_load(stream: TextIO | str) -> dict[str, Any]:
    """读取由嵌套映射和标量组成的 YAML。"""

    text = stream.read() if hasattr(stream, "read") else str(stream)
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()
        if ":" not in content:
            raise ValueError(f"不支持的 YAML 行：{raw_line}")
        key, value = content.split(":", 1)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = _scalar(value)
        parent[key.strip()] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root
