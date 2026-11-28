"""右栏：精简上下文属性检查器（「排版 / 整卷」两段式，专注精细缩进、题型分值与整卷设置）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..inspector_model import ContentObject
from ..layout_presets import DEFAULT_PRESET, PRESET_LABELS
from .icons import fluent_icon
from .theme import PALETTE
from .widgets import CollapsibleCard, FluentCheckBox, FluentComboBox, SegmentedTabs, StatusPill

FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
SPECIAL_INDENTS = ("无", "首行", "悬挂")
BATCH_SCOPES = ("当前题目", "当前大题", "整份试卷")
OPTION_LAYOUTS = ("四行单列", "两行两列")


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
    """右栏：「排版 / 整卷」两段式精细属性面板（基础字形与对齐已归入纸面上方 Word 格式条）。"""

    tabChanged = Signal(int)
    collapseRequested = Signal()
    identityChanged = Signal()
    scoreCommitted = Signal()
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
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶栏：对象标题 + 收起按钮
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
        collapse_btn.setToolTip("收起排版面板 (Ctrl+B)")
        collapse_btn.setFixedSize(26, 26)
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.clicked.connect(self.collapseRequested.emit)
        h_layout.addWidget(collapse_btn)

        root.addWidget(header)

        # 精简两段切换条：0 = 排版，1 = 整卷
        seg_wrap = QWidget(self)
        seg_layout = QVBoxLayout(seg_wrap)
        seg_layout.setContentsMargins(12, 10, 12, 6)
        seg_layout.setSpacing(0)

        self.inspector_tabs = SegmentedTabs(("排版", "整卷"), seg_wrap)
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

        self._layout_page = self._build_layout_page()
        self._global_page = self._build_global_page()

        self.pages_stack.addWidget(self._layout_page)
        self.pages_stack.addWidget(self._global_page)

        self.scroll_area.setWidget(self.pages_stack)
        root.addWidget(self.scroll_area, 1)

    def _build_layout_page(self) -> QWidget:
        page = QWidget(self.pages_stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 4, 10, 14)
        layout.setSpacing(10)

        # 1. 题目属性卡片（仅选中题目时自动出现）
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
        score_wrap, self.score_entry = _make_unit_input("", "分", min_width=46, parent=self.identity_card)
        self.score_entry.setPlaceholderText("可留空")
        self.score_entry.textChanged.connect(lambda _t: self._emit_if_ready(self.identityChanged))
        self.score_entry.returnPressed.connect(lambda: self._emit_if_ready(self.scoreCommitted))
        self.score_entry.editingFinished.connect(lambda: self._emit_if_ready(self.scoreCommitted))
        id_grid.addWidget(score_wrap, 0, 3)

        id_grid.setColumnStretch(1, 3)
        id_grid.setColumnStretch(3, 2)
        self.identity_card.hide()
        layout.addWidget(self.identity_card)

        # 2. 卷首或原生只读对象的温和说明卡片（仅对应场景可见）
        self.header_notice_card = QFrame(page)
        self.header_notice_card.setObjectName("InspectorCard")
        hn_layout = QVBoxLayout(self.header_notice_card)
        hn_layout.setContentsMargins(12, 11, 12, 11)
        hn_layout.setSpacing(6)
        self.header_notice_title = QLabel("卷首信息", self.header_notice_card)
        self.header_notice_title.setObjectName("InspectorCardTitle")
        hn_layout.addWidget(self.header_notice_title)
        self.header_notice_desc = QLabel(
            "卷首文字可直接在左侧纸面点击修改；卷首字体由内置高考真题模板统一控制，页边距和文档类型请在「整卷」中调整。",
            self.header_notice_card,
        )
        self.header_notice_desc.setObjectName("MutedHint")
        self.header_notice_desc.setWordWrap(True)
        hn_layout.addWidget(self.header_notice_desc)
        layout.addWidget(self.header_notice_card)

        # 3. 当前对象徽标与批量套用卡片（仅可格式化正文对象显示）
        self.batch_card = QFrame(page)
        self.batch_card.setObjectName("InspectorCard")
        bc_layout = QVBoxLayout(self.batch_card)
        bc_layout.setContentsMargins(10, 9, 10, 9)
        bc_layout.setSpacing(8)

        role_row = QHBoxLayout()
        role_row.setContentsMargins(0, 0, 0, 0)
        role_row.setSpacing(6)
        self.object_role_pill = StatusPill("当前：题干", "info", self.batch_card)
        role_row.addWidget(self.object_role_pill)
        role_row.addStretch(1)
        bc_layout.addLayout(role_row)

        batch_row = QHBoxLayout()
        batch_row.setContentsMargins(0, 0, 0, 0)
        batch_row.setSpacing(6)
        self.batch_scope_combo = FluentComboBox(self.batch_card)
        self.batch_scope_combo.addItems(list(BATCH_SCOPES))
        self.batch_scope_combo.setMinimumWidth(96)
        self.batch_scope_combo.setToolTip("批量应用范围")
        batch_row.addWidget(self.batch_scope_combo, 1)

        apply_same_btn = QPushButton("应用到同类", self.batch_card)
        apply_same_btn.setProperty("variant", "accent-outline")
        apply_same_btn.setIcon(fluent_icon("apply_all", PALETTE["accent_text"], 14))
        apply_same_btn.setIconSize(QSize(14, 14))
        apply_same_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_same_btn.setToolTip("将当前段落格式批量应用到选定范围内的同类型对象")
        apply_same_btn.clicked.connect(self.batchApplyRequested.emit)
        batch_row.addWidget(apply_same_btn)
        bc_layout.addLayout(batch_row)
        layout.addWidget(self.batch_card)

        # 4. 段落精细排版卡片（专注缩进与段距，绝不重复顶部工具条里的字体/对齐/行距）
        self.paragraph_card = CollapsibleCard(
            "缩进与段距",
            expanded=True,
            reset_callback=lambda: self.resetGroupRequested.emit("paragraph"),
            parent=page,
        )

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

        pg.setColumnStretch(1, 1)
        pg.setColumnStretch(3, 1)
        self.paragraph_card.body_layout.addLayout(pg)
        layout.addWidget(self.paragraph_card)

        # 5. 选择项专项排版卡片（仅题目节点显示）
        self.options_card = CollapsibleCard(
            "选择项排版",
            expanded=True,
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
        self.option_size_combo.setMinimumWidth(78)
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
        self.options_card.hide()
        layout.addWidget(self.options_card)

        # 6. 分页控制卡片
        self.pagination_card = CollapsibleCard(
            "分页控制",
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

    def _on_tab_changed(self, index: int) -> None:
        self.pages_stack.setCurrentIndex(index)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.tabChanged.emit(index)

    # ---------- 公共读写接口 ----------

    def set_loading(self, loading: bool) -> None:
        self._loading = loading

    def set_selection_title(self, title: str) -> None:
        self.selection_title_label.setText(title)
        self.selection_title_label.setToolTip(title)

    def selection_title(self) -> str:
        return self.selection_title_label.text()

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

    def set_native_readonly_placeholder(self) -> None:
        self.header_notice_title.setText("原生图表对象")
        self.header_notice_desc.setText("此原生表格或图片将在导出 Word 时完整保留，无需在此调整段落参数。")
        self.header_notice_card.show()
        self.batch_card.hide()
        self.paragraph_card.hide()
        self.pagination_card.hide()

    def load_content_object(
        self,
        item: ContentObject,
        spec: dict[str, Any],
        _inline_entries: list[dict[str, Any]] = (),
        option_spec: dict[str, Any] | None = None,
        option_layout: str = "四行单列",
    ) -> None:
        """把当前语义对象的精细排版与选择项参数载入检查器。"""

        self.set_format_error("")
        is_header = item.block_index is None
        self.header_notice_card.setVisible(is_header)
        self.batch_card.setVisible(not is_header)
        self.paragraph_card.setVisible(not is_header)
        self.pagination_card.setVisible(not is_header)

        if is_header:
            self.header_notice_title.setText(f"卷首 · {item.label}")
            self.header_notice_desc.setText(
                "卷首文字可直接在左侧纸面点击修改；卷首字体由内置高考真题模板统一控制，页边距和文档类型请在「整卷」中调整。"
            )
            return

        prev = self._loading
        self._loading = True
        try:
            self.object_role_pill.setText(f"当前：{item.label}")
            self.left_indent_edit.setText(self._clean_num(spec.get("left_indent_chars", 0)))
            self.right_indent_edit.setText(self._clean_num(spec.get("right_indent_chars", 0)))
            self.special_indent_combo.setCurrentText(str(spec.get("special_indent", "无")))
            self.special_amount_edit.setText(self._clean_num(spec.get("special_indent_chars", 0)))
            self.space_before_edit.setText(self._clean_num(spec.get("space_before_pt", 0)))
            self.space_after_edit.setText(self._clean_num(spec.get("space_after_pt", 0)))
            self.keep_next_check.setChecked(bool(spec.get("keep_with_next", False)))
            self.page_break_check.setChecked(bool(spec.get("page_break_before", False)))

            if option_spec is not None:
                self.option_layout_combo.setCurrentText(option_layout)
                self.option_font_combo.setCurrentText(str(option_spec.get("option_font", "宋体")))
                self.option_size_combo.setCurrentText(self._clean_num(option_spec.get("option_size_pt", 10.5)))
                if self.option_size_combo.lineEdit() is not None:
                    self.option_size_combo.lineEdit().setCursorPosition(0)
                self.option_left_edit.setText(self._clean_num(option_spec.get("option_left_indent_chars", 1.5)))
                self.option_hanging_edit.setText(self._clean_num(option_spec.get("option_hanging_indent_chars", 1.7)))
        finally:
            self._loading = prev

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
