"""右栏：上下文属性检查器（内容、格式、整卷三段式紧凑响应面板）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..inspector_model import ContentObject, summary_text
from ..layout_presets import DEFAULT_PRESET, PRESET_LABELS
from .icons import fluent_icon
from .theme import PALETTE
from .widgets import CollapsibleCard, FluentCheckBox, FluentComboBox, SegmentedTabs, StatusPill

FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
SIZE_CHOICES = ("9", "10.5", "12", "14", "15", "16", "18", "22")
LINE_SPACING_CHOICES = ("1.0", "1.05", "1.25", "1.5", "2.0")
ALIGNMENT_CHOICES = ("左对齐", "居中", "右对齐", "两端对齐")
SPECIAL_INDENTS = ("无", "首行", "悬挂")
BATCH_SCOPES = ("当前题目", "当前大题", "整份试卷")
OPTION_LAYOUTS = ("四行单列", "两行两列")

ROLE_OBJECT_KEY = Qt.ItemDataRole.UserRole + 1


def _make_unit_input(
    initial: str,
    unit: str,
    *,
    min_width: int = 44,
    parent: QWidget | None = None,
) -> tuple[QWidget, QLineEdit]:
    """构建带右置单位标签的紧凑响应式数值输入框。"""

    container = QWidget(parent)
    container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    edit = QLineEdit(initial, container)
    edit.setFixedHeight(28)
    edit.setMinimumWidth(min_width)
    edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    layout.addWidget(edit, 1)

    if unit:
        suffix = QLabel(unit, container)
        suffix.setObjectName("MutedHint")
        layout.addWidget(suffix)

    return container, edit


def _set_invalid(widget: QWidget, invalid: bool) -> None:
    widget.setProperty("invalid", "true" if invalid else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class InspectorPanel(QFrame):
    """右栏：随当前节点和内容对象联动的“内容 / 格式 / 整卷”三段式属性检查器。"""

    tabChanged = Signal(int)
    collapseRequested = Signal()
    objectSelected = Signal(str)
    textEdited = Signal()
    selectionChangedHint = Signal()
    identityChanged = Signal()
    scoreCommitted = Signal()
    fontChanged = Signal()
    inlineDecorationRequested = Signal(dict)  # {"underline": bool} 或 {"emphasis": str}
    clearInlineRequested = Signal()
    paragraphChanged = Signal()
    optionsChanged = Signal()
    batchApplyRequested = Signal()
    resetGroupRequested = Signal(str)
    globalFieldEdited = Signal()
    scopeChanged = Signal()
    applyGlobalRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RightPanel")
        self._loading = False
        self._alignment_value = "左对齐"
        self._object_items: dict[str, QListWidgetItem] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏：对象标题 + 折叠按钮
        header = QFrame(self)
        header.setObjectName("PanelHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 10, 10, 10)
        h_layout.setSpacing(6)

        self.selection_title_label = QLabel("卷首信息", header)
        self.selection_title_label.setObjectName("PanelTitle")
        self.selection_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h_layout.addWidget(self.selection_title_label, 1)

        collapse_btn = QPushButton(header)
        collapse_btn.setProperty("variant", "ghost")
        collapse_btn.setIcon(fluent_icon("panel_right", PALETTE["text_secondary"], 15))
        collapse_btn.setToolTip("收起属性面板 (Ctrl+B)")
        collapse_btn.setFixedSize(26, 26)
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.clicked.connect(self.collapseRequested.emit)
        h_layout.addWidget(collapse_btn)

        root.addWidget(header)

        # 分段切换条（内容 / 格式 / 整卷）
        seg_wrap = QWidget(self)
        seg_layout = QVBoxLayout(seg_wrap)
        seg_layout.setContentsMargins(12, 10, 12, 6)
        seg_layout.setSpacing(0)

        self.inspector_tabs = SegmentedTabs(("内容", "格式", "整卷"), seg_wrap)
        self.inspector_tabs.currentChanged.connect(self._on_tab_changed)
        seg_layout.addWidget(self.inspector_tabs)
        root.addWidget(seg_wrap)

        # 仅纵向平滑滚动，杜绝横向溢出
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.pages_stack = QStackedWidget(self.scroll_area)
        self.pages_stack.setStyleSheet("QStackedWidget { background: transparent; }")

        self._content_page = self._build_content_page()
        self._format_page = self._build_format_page()
        self._global_page = self._build_global_page()

        self.pages_stack.addWidget(self._content_page)
        self.pages_stack.addWidget(self._format_page)
        self.pages_stack.addWidget(self._global_page)

        self.scroll_area.setWidget(self.pages_stack)
        root.addWidget(self.scroll_area, 1)

    def _build_content_page(self) -> QWidget:
        page = QWidget(self.pages_stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 4, 10, 14)
        layout.setSpacing(10)

        # 题型与分值卡片（仅题目节点显示）
        self.identity_card = QFrame(page)
        self.identity_card.setObjectName("InspectorCard")
        id_grid = QGridLayout(self.identity_card)
        id_grid.setContentsMargins(10, 9, 10, 9)
        id_grid.setHorizontalSpacing(8)
        id_grid.setVerticalSpacing(6)

        id_grid.addWidget(QLabel("题型", self.identity_card), 0, 0)
        self.kind_combo = FluentComboBox(self.identity_card)
        self.kind_combo.addItems(["客观题", "主观题"])
        self.kind_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.identityChanged))
        id_grid.addWidget(self.kind_combo, 0, 1)

        id_grid.addWidget(QLabel("分值", self.identity_card), 0, 2)
        score_wrap, self.score_entry = _make_unit_input("", "分", min_width=48, parent=self.identity_card)
        self.score_entry.setPlaceholderText("可留空")
        self.score_entry.textChanged.connect(lambda _t: self._emit_if_ready(self.identityChanged))
        self.score_entry.returnPressed.connect(lambda: self._emit_if_ready(self.scoreCommitted))
        self.score_entry.editingFinished.connect(lambda: self._emit_if_ready(self.scoreCommitted))
        id_grid.addWidget(score_wrap, 0, 3)

        id_grid.setColumnStretch(1, 3)
        id_grid.setColumnStretch(3, 2)
        layout.addWidget(self.identity_card)

        # 内容对象选择器卡片
        obj_card = CollapsibleCard("内容对象", expanded=True, parent=page)
        self.object_list = QListWidget(obj_card.body)
        self.object_list.setObjectName("ObjectList")
        self.object_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.object_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.object_list.setMinimumHeight(88)
        self.object_list.setMaximumHeight(165)
        self.object_list.currentItemChanged.connect(self._on_object_item_changed)
        obj_card.body_layout.addWidget(self.object_list)
        layout.addWidget(obj_card)

        # 选中内容正文编辑卡片
        text_card = CollapsibleCard("正文内容", expanded=True, parent=page)
        self.stem_text = QTextEdit(text_card.body)
        self.stem_text.setAcceptRichText(False)
        self.stem_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.stem_text.setMinimumHeight(180)
        self.stem_text.setUndoRedoEnabled(False)
        self.stem_text.textChanged.connect(lambda: self._emit_if_ready(self.textEdited))
        self.stem_text.selectionChanged.connect(self._on_text_selection_changed)
        text_card.body_layout.addWidget(self.stem_text)

        self.selection_hint_label = QLabel("未选择文字时，字体设置作用于当前内容对象。", text_card.body)
        self.selection_hint_label.setObjectName("MutedHint")
        self.selection_hint_label.setWordWrap(True)
        text_card.body_layout.addWidget(self.selection_hint_label)
        layout.addWidget(text_card, 1)

        return page

    def _build_format_page(self) -> QWidget:
        page = QWidget(self.pages_stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 4, 10, 14)
        layout.setSpacing(10)

        # 1. 字体卡片（紧凑两行布局，绝不超宽）
        self.font_card = CollapsibleCard(
            "字体",
            expanded=True,
            reset_callback=lambda: self.resetGroupRequested.emit("font"),
            parent=page,
        )
        font_row = QHBoxLayout()
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(6)

        self.font_combo = FluentComboBox()
        self.font_combo.addItems(list(FONT_CHOICES))
        self.font_combo.setMinimumWidth(84)
        self.font_combo.setToolTip("字体")
        self.font_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.fontChanged))
        font_row.addWidget(self.font_combo, 2)

        self.size_combo = FluentComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.addItems(list(SIZE_CHOICES))
        self.size_combo.setCurrentText("10.5")
        self.size_combo.setFixedWidth(84)
        self.size_combo.setToolTip("字号（磅，五号为 10.5）")
        self.size_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.fontChanged))
        font_row.addWidget(self.size_combo)

        self.bold_button = QPushButton()
        self.bold_button.setProperty("variant", "chip")
        self.bold_button.setCheckable(True)
        self.bold_button.setIcon(fluent_icon("bold", PALETTE["text"], 14))
        self.bold_button.setIconSize(QSize(14, 14))
        self.bold_button.setFixedSize(30, 30)
        self.bold_button.setToolTip("加粗")
        self.bold_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bold_button.toggled.connect(lambda _checked: self._emit_if_ready(self.fontChanged))
        font_row.addWidget(self.bold_button)
        self.font_card.body_layout.addLayout(font_row)

        # 字符装饰快选行：下划线、着重号与清除局部字符格式
        deco_row = QHBoxLayout()
        deco_row.setContentsMargins(0, 0, 0, 0)
        deco_row.setSpacing(6)
        self.underline_button = QPushButton("下划线")
        self.underline_button.setProperty("variant", "chip")
        self.underline_button.setCheckable(True)
        self.underline_button.setIcon(fluent_icon("underline", PALETTE["text"], 13))
        self.underline_button.setIconSize(QSize(13, 13))
        self.underline_button.setFixedHeight(27)
        self.underline_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.underline_button.clicked.connect(
            lambda checked: self._emit_deco({"underline": checked})
        )
        deco_row.addWidget(self.underline_button, 1)

        self.emphasis_button = QPushButton("着重号")
        self.emphasis_button.setProperty("variant", "chip")
        self.emphasis_button.setCheckable(True)
        self.emphasis_button.setIcon(fluent_icon("emphasis_dot", PALETTE["text"], 13))
        self.emphasis_button.setIconSize(QSize(13, 13))
        self.emphasis_button.setFixedHeight(27)
        self.emphasis_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.emphasis_button.clicked.connect(
            lambda checked: self._emit_deco({"emphasis": "dot" if checked else ""})
        )
        deco_row.addWidget(self.emphasis_button, 1)

        clear_inline_btn = QPushButton()
        clear_inline_btn.setProperty("variant", "chip")
        clear_inline_btn.setIcon(fluent_icon("clear_format", PALETTE["text_secondary"], 14))
        clear_inline_btn.setIconSize(QSize(14, 14))
        clear_inline_btn.setFixedSize(30, 27)
        clear_inline_btn.setToolTip("清除局部字符格式")
        clear_inline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_inline_btn.clicked.connect(self.clearInlineRequested.emit)
        deco_row.addWidget(clear_inline_btn)
        self.font_card.body_layout.addLayout(deco_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"QFrame {{ color: {PALETTE['border']}; background-color: {PALETTE['border']}; max-height: 1px; }}")
        self.font_card.body_layout.addWidget(sep)

        batch_row = QHBoxLayout()
        batch_row.setContentsMargins(0, 0, 0, 0)
        batch_row.setSpacing(6)
        self.batch_scope_combo = FluentComboBox()
        self.batch_scope_combo.addItems(list(BATCH_SCOPES))
        self.batch_scope_combo.setMinimumWidth(96)
        self.batch_scope_combo.setToolTip("批量应用范围")
        batch_row.addWidget(self.batch_scope_combo, 1)

        apply_same_btn = QPushButton("应用到同类")
        apply_same_btn.setProperty("variant", "accent-outline")
        apply_same_btn.setIcon(fluent_icon("apply_all", PALETTE["accent_text"], 14))
        apply_same_btn.setIconSize(QSize(14, 14))
        apply_same_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_same_btn.setToolTip("将当前对象格式批量应用到选定范围内的同类型对象")
        apply_same_btn.clicked.connect(self.batchApplyRequested.emit)
        batch_row.addWidget(apply_same_btn)
        self.font_card.body_layout.addLayout(batch_row)
        layout.addWidget(self.font_card)

        # 2. 段落卡片
        self.paragraph_card = CollapsibleCard(
            "段落",
            expanded=True,
            reset_callback=lambda: self.resetGroupRequested.emit("paragraph"),
            parent=page,
        )

        # 对齐四连矢量图标按钮组（紧凑现代，像 Word/Figma）
        align_row = QHBoxLayout()
        align_row.setContentsMargins(0, 0, 0, 0)
        align_row.setSpacing(5)
        align_lbl = QLabel("对齐")
        align_lbl.setFixedWidth(36)
        align_row.addWidget(align_lbl)

        self._align_group = QButtonGroup(self)
        self._align_group.setExclusive(True)
        self._align_buttons: dict[str, QPushButton] = {}
        align_specs = (
            ("左对齐", "align_left"),
            ("居中", "align_center"),
            ("右对齐", "align_right"),
            ("两端对齐", "align_justify"),
        )
        for idx, (label, icon_name) in enumerate(align_specs):
            btn = QPushButton()
            btn.setProperty("variant", "chip")
            btn.setCheckable(True)
            btn.setIcon(fluent_icon(icon_name, PALETTE["text"], 15))
            btn.setIconSize(QSize(15, 15))
            btn.setToolTip(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(28)
            self._align_group.addButton(btn, idx)
            self._align_buttons[label] = btn
            align_row.addWidget(btn)
        self._align_buttons["左对齐"].setChecked(True)
        self._align_group.idClicked.connect(self._on_alignment_clicked)
        self.paragraph_card.body_layout.addLayout(align_row)

        pg = QGridLayout()
        pg.setHorizontalSpacing(6)
        pg.setVerticalSpacing(7)

        pg.addWidget(QLabel("左缩进"), 0, 0)
        left_wrap, self.left_indent_edit = _make_unit_input("0", "字", min_width=40)
        self.left_indent_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(left_wrap, 0, 1)

        pg.addWidget(QLabel("右缩进"), 0, 2)
        right_wrap, self.right_indent_edit = _make_unit_input("0", "字", min_width=40)
        self.right_indent_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(right_wrap, 0, 3)

        pg.addWidget(QLabel("特殊"), 1, 0)
        self.special_indent_combo = FluentComboBox()
        self.special_indent_combo.addItems(list(SPECIAL_INDENTS))
        self.special_indent_combo.setMinimumWidth(62)
        self.special_indent_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(self.special_indent_combo, 1, 1)

        pg.addWidget(QLabel("数值"), 1, 2)
        amt_wrap, self.special_amount_edit = _make_unit_input("0", "字", min_width=40)
        self.special_amount_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(amt_wrap, 1, 3)

        pg.addWidget(QLabel("段前"), 2, 0)
        sb_wrap, self.space_before_edit = _make_unit_input("0", "磅", min_width=40)
        self.space_before_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(sb_wrap, 2, 1)

        pg.addWidget(QLabel("段后"), 2, 2)
        sa_wrap, self.space_after_edit = _make_unit_input("0", "磅", min_width=40)
        self.space_after_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(sa_wrap, 2, 3)

        pg.addWidget(QLabel("行距"), 3, 0)
        self.line_spacing_combo = FluentComboBox()
        self.line_spacing_combo.setEditable(True)
        self.line_spacing_combo.addItems(list(LINE_SPACING_CHOICES))
        self.line_spacing_combo.setCurrentText("1.25")
        self.line_spacing_combo.setMinimumWidth(62)
        self.line_spacing_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.paragraphChanged))
        pg.addWidget(self.line_spacing_combo, 3, 1)

        unit_mul = QLabel("倍行距")
        unit_mul.setObjectName("MutedHint")
        pg.addWidget(unit_mul, 3, 2, 1, 2)

        pg.setColumnStretch(1, 1)
        pg.setColumnStretch(3, 1)
        self.paragraph_card.body_layout.addLayout(pg)
        layout.addWidget(self.paragraph_card)

        # 3. 选择项卡片（仅题目节点显示）
        self.options_card = CollapsibleCard(
            "选择项",
            expanded=False,
            reset_callback=lambda: self.resetGroupRequested.emit("options"),
            parent=page,
        )
        og = QGridLayout()
        og.setHorizontalSpacing(6)
        og.setVerticalSpacing(7)

        og.addWidget(QLabel("排列"), 0, 0)
        self.option_layout_combo = FluentComboBox()
        self.option_layout_combo.addItems(list(OPTION_LAYOUTS))
        self.option_layout_combo.setMinimumWidth(82)
        self.option_layout_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.optionsChanged))
        og.addWidget(self.option_layout_combo, 0, 1)

        og.addWidget(QLabel("字体"), 0, 2)
        self.option_font_combo = FluentComboBox()
        self.option_font_combo.addItems(list(FONT_CHOICES))
        self.option_font_combo.setMinimumWidth(66)
        self.option_font_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.optionsChanged))
        og.addWidget(self.option_font_combo, 0, 3)

        og.addWidget(QLabel("字号"), 1, 0)
        self.option_size_combo = FluentComboBox()
        self.option_size_combo.setEditable(True)
        self.option_size_combo.addItems(["9", "10.5", "12"])
        self.option_size_combo.setCurrentText("10.5")
        self.option_size_combo.setMinimumWidth(66)
        self.option_size_combo.currentTextChanged.connect(lambda _t: self._emit_if_ready(self.optionsChanged))
        og.addWidget(self.option_size_combo, 1, 1)

        og.addWidget(QLabel("左缩进"), 2, 0)
        ol_wrap, self.option_left_edit = _make_unit_input("1.5", "字", min_width=40)
        self.option_left_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.optionsChanged))
        og.addWidget(ol_wrap, 2, 1)

        og.addWidget(QLabel("悬挂"), 2, 2)
        oh_wrap, self.option_hanging_edit = _make_unit_input("1.7", "字", min_width=40)
        self.option_hanging_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.optionsChanged))
        og.addWidget(oh_wrap, 2, 3)

        og.setColumnStretch(1, 1)
        og.setColumnStretch(3, 1)
        self.options_card.body_layout.addLayout(og)
        layout.addWidget(self.options_card)

        # 4. 分页卡片
        self.pagination_card = CollapsibleCard(
            "分页",
            expanded=False,
            reset_callback=lambda: self.resetGroupRequested.emit("pagination"),
            parent=page,
        )
        self.keep_next_check = FluentCheckBox("与下一段同页", self.pagination_card.body)
        self.keep_next_check.toggled.connect(lambda _v: self._emit_if_ready(self.paragraphChanged))
        self.pagination_card.body_layout.addWidget(self.keep_next_check)

        self.page_break_check = FluentCheckBox("当前对象前分页", self.pagination_card.body)
        self.page_break_check.toggled.connect(lambda _v: self._emit_if_ready(self.paragraphChanged))
        self.pagination_card.body_layout.addWidget(self.page_break_check)
        layout.addWidget(self.pagination_card)

        # 统一格式错误提示
        self.format_error_label = QLabel("", page)
        self.format_error_label.setObjectName("ErrorHint")
        self.format_error_label.setWordWrap(True)
        self.format_error_label.hide()
        layout.addWidget(self.format_error_label)

        layout.addStretch(1)
        return page

    def _build_global_page(self) -> QWidget:
        page = QWidget(self.pages_stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 4, 10, 14)
        layout.setSpacing(10)

        # 常显整卷页面边距卡片
        self.global_group = QFrame(page)
        self.global_group.setObjectName("InspectorCard")
        g_layout = QVBoxLayout(self.global_group)
        g_layout.setContentsMargins(12, 12, 12, 12)
        g_layout.setSpacing(10)

        tpl_row = QHBoxLayout()
        tpl_row.setContentsMargins(0, 0, 0, 0)
        self.template_label = StatusPill(PRESET_LABELS[DEFAULT_PRESET], "info", self.global_group)
        tpl_row.addWidget(self.template_label)
        tpl_row.addStretch(1)
        g_layout.addLayout(tpl_row)

        margin_header = QHBoxLayout()
        margin_header.setContentsMargins(0, 0, 0, 0)
        margin_header.setSpacing(6)
        margin_title = QLabel("页边距 (mm)", self.global_group)
        margin_title.setObjectName("InspectorCardTitle")
        margin_header.addWidget(margin_title)
        margin_header.addStretch(1)

        std_chip = QPushButton("国标", self.global_group)
        std_chip.setProperty("variant", "chip")
        std_chip.setToolTip("上下 25.4 mm · 左右 31.75 mm")
        std_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        std_chip.clicked.connect(lambda: self.apply_margin_preset(25.4, 25.4, 31.75, 31.75))
        margin_header.addWidget(std_chip)

        compact_chip = QPushButton("紧凑", self.global_group)
        compact_chip.setProperty("variant", "chip")
        compact_chip.setToolTip("上下 16 mm · 左右 20 mm")
        compact_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        compact_chip.clicked.connect(lambda: self.apply_margin_preset(16, 16, 20, 20))
        margin_header.addWidget(compact_chip)
        g_layout.addLayout(margin_header)

        mg = QGridLayout()
        mg.setHorizontalSpacing(8)
        mg.setVerticalSpacing(8)
        mg.addWidget(QLabel("上"), 0, 0)
        mt_wrap, self.margin_top_edit = _make_unit_input("25.4", "mm", min_width=46)
        mg.addWidget(mt_wrap, 0, 1)

        mg.addWidget(QLabel("下"), 0, 2)
        mb_wrap, self.margin_bottom_edit = _make_unit_input("25.4", "mm", min_width=46)
        mg.addWidget(mb_wrap, 0, 3)

        mg.addWidget(QLabel("左"), 1, 0)
        ml_wrap, self.margin_left_edit = _make_unit_input("31.75", "mm", min_width=46)
        mg.addWidget(ml_wrap, 1, 1)

        mg.addWidget(QLabel("右"), 1, 2)
        mr_wrap, self.margin_right_edit = _make_unit_input("31.75", "mm", min_width=46)
        mg.addWidget(mr_wrap, 1, 3)

        for edit in (
            self.margin_top_edit,
            self.margin_bottom_edit,
            self.margin_left_edit,
            self.margin_right_edit,
        ):
            edit.textChanged.connect(lambda _t: self._emit_if_ready(self.globalFieldEdited))

        mg.setColumnStretch(1, 1)
        mg.setColumnStretch(3, 1)
        g_layout.addLayout(mg)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.apply_page_settings_button = QPushButton("应用并查看", self.global_group)
        self.apply_page_settings_button.setProperty("variant", "primary")
        self.apply_page_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_page_settings_button.clicked.connect(self.applyGlobalRequested.emit)
        actions.addWidget(self.apply_page_settings_button)

        restore_btn = QPushButton("恢复默认", self.global_group)
        restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        restore_btn.clicked.connect(lambda: self.resetGroupRequested.emit("global"))
        actions.addWidget(restore_btn)
        actions.addStretch(1)
        g_layout.addLayout(actions)

        self.page_settings_status_label = QLabel("修改后自动更新，也可点击应用并查看。", self.global_group)
        self.page_settings_status_label.setObjectName("FeedbackInfo")
        self.page_settings_status_label.setWordWrap(True)
        g_layout.addWidget(self.page_settings_status_label)

        layout.addWidget(self.global_group)

        # 默认折叠的卷首信息与文档类型卡片
        self.header_settings_group = CollapsibleCard("卷首信息与文档类型", expanded=False, parent=page)
        hg = QGridLayout()
        hg.setHorizontalSpacing(8)
        hg.setVerticalSpacing(8)

        hg.addWidget(QLabel("试卷名称"), 0, 0)
        self.exam_name_edit = QLineEdit()
        self.exam_name_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.globalFieldEdited))
        hg.addWidget(self.exam_name_edit, 0, 1)

        hg.addWidget(QLabel("科目名称"), 1, 0)
        self.subject_name_edit = QLineEdit()
        self.subject_name_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.globalFieldEdited))
        hg.addWidget(self.subject_name_edit, 1, 1)

        hg.addWidget(QLabel("试卷说明"), 2, 0)
        self.exam_meta_edit = QLineEdit()
        self.exam_meta_edit.textChanged.connect(lambda _t: self._emit_if_ready(self.globalFieldEdited))
        hg.addWidget(self.exam_meta_edit, 2, 1)

        hg.addWidget(QLabel("文档类型"), 3, 0)
        self.document_scope_combo = FluentComboBox()
        self.document_scope_combo.addItems(["全卷", "局部训练"])
        self.document_scope_combo.activated.connect(lambda _i: self._emit_if_ready(self.scopeChanged))
        hg.addWidget(self.document_scope_combo, 3, 1)

        hg.setColumnStretch(1, 1)
        self.header_settings_group.body_layout.addLayout(hg)

        header_tip = QLabel("修改卷首信息后，“应用并查看”会自动定位到首页。", self.header_settings_group.body)
        header_tip.setObjectName("MutedHint")
        header_tip.setWordWrap(True)
        self.header_settings_group.body_layout.addWidget(header_tip)

        layout.addWidget(self.header_settings_group)
        layout.addStretch(1)
        return page

    # ---------- 内部信号转发 ----------

    def _emit_if_ready(self, signal: Signal) -> None:
        if not self._loading:
            signal.emit()

    def _emit_deco(self, patch: dict[str, Any]) -> None:
        if not self._loading:
            self.inlineDecorationRequested.emit(patch)

    def _on_tab_changed(self, index: int) -> None:
        self.pages_stack.setCurrentIndex(index)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.tabChanged.emit(index)

    def _on_object_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if self._loading or current is None:
            return
        key = current.data(ROLE_OBJECT_KEY)
        if key:
            self.objectSelected.emit(str(key))

    def _on_alignment_clicked(self, index: int) -> None:
        if 0 <= index < len(ALIGNMENT_CHOICES):
            self._alignment_value = ALIGNMENT_CHOICES[index]
            self._emit_if_ready(self.paragraphChanged)

    def _on_text_selection_changed(self) -> None:
        if not self._loading:
            self.selectionChangedHint.emit()

    # ---------- 公共读写接口 ----------

    def set_loading(self, loading: bool) -> None:
        self._loading = loading

    def set_selection_title(self, title: str) -> None:
        self.selection_title_label.setText(title)
        self.selection_title_label.setToolTip(title)

    def selection_title(self) -> str:
        return self.selection_title_label.text()

    def set_format_tab_enabled(self, enabled: bool) -> None:
        self.inspector_tabs.setTabEnabled(1, enabled)

    def set_question_mode(self, is_question: bool, *, kind: str = "客观题", score: str = "") -> None:
        self.identity_card.setVisible(is_question)
        self.options_card.setVisible(is_question)
        if is_question:
            prev = self._loading
            self._loading = True
            try:
                self.kind_combo.setCurrentText(kind)
                self.score_entry.setText(score)
                _set_invalid(self.score_entry, False)
            finally:
                self._loading = prev

    def populate_objects(self, items: list[ContentObject]) -> None:
        prev = self._loading
        self._loading = True
        try:
            self.object_list.clear()
            self._object_items.clear()
            for obj in items:
                row = QListWidgetItem(f"{obj.label}  ·  {summary_text(obj, limit=24)}")
                row.setData(ROLE_OBJECT_KEY, obj.key)
                row.setToolTip(f"{obj.label}：{obj.text}")
                self.object_list.addItem(row)
                self._object_items[obj.key] = row
        finally:
            self._loading = prev

    def update_object_summary(self, obj: ContentObject) -> None:
        row = self._object_items.get(obj.key)
        if row is not None:
            row.setText(f"{obj.label}  ·  {summary_text(obj, limit=24)}")
            row.setToolTip(f"{obj.label}：{obj.text}")

    def select_object_key(self, key: str) -> None:
        row = self._object_items.get(key)
        if row is None:
            return
        prev = self._loading
        self._loading = True
        try:
            self.object_list.setCurrentItem(row)
        finally:
            self._loading = prev

    def set_native_readonly_placeholder(self) -> None:
        prev = self._loading
        self._loading = True
        try:
            self.stem_text.setPlainText("此原生对象将在导出时保留，请在 Word 中检查表格或图片。")
            self.stem_text.setReadOnly(True)
            self.stem_text.setEnabled(False)
        finally:
            self._loading = prev

    def load_content_object(
        self,
        item: ContentObject,
        spec: dict[str, Any],
        inline_entries: list[dict[str, Any]],
        option_spec: dict[str, Any] | None = None,
        option_layout: str = "四行单列",
    ) -> None:
        """把当前语义对象的文本、行内字符样式、段落与选项属性载入检查器。"""

        self.set_format_error("")
        prev = self._loading
        self._loading = True
        try:
            self.stem_text.setEnabled(True)
            self.stem_text.setReadOnly(False)
            self.stem_text.setPlainText(item.text)
            self.apply_editor_style(item, spec, inline_entries)

            self.font_combo.setCurrentText(str(spec.get("font", "宋体")))
            self.size_combo.setCurrentText(self._clean_num(spec.get("size_pt", 10.5)))
            if self.size_combo.lineEdit() is not None:
                self.size_combo.lineEdit().setCursorPosition(0)
            self.bold_button.setChecked(bool(spec.get("bold", False)))
            self.underline_button.setChecked(any(bool(e.get("underline")) for e in inline_entries))
            self.emphasis_button.setChecked(any(bool(e.get("emphasis")) for e in inline_entries))

            self.left_indent_edit.setText(self._clean_num(spec.get("left_indent_chars", 0)))
            self.right_indent_edit.setText(self._clean_num(spec.get("right_indent_chars", 0)))
            self.special_indent_combo.setCurrentText(str(spec.get("special_indent", "无")))
            self.special_amount_edit.setText(self._clean_num(spec.get("special_indent_chars", 0)))
            self.set_alignment(str(spec.get("alignment", "左对齐")))
            self.line_spacing_combo.setCurrentText(self._clean_num(spec.get("line_spacing", 1.25)))
            self.space_before_edit.setText(self._clean_num(spec.get("space_before_pt", 0)))
            self.space_after_edit.setText(self._clean_num(spec.get("space_after_pt", 0)))
            self.keep_next_check.setChecked(bool(spec.get("keep_with_next", False)))
            self.page_break_check.setChecked(bool(spec.get("page_break_before", False)))

            if option_spec is not None:
                self.option_layout_combo.setCurrentText(option_layout)
                self.option_font_combo.setCurrentText(str(option_spec.get("option_font", "宋体")))
                self.option_size_combo.setCurrentText(self._clean_num(option_spec.get("option_size_pt", 10.5)))
                self.option_left_edit.setText(self._clean_num(option_spec.get("option_left_indent_chars", 1.5)))
                self.option_hanging_edit.setText(self._clean_num(option_spec.get("option_hanging_indent_chars", 1.7)))

            self.selection_hint_label.setText(f"当前对象：{item.label}。未选择文字时，字体设置作用于整个对象。")
        finally:
            self._loading = prev

    def apply_editor_style(
        self,
        item: ContentObject,
        spec: dict[str, Any],
        inline_entries: list[dict[str, Any]],
    ) -> None:
        """按真实中文字体与行内覆盖样式渲染编辑框文本，并高亮断句标记。"""

        prev = self._loading
        self._loading = True
        try:
            saved_cursor = self.stem_text.textCursor()
            sel_start = saved_cursor.selectionStart()
            sel_end = saved_cursor.selectionEnd()

            font_name = str(spec.get("font", "宋体"))
            size_pt = max(9.0, float(spec.get("size_pt", 10.5)))
            base_font = QFont(font_name)
            base_font.setPointSizeF(size_pt)
            base_font.setBold(bool(spec.get("bold", False)))
            self.stem_text.setFont(base_font)

            text = self.stem_text.toPlainText()
            doc_len = len(text)
            cursor = self.stem_text.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(doc_len, QTextCursor.MoveMode.KeepAnchor)
            base_fmt = QTextCharFormat()
            base_fmt.setFont(base_font)
            cursor.setCharFormat(base_fmt)

            for entry in inline_entries:
                start = max(0, min(doc_len, int(entry.get("start", 0))))
                end = max(start, min(doc_len, int(entry.get("end", 0))))
                if start >= end:
                    continue
                ef = QFont(str(entry.get("font", font_name)))
                ef.setPointSizeF(max(9.0, float(entry.get("size_pt", size_pt))))
                ef.setBold(bool(entry.get("bold", False)))
                fmt = QTextCharFormat()
                fmt.setFont(ef)
                if "underline" in entry and entry["underline"] is not None:
                    fmt.setFontUnderline(bool(entry["underline"]))
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)

            if item.role == "segmentation":
                marker_fmt = QTextCharFormat()
                marker_fmt.setBackground(QBrush(QColor(PALETTE["accent_soft"])))
                marker_fmt.setForeground(QBrush(QColor(PALETTE["accent_text"])))
                marker_fmt.setFontWeight(QFont.Weight.Bold)
                for idx, ch in enumerate(text):
                    if ch in "ABCDEFGH":
                        cursor.setPosition(idx)
                        cursor.setPosition(idx + 1, QTextCursor.MoveMode.KeepAnchor)
                        cursor.mergeCharFormat(marker_fmt)

            restore = self.stem_text.textCursor()
            restore.setPosition(min(sel_start, doc_len))
            if sel_end != sel_start:
                restore.setPosition(min(sel_end, doc_len), QTextCursor.MoveMode.KeepAnchor)
            self.stem_text.setTextCursor(restore)
        finally:
            self._loading = prev

    def set_alignment(self, alignment: str) -> None:
        target = alignment if alignment in self._align_buttons else "左对齐"
        self._alignment_value = target
        self._align_buttons[target].setChecked(True)

    def alignment(self) -> str:
        return self._alignment_value

    def set_format_error(self, message: str) -> None:
        self.format_error_label.setText(message)
        self.format_error_label.setVisible(bool(message))

    def set_score_invalid(self, invalid: bool) -> None:
        _set_invalid(self.score_entry, invalid)

    def set_margin_invalid(self, invalid: bool) -> None:
        for edit in (
            self.margin_top_edit,
            self.margin_bottom_edit,
            self.margin_left_edit,
            self.margin_right_edit,
        ):
            _set_invalid(edit, invalid)

    def set_page_settings_status(self, message: str, *, tone: str = "info") -> None:
        self.page_settings_status_label.setText(message)
        color = {
            "info": PALETTE["accent_text"],
            "success": PALETTE["success_text"],
            "warning": PALETTE["warning_text"],
            "error": PALETTE["danger_text"],
        }.get(tone, PALETTE["accent_text"])
        self.page_settings_status_label.setStyleSheet(f"QLabel {{ font-size: 12px; color: {color}; }}")

    def page_settings_status(self) -> str:
        return self.page_settings_status_label.text()

    def apply_margin_preset(self, top: float, bottom: float, left: float, right: float) -> None:
        self.margin_top_edit.setText(self._clean_num(top))
        self.margin_bottom_edit.setText(self._clean_num(bottom))
        self.margin_left_edit.setText(self._clean_num(left))
        self.margin_right_edit.setText(self._clean_num(right))

    def text_selection_range(self) -> tuple[int, int] | None:
        cursor = self.stem_text.textCursor()
        if not cursor.hasSelection():
            return None
        return cursor.selectionStart(), cursor.selectionEnd()

    def set_locked(self, locked: bool) -> None:
        self.inspector_tabs.setEnabled(not locked)
        self.pages_stack.setEnabled(not locked)

    @staticmethod
    def _clean_num(value: Any) -> str:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return str(value)
        text = f"{num:.4f}".rstrip("0").rstrip(".")
        return text or "0"


__all__ = ["InspectorPanel"]
