"""试卷结构化数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Literal


QuestionKind = Literal["objective", "subjective"]


@dataclass(slots=True)
class InlineSegment:
    """同一段落中的一段字符及其语义角色。"""

    text: str
    role: str = "body"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InlineSegment":
        return cls(text=str(data["text"]), role=str(data.get("role", "body")))


@dataclass(slots=True)
class Question:
    """题目结构。"""

    number: int
    kind: QuestionKind
    stem: str
    score: float | None = None
    options: list[str] = field(default_factory=list)
    option_layout: str = "vertical"
    embedded_segments: list[list[InlineSegment]] = field(default_factory=list)
    segmentation_text: str | None = None
    subquestions: list[str] = field(default_factory=list)
    composition_material: list[str] = field(default_factory=list)
    composition_prompt: list[str] = field(default_factory=list)
    composition_requirements: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Question":
        embedded = [
            [InlineSegment.from_dict(segment) for segment in paragraph]
            for paragraph in data.get("embedded_segments", [])
        ]
        return cls(
            number=int(data["number"]),
            kind=data["kind"],
            stem=str(data["stem"]),
            score=float(data["score"]) if data.get("score") is not None else None,
            options=[str(item) for item in data.get("options", [])],
            option_layout=str(data.get("option_layout", "vertical")),
            embedded_segments=embedded,
            segmentation_text=data.get("segmentation_text"),
            subquestions=[str(item) for item in data.get("subquestions", [])],
            composition_material=[str(item) for item in data.get("composition_material", [])],
            composition_prompt=[str(item) for item in data.get("composition_prompt", [])],
            composition_requirements=[str(item) for item in data.get("composition_requirements", [])],
        )


@dataclass(slots=True)
class Block:
    """按出现顺序排列的试卷内容块。"""

    type: str
    text: str = ""
    paragraphs: list[str] = field(default_factory=list)
    title: str = ""
    author: str = ""
    note: str = ""
    source: str = ""
    question: Question | None = None
    name: str = ""
    meta: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Block":
        question = Question.from_dict(data["question"]) if "question" in data else None
        return cls(
            type=str(data["type"]),
            text=str(data.get("text", "")),
            paragraphs=[str(item) for item in data.get("paragraphs", [])],
            title=str(data.get("title", "")),
            author=str(data.get("author", "")),
            note=str(data.get("note", "")),
            source=str(data.get("source", "")),
            question=question,
            name=str(data.get("name", "")),
            meta=str(data.get("meta", "")),
        )


@dataclass(slots=True)
class ExamMetadata:
    """卷首元数据。"""

    exam_name: str
    subject_name: str
    meta_text: str
    notices: list[str] = field(default_factory=list)
    total_score: float = 150

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExamMetadata":
        return cls(
            exam_name=str(data["exam_name"]),
            subject_name=str(data.get("subject_name", "语　文")),
            meta_text=str(data["meta_text"]),
            notices=[str(item) for item in data.get("notices", [])],
            total_score=float(data.get("total_score", 150)),
        )


@dataclass(slots=True)
class ExamDocument:
    """完整试卷。"""

    metadata: ExamMetadata
    blocks: list[Block]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExamDocument":
        return cls(
            metadata=ExamMetadata.from_dict(data["metadata"]),
            blocks=[Block.from_dict(item) for item in data["blocks"]],
        )


def load_exam(path: str | Path) -> ExamDocument:
    """从 UTF-8 JSON 文件加载试卷。"""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExamDocument.from_dict(data)
