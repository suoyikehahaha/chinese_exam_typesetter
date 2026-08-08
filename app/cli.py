"""DOCX-only command-line entry point for the current runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.config import load_layout
from app.models import load_exam
from app.pagination import apply_pagination_guards
from app.renderers import DocxRenderer
from app.validators import check_required_fonts, validate_exam


def build(args: argparse.Namespace) -> int:
    """Generate one DOCX file from a structured exam JSON file."""

    exam = load_exam(args.exam)
    layout = load_layout(args.config)
    issues = validate_exam(exam)
    for issue in issues:
        print(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
    if any(issue.severity == "error" for issue in issues):
        return 2

    required_fonts = sorted(set(layout["fonts"].values()))
    fonts = check_required_fonts(required_fonts)
    print(json.dumps({"fonts": fonts}, ensure_ascii=False))
    missing = [name for name, installed in fonts.items() if not installed]
    if missing and not args.allow_missing_fonts:
        print("缺少字体：" + "、".join(missing), file=sys.stderr)
        return 3

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or Path(args.exam).stem
    docx_path = output_dir / f"{stem}.docx"
    DocxRenderer(layout, args.template).render(exam, docx_path)
    apply_pagination_guards(docx_path)
    print(f"DOCX: {docx_path.resolve()}")
    return 0


def parser() -> argparse.ArgumentParser:
    """Construct the command-line parser."""

    root = argparse.ArgumentParser(description="高中语文试卷智能排版系统")
    subcommands = root.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("build", help="从结构化 JSON 生成 DOCX")
    command.add_argument("exam")
    command.add_argument("--config", required=True)
    command.add_argument("--output", default="output")
    command.add_argument("--name")
    command.add_argument("--template")
    command.add_argument("--allow-missing-fonts", action="store_true")
    command.set_defaults(func=build)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
