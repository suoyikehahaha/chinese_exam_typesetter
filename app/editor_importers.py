"""兼容入口，使用上下文感知试题导入器。"""

from .flexible_importers_v2 import import_exam, parse_plain_lines, save_exam

__all__ = ["import_exam", "parse_plain_lines", "save_exam"]
