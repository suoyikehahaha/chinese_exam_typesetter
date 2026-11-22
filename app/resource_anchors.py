"""Link Word resources to editable objects; project their positions onto each revision."""
from collections import defaultdict
from difflib import SequenceMatcher

from .paragraph_identity import paragraph_identity


def remap_ranges(ranges, old, new):
    """Keep formatting on surviving characters, including insertions between them."""
    if old == new or not ranges:
        return ranges
    matches = SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks()
    result = []
    for entry in ranges:
        left, right = int(entry["start"]), int(entry["end"])
        spans = [(b + max(a, left) - a, b + min(a + n, right) - a)
                 for a, b, n in matches if max(a, left) < min(a + n, right)]
        if spans:
            result.append({**entry, "start": spans[0][0], "end": spans[-1][1]})
    return result


def _key(text):
    return "".join(paragraph_identity(text).translate(str.maketrans("．（）", ".()")).split())


def _slots(raw):
    from .inspector_model import all_content_objects
    counts = defaultdict(int)
    for item in all_content_objects(raw):
        block = raw["blocks"][item.block_index] if item.block_index is not None else {}
        owner = block.get("id", "meta")
        value = item.text
        if item.target == "stem":
            question = block["question"]
            score = question.get("score")
            suffix = f"（{float(score):g}分）" if score is not None else ""
            value = f"{question['number']}．{value}{suffix}"
        elif item.target == "subquestion":
            value = f"（{item.target_index + 1}）{value}"
        elif item.target == "notice":
            value = f"{item.target_index + 1}．{value}"
        elif block.get("type") in {"subsection", "answer_subsection"}:
            if item.target == "meta":
                continue
            value = str(block.get("name", "")) + str(block.get("meta", ""))
        identity = paragraph_identity(value)
        occurrence = counts[identity]
        counts[identity] += 1
        yield (owner, item.target, item.target_index), value, item.text, occurrence


def bind_source_resources(raw):
    """At import (also for older drafts), establish a persistent semantic address."""
    metadata = raw.get("metadata", {})
    entries = [*metadata.get("native_objects", ()), *metadata.get("source_decorations", ())]
    if not entries:
        return
    from .models.identity import ensure_block_ids
    ensure_block_ids(raw)
    by_text = defaultdict(list)
    for ref, full, editable, _ in _slots(raw):
        for key in {_key(full), _key(editable)} - {""}:
            by_text[key].append(ref)
    for entry in entries:
        if "object_ref" in entry:
            continue
        original = entry.get("text") or entry.get("target_text") or entry.get("marker", "")
        candidates = by_text.get(_key(original), ())
        idx = int(entry.get("occurrence", 0))
        if 0 <= idx < len(candidates):
            entry["object_ref"] = list(candidates[idx])


def project_source_resources(raw):
    """Resolve stored object references on a private render snapshot."""
    metadata = raw.get("metadata", {})
    entries = [*metadata.get("native_objects", ()), *metadata.get("source_decorations", ())]
    if not entries:
        return
    slots = {ref: (full, occurrence) for ref, full, _, occurrence in _slots(raw)}
    for entry in entries:
        ref = tuple(entry.get("object_ref", ()))
        if not ref or ref not in slots:
            continue
        full, occurrence = slots[ref]
        if "ranges" in entry:
            entry["ranges"] = remap_ranges(entry["ranges"], entry["text"], full)
            entry["text"] = full
        elif "target_text" in entry:
            entry["target_text"] = full
        entry["occurrence"] = occurrence


def register_block_paragraphs(document, raw_block, paragraphs):
    """Associate rendered Word paragraphs, including option cells, with editor keys."""
    from .inspector_model import content_objects_for_block
    objects = content_objects_for_block(raw_block, 0)
    shared = raw_block.get("type") in {"subsection", "answer_subsection"}
    registry = document.part.exam_paragraphs
    for item in objects:
        index = 0 if shared else item.line_index
        if index < len(paragraphs):
            p = paragraphs[index]
            owner = raw_block.get("id")
            registry[owner, item.target, item.target_index] = p
            registry[owner, "block", item.line_index] = p


def bound_paragraph(document, ref):
    p = getattr(document.part, "exam_paragraphs", {}).get(tuple(ref))
    if p is not None and p._p.getparent() is not None:
        return p
    return None
