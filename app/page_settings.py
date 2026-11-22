"""Compact document-wide controls, with optional header details."""

import tkinter as tk
from tkinter import ttk


class DocumentSettingsPanel(ttk.Frame):
    """Always-visible controls without a redundant collapsible title bar."""

    title = "整卷设置"

    def __init__(self, parent):
        super().__init__(parent, style="InspectorGroup.TFrame")
        self.body = ttk.Frame(self, padding=(8, 7, 8, 8), style="InspectorGroup.TFrame")
        self.body.pack(fill=tk.X)


def build_page_settings(app, parent, group_type):
    app.template_label = ttk.Label(parent, textvariable=app.layout_preset_var, style="EditorHint.TLabel")
    app.template_label.pack(anchor=tk.W, pady=(2, 10))

    ttk.Label(parent, text="页边距（毫米）").pack(anchor=tk.W, pady=(3, 5))
    margins = ttk.Frame(parent)
    margins.pack(anchor=tk.W)
    for index, (label, variable) in enumerate((("上", app.margin_top_var), ("下", app.margin_bottom_var),
                                               ("左", app.margin_left_var), ("右", app.margin_right_var))):
        row, column = divmod(index, 2)
        ttk.Label(margins, text=label).grid(row=row, column=column * 2, sticky=tk.W, pady=3)
        ttk.Spinbox(margins, textvariable=variable, from_=5, to=45, increment=1, width=8).grid(
            row=row, column=column * 2 + 1, sticky=tk.W, padx=(6, 20), pady=3)

    actions = ttk.Frame(parent)
    actions.pack(fill=tk.X, pady=(12, 5))
    app.apply_page_settings_button = ttk.Button(actions, text="应用并查看", style="Primary.TButton",
                                                command=app.apply_global_settings)
    app.apply_page_settings_button.pack(side=tk.LEFT)
    ttk.Button(actions, text="恢复默认", command=lambda: app._restore_group("global")).pack(side=tk.LEFT, padx=8)
    app.page_settings_status_var = tk.StringVar(master=app, value="修改后自动更新，也可点击应用并查看。")
    ttk.Label(parent, textvariable=app.page_settings_status_var, wraplength=320,
              foreground="#245D8C").pack(fill=tk.X, pady=(0, 12))

    app.header_settings_group = group_type(parent, "卷首信息与文档类型", expanded=False)
    app.header_settings_group.pack(fill=tk.X)
    details = app.header_settings_group.body
    for row, (label, variable) in enumerate((("试卷名称", app.exam_name_var), ("科目名称", app.subject_name_var),
                                            ("试卷说明", app.exam_meta_var))):
        app._global_entry(details, row, label, variable, width=24)
    app.document_scope_var = tk.StringVar(master=app, value="全卷")
    ttk.Label(details, text="文档类型").grid(row=3, column=0, sticky=tk.W, pady=5)
    scope = ttk.Combobox(details, textvariable=app.document_scope_var, values=("全卷", "局部训练"),
                         state="readonly", width=12)
    scope.grid(row=3, column=1, sticky=tk.W, padx=7)
    scope.bind("<<ComboboxSelected>>", app._scope_changed)
    ttk.Label(details, text="修改卷首信息后，应用并查看会定位到首页。", wraplength=300,
              style="EditorHint.TLabel").grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(7, 0))
    details.columnconfigure(1, weight=1)
