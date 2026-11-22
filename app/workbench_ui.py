"""The desktop shell: one primary workflow, quiet secondary commands."""

import tkinter as tk
from tkinter import ttk

from .version import APP_VERSION


def build_workbench_shell(app) -> None:
    brand = ttk.Frame(app, style="Brand.TFrame", padding=(22, 14))
    brand.pack(fill=tk.X)
    ttk.Label(brand, text="语文试题排版工作台", style="Brand.TLabel").pack(side=tk.LEFT)
    ttk.Label(brand, text=f"v{APP_VERSION}  ·  本地处理", style="BrandMeta.TLabel").pack(side=tk.LEFT, padx=16)
    ttk.Label(brand, text="导入 Word  →  校对与调整  →  导出 Word", style="BrandMeta.TLabel").pack(side=tk.RIGHT)

    toolbar = ttk.Frame(app, style="Toolbar.TFrame", padding=(18, 10))
    toolbar.pack(fill=tk.X)
    ttk.Button(toolbar, text="导入 Word", command=app.import_new_exam, style="Primary.TButton").pack(side=tk.LEFT)
    app.cancel_import_button = ttk.Button(toolbar, text="取消导入", command=app.cancel_import)
    app.file_label_var = tk.StringVar(value="内置示例 · 可导入整卷、限时训练或参考答案")
    app.file_label = ttk.Label(toolbar, textvariable=app.file_label_var, style="Toolbar.TLabel", width=36, anchor=tk.W)
    app.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=16)
    app.export_button = ttk.Button(toolbar, text="导出 Word…", command=app.open_export_dialog, style="Primary.TButton")
    app.export_button.pack(side=tk.RIGHT)
    ttk.Button(toolbar, text="整卷设置", command=app.show_page_settings).pack(side=tk.RIGHT, padx=(6, 12))

    app.main_pane = ttk.Panedwindow(app, orient=tk.HORIZONTAL)
    app.main_pane.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
    left = ttk.Frame(app.main_pane, padding=12, style="Panel.TFrame")
    right = ttk.Frame(app.main_pane, padding=12, style="Panel.TFrame")
    app.main_pane.add(left, weight=4)
    app.main_pane.add(right, weight=7)
    app.editor_panel = left
    app._build_editor_panel(left)
    app._build_preview_panel(right)

    app.result_bar = ttk.Frame(app, style="Result.TFrame", padding=(20, 8))
    app.result_var = tk.StringVar()
    ttk.Label(app.result_bar, textvariable=app.result_var, style="Result.TLabel").pack(side=tk.LEFT, fill=tk.X, expand=True)
    ttk.Button(app.result_bar, text="打开文件", command=lambda: app.open_export_result(False)).pack(side=tk.RIGHT)
    ttk.Button(app.result_bar, text="打开文件夹", command=lambda: app.open_export_result(True)).pack(side=tk.RIGHT, padx=8)

    status = ttk.Frame(app, style="Status.TFrame", padding=(20, 6))
    status.pack(side=tk.BOTTOM, fill=tk.X)
    ttk.Label(status, textvariable=app.status_var, style="Status.TLabel").pack(side=tk.LEFT, fill=tk.X, expand=True)
    ttk.Label(status, text="公众号：蓑衣微言", style="Status.TLabel").pack(side=tk.RIGHT, padx=(12, 0))
    app.busy_bar = ttk.Progressbar(status, mode="indeterminate", length=90)
    app.busy_bar.pack(side=tk.RIGHT)


def apply_workbench_styles(window) -> None:
    style = ttk.Style(window)
    font = "Microsoft YaHei UI"
    style.configure(".", font=(font, 10), background="#FFFFFF", foreground="#25364A")
    style.configure("TFrame", background="#FFFFFF")
    style.configure("TLabel", background="#FFFFFF", foreground="#25364A")
    style.configure("Toolbar.TFrame", background="#F4F7FB")
    style.configure("Toolbar.TLabel", background="#F4F7FB", foreground="#596C82")
    style.configure("Brand.TFrame", background="#152C48")
    style.configure("Brand.TLabel", background="#152C48", foreground="#FFFFFF", font=(font, 17, "bold"))
    style.configure("BrandMeta.TLabel", background="#152C48", foreground="#BBCDE2", font=(font, 9))
    style.configure("Title.TLabel", background="#FFFFFF", foreground="#25364A", font=(font, 12, "bold"))
    style.configure("Status.TFrame", background="#F4F7FB")
    style.configure("Status.TLabel", background="#F4F7FB", foreground="#596C82", font=(font, 9))
    style.configure("Result.TFrame", background="#EAF7EF")
    style.configure("Result.TLabel", background="#EAF7EF", foreground="#216B43")
    style.configure("TButton", font=(font, 9), padding=(10, 6), background="#FFFFFF", bordercolor="#D7E0EB")
    style.configure("Primary.TButton", font=(font, 10, "bold"), background="#2468CF", bordercolor="#2468CF", padding=(16, 7))
    style.map("Primary.TButton", background=[("disabled", "#A7B8CF"), ("pressed", "#174B9A"), ("active", "#377BE1")])
    style.configure("Treeview", font=(font, 10), rowheight=29, borderwidth=0)
    style.configure("Treeview.Heading", font=(font, 9), padding=(6, 5), background="#F4F7FB")
    style.configure("Object.Treeview", font=(font, 9), rowheight=26)
    style.map("Treeview", background=[("selected", "#E1EDFF")], foreground=[("selected", "#174B9A")])
    style.configure("EditorHint.TLabel", background="#FFFFFF", foreground="#6A7C90", font=(font, 9), padding=0)
    style.configure("TCheckbutton", background="#FFFFFF", font=(font, 9))
    style.configure("TNotebook", background="#FFFFFF", borderwidth=0, tabmargins=0)
    style.configure("TNotebook.Tab", font=(font, 10), padding=(18, 7))
    style.map("TNotebook.Tab", background=[("selected", "#E1EDFF"), ("active", "#F4F7FB")], foreground=[("selected", "#174B9A")])
