"""左栏：试卷结构大纲树、分值汇总卡片、实时搜索与按需待核对抽屉。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..score_summary import ScoreSummary, calculate_score_summary, format_score
from .icons import fluent_icon, fluent_pixmap
from .theme import PALETTE
from .widgets import StatusPill

# 节点元数据角色：存储 block_index（int）或 None（卷首根节点）
ROLE_BLOCK_INDEX = Qt.ItemDataRole.UserRole + 1
ROLE_NODE_IID = Qt.ItemDataRole.UserRole + 2


def answer_tree_label(block: dict[str, Any]) -> tuple[str, str]:
    """返回答案类结构块的展示标题与类型标签。"""

    kind = block.get("type")
    if kind == "answer_section":
        return str(block.get("text", "答案章节")), "答案章节"
    if kind == "answer_subsection":
        return f"{block.get('name', '')}{block.get('meta', '')}", "答案模块"
    if kind == "answer_question":
        return str(block.get("header", "答案题目")), "答案"
    if kind == "answer_table":
        return "作文等级评分表", "原表格"
    paragraphs = block.get("paragraphs", [])
    text = str(paragraphs[0].get("text", "")) if paragraphs else "答案说明"
    return text[:28], "答案说明"


def block_display_label(block: dict[str, Any]) -> str:
    """提取结构块的简洁可读标题。"""

    kind = str(block.get("type", ""))
    if kind.startswith("answer_"):
        return answer_tree_label(block)[0]
    if kind == "question":
        q = block.get("question", {})
        return f"第 {q.get('number', '?')} 题"
    return str(
        block.get("text")
        or block.get("name")
        or block.get("title")
        or {
            "material": "阅读材料",
            "poetry": "古诗文",
            "instruction": "阅读提示",
        }.get(kind, "结构内容")
    )


def block_search_text(block: dict[str, Any]) -> str:
    """拼接结构块的可搜索文本。"""

    values = [str(value) for value in block.values() if not isinstance(value, (dict, list))]
    question = block.get("question", {})
    if isinstance(question, dict):
        values.extend(str(question.get(key, "")) for key in ("number", "stem"))
        values.extend(str(value) for value in question.get("options", []))
    values.extend(str(value) for value in block.get("paragraphs", []))
    return " ".join(values)


class ReviewDrawer(QFrame):
    """按需显现的待核对事项抽屉：无提醒时不占空间，有提醒时温和呈现并支持一键跳转。"""

    issueActivated = Signal(object)  # int | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[tuple[str, int | None]] = []
        self.opened = False

        self.setStyleSheet(
            f"ReviewDrawer {{ background-color: {PALETTE['warning_bg']}; "
            f"border: 1px solid {PALETTE['warning_border']}; border-radius: 8px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setIcon(fluent_icon("warning", PALETTE["warning_text"], 14))
        self.toggle_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {PALETTE['warning_text']}; "
            "font-size: 12px; font-weight: 600; text-align: left; padding: 2px 4px; }"
            "QPushButton:hover { background: rgba(154, 91, 0, 0.08); border-radius: 5px; }"
        )
        self.toggle_btn.clicked.connect(self.toggle)
        layout.addWidget(self.toggle_btn)

        self.listbox = QListWidget(self)
        self.listbox.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listbox.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.listbox.setStyleSheet(
            f"QListWidget {{ background: #FFFFFF; border: 1px solid {PALETTE['warning_border']}; "
            "border-radius: 6px; padding: 2px; font-size: 12px; }"
            f"QListWidget::item {{ padding: 4px 6px; border-radius: 4px; color: {PALETTE['text']}; }}"
            f"QListWidget::item:hover {{ background: {PALETTE['warning_bg']}; }}"
            f"QListWidget::item:selected {{ background: {PALETTE['warning_bg']}; color: {PALETTE['warning_text']}; font-weight: 600; }}"
        )
        self.listbox.itemClicked.connect(self._activate_item)
        self.listbox.itemActivated.connect(self._activate_item)
        self.listbox.hide()
        layout.addWidget(self.listbox)

        self.hide()

    def set_items(self, items: list[tuple[str, int | None]]) -> None:
        if items != self.rows:
            self.rows = list(items)
            self.listbox.clear()
            for message, block_index in self.rows:
                item = QListWidgetItem(message)
                item.setData(ROLE_BLOCK_INDEX, block_index)
                item.setToolTip(message)
                self.listbox.addItem(item)
        count = len(self.rows)
        if count == 0:
            self.opened = False
            self.listbox.hide()
            self.hide()
            return
        self.listbox.setFixedHeight(min(116, max(34, count * 28 + 8)))
        arrow = "▾" if self.opened else "▸"
        self.toggle_btn.setText(f"待核对事项（{count}）  {arrow}")
        self.show()

    def toggle(self) -> None:
        if not self.rows:
            return
        self.opened = not self.opened
        self.listbox.setVisible(self.opened)
        arrow = "▾" if self.opened else "▸"
        self.toggle_btn.setText(f"待核对事项（{len(self.rows)}）  {arrow}")

    def activate_row(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            self.listbox.setCurrentRow(row)
            self.issueActivated.emit(self.rows[row][1])

    def _activate_item(self, item: QListWidgetItem) -> None:
        self.issueActivated.emit(item.data(ROLE_BLOCK_INDEX))


class OutlinePanel(QFrame):
    """左栏：结构化分级试卷大纲、动态分值卡片、搜索过滤与待核对导航。"""

    nodeSelected = Signal(object)  # int | None（None 表示整份试卷/卷首根节点）
    issueSelected = Signal(object)  # int | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LeftPanel")
        self._items_by_iid: dict[str, QTreeWidgetItem] = {}
        self._raw_exam: dict[str, Any] = {"metadata": {}, "blocks": []}
        self._suppress_select = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self._run_search)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 面板标题栏
        header = QFrame(self)
        header.setObjectName("PanelHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 10, 10, 10)
        h_layout.setSpacing(4)

        title = QLabel("试卷大纲", header)
        title.setObjectName("PanelTitle")
        h_layout.addWidget(title)
        h_layout.addStretch(1)

        expand_btn = QPushButton(header)
        expand_btn.setProperty("variant", "ghost")
        expand_btn.setIcon(fluent_icon("expand_all", PALETTE["text_secondary"], 14))
        expand_btn.setToolTip("展开全部层级")
        expand_btn.setFixedSize(26, 26)
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        expand_btn.clicked.connect(self.tree_expand_all)
        h_layout.addWidget(expand_btn)

        collapse_btn = QPushButton(header)
        collapse_btn.setProperty("variant", "ghost")
        collapse_btn.setIcon(fluent_icon("collapse_all", PALETTE["text_secondary"], 14))
        collapse_btn.setToolTip("折叠大题层级")
        collapse_btn.setFixedSize(26, 26)
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.clicked.connect(self.tree_collapse_sections)
        h_layout.addWidget(collapse_btn)

        root.addWidget(header)

        # 摘要与搜索区
        meta_wrap = QWidget(self)
        meta_layout = QVBoxLayout(meta_wrap)
        meta_layout.setContentsMargins(10, 10, 10, 8)
        meta_layout.setSpacing(8)

        # 分值汇总卡片
        self.score_card = QFrame(meta_wrap)
        self.score_card.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['surface_alt']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 8px; }}"
        )
        score_layout = QVBoxLayout(self.score_card)
        score_layout.setContentsMargins(10, 8, 10, 8)
        score_layout.setSpacing(6)

        score_top = QHBoxLayout()
        score_top.setContentsMargins(0, 0, 0, 0)
        score_top.setSpacing(6)

        self.score_total_label = QLabel("0 / 150 分", self.score_card)
        self.score_total_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {PALETTE['text']}; background: transparent; border: none;"
        )
        score_top.addWidget(self.score_total_label)
        score_top.addStretch(1)

        self.score_delta_pill = StatusPill("待录入", "neutral", self.score_card)
        score_top.addWidget(self.score_delta_pill)
        score_layout.addLayout(score_top)

        self.score_progress = QProgressBar(self.score_card)
        self.score_progress.setTextVisible(False)
        self.score_progress.setRange(0, 150)
        self.score_progress.setValue(0)
        score_layout.addWidget(self.score_progress)

        meta_layout.addWidget(self.score_card)

        # 待核对事项按需抽屉
        self.review_drawer = ReviewDrawer(meta_wrap)
        self.review_drawer.issueActivated.connect(self.issueSelected.emit)
        meta_layout.addWidget(self.review_drawer)

        # 查找框（带前缀放大镜图标与一键清空按钮）
        self.search_input = QLineEdit(meta_wrap)
        self.search_input.setPlaceholderText("查找题号、题干或材料… (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumHeight(30)
        self.search_input.addAction(
            fluent_icon("search", PALETTE["text_muted"], 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_input.textChanged.connect(lambda _t: self._search_timer.start())
        meta_layout.addWidget(self.search_input)

        root.addWidget(meta_wrap)

        # 分层试卷结构树
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["内容", "类型", "分值"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)

        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 50)
        self.tree.setColumnWidth(2, 54)

        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        root.addWidget(self.tree, 1)

    def focus_search(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def clear_search(self) -> None:
        if self.search_input.text():
            self.search_input.clear()

    def tree_expand_all(self) -> None:
        self.tree.expandAll()

    def tree_collapse_sections(self) -> None:
        root_item = self._items_by_iid.get("document")
        if root_item is None:
            return
        self.tree.collapseAll()
        root_item.setExpanded(True)

    def populate(self, raw_exam: dict[str, Any], *, keep_selection: bool = True) -> ScoreSummary:
        """根据最新文档模型重建分层结构树并刷新分值展示。"""

        self._raw_exam = raw_exam
        previous_iid = self.current_iid() if keep_selection else None

        self._suppress_select = True
        try:
            self.tree.clear()
            self._items_by_iid.clear()

            root_text = "答案信息" if raw_exam.get("document_kind") == "answer" else "整份试卷"
            root_item = QTreeWidgetItem([root_text, "卷首", ""])
            root_item.setData(0, ROLE_BLOCK_INDEX, None)
            root_item.setData(0, ROLE_NODE_IID, "document")
            bold_font = QFont(self.tree.font())
            bold_font.setBold(True)
            root_item.setFont(0, bold_font)
            root_item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            root_item.setForeground(1, QBrush(QColor(PALETTE["accent_text"])))
            self.tree.addTopLevelItem(root_item)
            self._items_by_iid["document"] = root_item

            summary = calculate_score_summary(raw_exam)
            missing = set(summary.missing_questions)
            query = self.search_input.text().strip().casefold()

            current_section: QTreeWidgetItem | None = None
            current_subsection: QTreeWidgetItem | None = None

            for index, block in enumerate(raw_exam.get("blocks", [])):
                if not isinstance(block, dict):
                    continue
                kind = str(block.get("type", ""))
                if query and query not in block_search_text(block).casefold():
                    continue

                item = self._create_block_item(index, block, missing)

                # 搜索模式下直接在根下平铺匹配项，普通模式下按“大题 → 模块 → 内容/题目”优雅分层
                if query:
                    root_item.addChild(item)
                elif kind in {"section_title", "answer_section"}:
                    root_item.addChild(item)
                    current_section = item
                    current_subsection = None
                elif kind in {"subsection", "answer_subsection"}:
                    parent_node = current_section or root_item
                    parent_node.addChild(item)
                    current_subsection = item
                else:
                    parent_node = current_subsection or current_section or root_item
                    parent_node.addChild(item)

                self._items_by_iid[f"block-{index}"] = item

            self.tree.expandAll()

            if previous_iid and previous_iid in self._items_by_iid:
                target = self._items_by_iid[previous_iid]
                self.tree.setCurrentItem(target)
        finally:
            self._suppress_select = False

        self.refresh_score(raw_exam, summary)
        return summary

    def _create_block_item(
        self,
        index: int,
        block: dict[str, Any],
        missing_questions: set[str],
    ) -> QTreeWidgetItem:
        kind = str(block.get("type", ""))
        score_text = ""
        is_missing = False

        if kind == "question":
            q = block.get("question", {})
            number = str(q.get("number", "?"))
            stem = str(q.get("stem", "")).strip()
            label = f"{number}．{stem}" if stem else f"第 {number} 题"
            category = "客观" if q.get("kind") == "objective" else "主观"
            if q.get("score") is not None:
                score_val = q["score"]
                score_text = f"{score_val:g}" if isinstance(score_val, (int, float)) else str(score_val)
            elif number in missing_questions:
                score_text = "待填"
                is_missing = True
        elif kind.startswith("answer_"):
            label, category = answer_tree_label(block)
        else:
            label = block_display_label(block)
            category = {
                "section_title": "大题",
                "subsection": "模块",
                "instruction": "提示",
                "material": "材料",
                "poetry": "诗歌",
            }.get(kind, "内容")

        item = QTreeWidgetItem([label, category, score_text])
        item.setToolTip(0, label)
        item.setData(0, ROLE_BLOCK_INDEX, index)
        item.setData(0, ROLE_NODE_IID, f"block-{index}")
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 语义字体与色彩层次
        if kind in {"section_title", "answer_section"}:
            f = QFont(self.tree.font())
            f.setBold(True)
            item.setFont(0, f)
            item.setForeground(1, QBrush(QColor(PALETTE["accent_text"])))
        elif kind in {"subsection", "answer_subsection"}:
            f = QFont(self.tree.font())
            f.setWeight(QFont.Weight.DemiBold)
            item.setFont(0, f)
            item.setForeground(1, QBrush(QColor("#2B6CB0")))
        elif kind == "question":
            cat_color = "#0067C0" if category == "客观" else "#6B46C1"
            item.setForeground(1, QBrush(QColor(cat_color)))
            item.setForeground(2, QBrush(QColor(PALETTE["text_secondary"])))
        else:
            item.setForeground(1, QBrush(QColor(PALETTE["text_muted"])))

        if is_missing:
            warn_bg = QBrush(QColor(PALETTE["warning_bg"]))
            warn_fg = QBrush(QColor(PALETTE["warning_text"]))
            for col in range(3):
                item.setBackground(col, warn_bg)
            item.setForeground(2, warn_fg)
            warn_font = QFont(self.tree.font())
            warn_font.setBold(True)
            item.setFont(2, warn_font)

        return item

    def update_block_label(self, raw_exam: dict[str, Any], block_index: int | None) -> None:
        """局部刷新单个结构块在树中的标题、题型和分值显示。"""

        if block_index is None:
            return
        item = self._items_by_iid.get(f"block-{block_index}")
        if item is None:
            return
        blocks = raw_exam.get("blocks", [])
        if not (0 <= block_index < len(blocks)):
            return
        block = blocks[block_index]
        if block.get("type") != "question":
            item.setText(0, block_display_label(block))
            return
        q = block["question"]
        number = str(q.get("number", "?"))
        stem = str(q.get("stem", "")).strip()
        label = f"{number}．{stem}" if stem else f"第 {number} 题"
        category = "客观" if q.get("kind") == "objective" else "主观"
        score = q.get("score")
        counts = q.get("counts_toward_total", True)
        missing = score is None and counts
        score_text = "待填" if missing else ("" if score is None else f"{float(score):g}")

        item.setText(0, label)
        item.setToolTip(0, label)
        item.setText(1, category)
        item.setText(2, score_text)
        cat_color = "#0067C0" if category == "客观" else "#6B46C1"
        item.setForeground(1, QBrush(QColor(cat_color)))
        if missing:
            for col in range(3):
                item.setBackground(col, QBrush(QColor(PALETTE["warning_bg"])))
            item.setForeground(2, QBrush(QColor(PALETTE["warning_text"])))
        else:
            for col in range(3):
                item.setBackground(col, QBrush())
            item.setForeground(2, QBrush(QColor(PALETTE["text_secondary"])))

    def refresh_score(
        self,
        raw_exam: dict[str, Any],
        summary: ScoreSummary | None = None,
    ) -> ScoreSummary:
        """刷新分值汇总卡片和进度条状态。"""

        state = summary or calculate_score_summary(raw_exam)
        if raw_exam.get("document_kind") == "answer":
            self.score_total_label.setText("独立答案文档")
            self.score_total_label.setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {PALETTE['success_text']}; background: transparent; border: none;"
            )
            self.score_delta_pill.setText("免总分校验")
            self.score_delta_pill.set_tone("success")
            self.score_progress.hide()
            return state

        metadata = raw_exam.get("metadata", {})
        is_unbounded_practice = (
            metadata.get("document_scope") == "practice" and not metadata.get("declared_total_score")
        )
        if is_unbounded_practice:
            self.score_total_label.setText(f"已录入 {format_score(state.total)} 分")
        else:
            self.score_total_label.setText(
                f"{format_score(state.total)} / {format_score(state.target)} 分"
            )

        if state.complete:
            delta_text = f"合计 {format_score(state.total)} 分"
            tone = "success"
            bar_color = PALETTE["success_text"]
        elif state.difference < 0:
            delta_text = f"超出 {format_score(-state.difference)} 分"
            tone = "danger"
            bar_color = PALETTE["danger_text"]
        else:
            delta_text = f"还差 {format_score(state.difference)} 分"
            if state.missing_questions:
                delta_text += f" · {len(state.missing_questions)} 题待填"
            tone = "warning"
            bar_color = PALETTE["warning_text"]

        if state.complete and state.missing_questions:
            delta_text += f" · {len(state.missing_questions)} 题待填"
            tone = "warning"

        self.score_delta_pill.setText(delta_text)
        self.score_delta_pill.set_tone(tone)
        self.score_total_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {PALETTE['text']}; background: transparent; border: none;"
        )

        target_int = max(1, int(round(float(state.target))))
        total_int = max(0, min(target_int, int(round(float(state.total)))))
        if is_unbounded_practice:
            self.score_progress.hide()
        else:
            self.score_progress.show()
            self.score_progress.setRange(0, target_int)
            self.score_progress.setValue(total_int)
            self.score_progress.setStyleSheet(
                f"QProgressBar {{ background-color: {PALETTE['border']}; border: none; border-radius: 2px; max-height: 4px; }}"
                f"QProgressBar::chunk {{ background-color: {bar_color}; border-radius: 2px; }}"
            )
        return state

    def set_review_items(self, items: list[tuple[str, int | None]]) -> None:
        self.review_drawer.set_items(items)

    def current_iid(self) -> str | None:
        item = self.tree.currentItem()
        return str(item.data(0, ROLE_NODE_IID)) if item is not None else None

    def current_block_index(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, ROLE_BLOCK_INDEX) if item is not None else None

    def select_block(self, block_index: int | None, *, emit: bool = True) -> bool:
        iid = "document" if block_index is None else f"block-{block_index}"
        item = self._items_by_iid.get(iid)
        if item is None:
            return False
        previous_flag = self._suppress_select
        self._suppress_select = not emit
        try:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._suppress_select = previous_flag
        return True

    def select_first_question(self) -> bool:
        """优先选中第一道试题，答案文档选中第一条答案块，否则选中卷首。"""

        blocks = self._raw_exam.get("blocks", [])
        for index, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("type") == "question":
                if self.select_block(index, emit=True):
                    return True
        for index, block in enumerate(blocks):
            if isinstance(block, dict) and str(block.get("type", "")).startswith("answer_"):
                if self.select_block(index, emit=True):
                    return True
        return self.select_block(None, emit=True)

    def _run_search(self) -> None:
        self.populate(self._raw_exam, keep_selection=True)

    def _on_current_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if self._suppress_select or current is None:
            return
        self.nodeSelected.emit(current.data(0, ROLE_BLOCK_INDEX))


__all__ = ["OutlinePanel", "ReviewDrawer", "answer_tree_label", "block_display_label"]
