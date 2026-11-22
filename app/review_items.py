"""Review prompts with stable links back to the exam structure."""
import tkinter as tk
from tkinter import ttk

from .score_summary import question_leaf_score


def review_items(raw, preview_warnings=()):
    blocks = raw.get("blocks", [])
    rows = []
    ids = {block.get("id"): index for index, block in enumerate(blocks) if block.get("id")}
    questions = [(i, b["question"]) for i, b in enumerate(blocks) if b.get("type") == "question"]
    for index, question in questions:
        if question.get("counts_toward_total", True) and question_leaf_score(question) is None:
            rows.append((f"第 {question.get('number', '?')} 题 · 待填写分值", index))
    if not questions and raw.get("document_kind") != "answer":
        rows.append(("识别到 0 道编号题，请核对文档结构。", None))
    for item in raw.get("diagnostics", []):
        if isinstance(item, dict):
            rows.append((str(item.get("message") or item.get("code", "识别提示")), ids.get(item.get("block"))))
        else:
            rows.append((str(item), None))
    rows.extend((str(message), None) for message in preview_warnings)
    return rows


class ReviewPanel(ttk.Frame):
    def __init__(self, parent, navigate):
        super().__init__(parent)
        self.navigate = navigate
        self.rows = []
        self.opened = False
        self.button = ttk.Button(self, text="待核对", command=self.toggle)
        self.button.pack(fill=tk.X)
        self.listbox = tk.Listbox(self, height=3, exportselection=False, activestyle="dotbox")
        self.listbox.bind("<ButtonRelease-1>", self._go)
        self.listbox.bind("<Return>", self._go)

    def set_items(self, items):
        if items != self.rows:
            self.rows = items
            self.listbox.delete(0, tk.END)
            for message, _index in items:
                self.listbox.insert(tk.END, message)
        self.button.configure(text=f"{'收起' if self.opened else '查看'}待核对事项（{len(items)}）")
        if items:
            self.button.pack(fill=tk.X, before=self.listbox if self.listbox.winfo_manager() else None)
        else:
            self.button.pack_forget()
            self.listbox.pack_forget()
            self.opened = False

    def toggle(self):
        self.opened = not self.opened
        if self.opened:
            self.listbox.pack(fill=tk.X)
        else:
            self.listbox.pack_forget()
        self.set_items(self.rows)

    def _go(self, _event=None):
        selection = self.listbox.curselection()
        if selection:
            self.navigate(self.rows[selection[0]][1])
