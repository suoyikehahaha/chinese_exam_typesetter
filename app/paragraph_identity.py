"""Match source paragraphs after punctuation-only subquestion normalization."""
import re


def paragraph_identity(text: str) -> str:
    value = str(text).strip()
    return re.sub(r"^[（(]\s*(\d+)\s*[）)]\s*", lambda m: f"（{m.group(1)}）", value)
