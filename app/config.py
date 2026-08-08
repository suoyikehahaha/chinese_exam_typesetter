"""版式配置加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_layout(path: str | Path) -> dict[str, Any]:
    """加载 YAML 版式配置。"""

    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or "styles" not in data:
        raise ValueError("版式配置必须包含 styles")
    return data
