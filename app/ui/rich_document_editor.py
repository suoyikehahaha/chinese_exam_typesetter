"""Word 风格所见即所得 A4 富文本排版画布与浮动迷你格式栏。

核心设计：
1. 整份试卷按自然顺序渲染于居中悬浮的仿真 A4 白纸（真实页边距 + 投影）。
2. 每个语义内容对象（ContentObject）绑定到一个带 _BlockMeta 的 QTextBlock，
   通过 prefix / suffix 偏移映射保证选中文本与底层 raw_exam 数据 100% 精确对齐。
3. 支持直接点选、单段局部拖选、跨多段拖选、原地编辑文字、顶部 Word 快捷格式条
   以及选区上方自动弹出的浮动迷你格式栏（字体/字号/加粗/下划线/着重号/对齐/行距/清除格式）。
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Iterable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QTextBlock,
    QTextBlockFormat,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import load_layout
from ..inspector_model import (
    ContentObject,
    content_objects_for_block,
    inline_formats_for,
    metadata_content_objects,
    paragraph_format_for,
)
from ..page_layout import adjusted_layout
from ..paragraph_identity import paragraph_identity
from ..score_summary import format_score, parse_score
from .icons import fluent_icon
from .theme import PALETTE
from .widgets import FluentComboBox

FONT_MAP = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
}
FONT_CHOICES = ("宋体", "黑体", "楷体", "仿宋")
SIZE_CHOICES = ("9", "10.5", "12", "14", "15", "16", "18", "22")
LINE_SPACING_CHOICES = ("1.0", "1.05", "1.25", "1.5", "2.0")
ALIGNMENT_CHOICES = ("左对齐", "居中", "右对齐", "两端对齐")

# 自定义字符属性：标记中文着重号圆点与断句方框标记
EMPHASIS_DOT_PROP = int(QTextFormat.Property.UserProperty) + 101
SEGMENT_BOX_PROP = int(QTextFormat.Property.UserProperty) + 102

_NATIVE_MARKER_RE = re.compile(r"^\[\[NATIVE_(?:DRAWING|TABLE):(\d+)\]\]$")
_QUESTION_PREFIX_RE = re.compile(r"^\s*\d+[.．、]")
_SCORE_SUFFIX_RE = re.compile(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*$")
_A4_WIDTH_PX = 794.0
_PX_PER_MM = _A4_WIDTH_PX / 210.0
_PT_TO_PX = _PX_PER_MM * 25.4 / 72.0


@dataclass
class SelectedSegment:
    """选区在某一个语义内容对象上的精确区间。"""

    item: ContentObject
    start: int
    end: int
    whole_object: bool


class _BlockMeta(QTextBlockUserData):
    """绑定在 QTextBlock 上的语义锚点元数据。"""

    def __init__(
        self,
        item: ContentObject | None,
        *,
        prefix: str = "",
        suffix: str = "",
        editable: bool = True,
        block_index: int | None = None,
    ) -> None:
        super().__init__()
        self.item = item
        self.prefix = prefix
        self.suffix = suffix
        self.editable = editable
        self.block_index = item.block_index if item is not None else block_index


def _extract_body_text(full_text: str, meta: _BlockMeta) -> str:
    """从 QTextBlock 完整展示文字中剥离自动拼接的前缀和后缀，还原真实正文。"""

    text = full_text
    if meta.prefix and text.startswith(meta.prefix):
        text = text[len(meta.prefix) :]
    if meta.suffix and text.endswith(meta.suffix):
        text = text[: len(text) - len(meta.suffix)]
    return text


def _alignment_to_qt(value: str) -> Qt.AlignmentFlag:
    mapping = {
        "左对齐": Qt.AlignmentFlag.AlignLeft,
        "left": Qt.AlignmentFlag.AlignLeft,
        "居中": Qt.AlignmentFlag.AlignHCenter,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "右对齐": Qt.AlignmentFlag.AlignRight,
        "right": Qt.AlignmentFlag.AlignRight,
        "两端对齐": Qt.AlignmentFlag.AlignJustify,
        "justify": Qt.AlignmentFlag.AlignJustify,
    }
    return mapping.get(str(value), Qt.AlignmentFlag.AlignLeft)


def _load_source_image_map(raw_exam: dict[str, Any]) -> dict[int, QImage]:
    """尽力从导入的原件或内嵌资源中提取图片供富文本画布原位展示（失败静默降级）。"""

    native_objects = raw_exam.get("metadata", {}).get("native_objects", [])
    if not native_objects:
        return {}
    try:
        from ..source_document import source_document, source_read_session
        from docx.oxml.ns import qn

        images: dict[int, QImage] = {}
        with source_read_session():
            doc = source_document(raw_exam.get("metadata", {}))
            if doc is None:
                return {}
            paragraphs = list(doc.paragraphs)
            for idx, entry in enumerate(native_objects):
                if entry.get("kind") != "drawing":
                    continue
                p_idx = int(entry.get("source_paragraph_index", -1))
                if not (0 <= p_idx < len(paragraphs)):
                    continue
                blips = paragraphs[p_idx]._p.xpath(".//*[local-name()='blip']")
                for blip in blips:
                    rel_id = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
                    if not rel_id or rel_id not in doc.part.related_parts:
                        continue
                    blob = doc.part.related_parts[rel_id].blob
                    qimg = QImage.fromData(blob)
                    if not qimg.isNull():
                        images[idx] = qimg
                        break
        return images
    except Exception:
        return {}


def _source_marks_lookup(raw_exam: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """按段落文本指纹索引原文下划线、着重号与加粗范围。"""

    lookup: dict[str, list[dict[str, Any]]] = {}
    for entry in raw_exam.get("metadata", {}).get("source_decorations", []):
        text = str(entry.get("text", ""))
        ranges = entry.get("ranges", [])
        if text and ranges:
            lookup[paragraph_identity(text)] = list(ranges)
    return lookup


class _RichPaperTextEdit(QTextEdit):
    """仿真 A4 纸面内嵌富文本编辑器：支持着重号绘制、防破坏结构按键拦截与选区释放事件。"""

    selectionReleased = Signal()
    blockTextModified = Signal(int)  # QTextBlock.blockNumber()
    undoShortcutPressed = Signal()
    redoShortcutPressed = Signal()
    zoomWheelRequested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zoom = 1.0
        self._suppress_change = False
        self._highlighted_block_num: int = -1
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setStyleSheet(
            "QTextEdit { background-color: #FFFFFF; border: none; padding: 0px; "
            f"selection-background-color: {PALETTE['accent_soft']}; selection-color: {PALETTE['text']}; }}"
        )
        self.document().contentsChange.connect(self._on_contents_change)

    def set_zoom_factor(self, zoom: float) -> None:
        self._zoom = zoom

    def set_highlighted_block(self, block_num: int) -> None:
        self._highlighted_block_num = block_num
        self.viewport().update()

    def _on_contents_change(self, position: int, _removed: int, _added: int) -> None:
        if self._suppress_change:
            return
        block = self.document().findBlock(max(0, position))
        if block.isValid():
            self.blockTextModified.emit(block.blockNumber())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                self.zoomWheelRequested.emit(0.08 if delta > 0 else -0.08)
                event.accept()
                return
        # 交由外层纸台 QScrollArea 统一平滑滚动
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        mods = event.modifiers()
        key = event.key()

        # 撤销 / 重做统一交由主窗口快照栈管理，确保图文装饰与大纲完全同步
        if mods & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    self.redoShortcutPressed.emit()
                else:
                    self.undoShortcutPressed.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Y:
                self.redoShortcutPressed.emit()
                event.accept()
                return

        # 导航与复制键始终放行
        nav_keys = {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Escape,
        }
        if key in nav_keys or (mods & Qt.KeyboardModifier.ControlModifier and key in {Qt.Key.Key_C, Qt.Key.Key_A}):
            super().keyPressEvent(event)
            return

        cursor = self.textCursor()
        start_block = self.document().findBlock(cursor.selectionStart())
        end_block = self.document().findBlock(cursor.selectionEnd())
        meta = start_block.userData()
        if not isinstance(meta, _BlockMeta) or not meta.editable:
            event.accept()
            return

        # 阻止跨段删改或回车拆分语义段落，保护试卷结构树 100% 稳定
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            event.accept()
            return
        if start_block.blockNumber() != end_block.blockNumber():
            event.accept()
            return

        pos_in_block = cursor.positionInBlock()
        prefix_len = len(meta.prefix)
        suffix_start = max(prefix_len, len(start_block.text()) - len(meta.suffix))

        # 保护自动生成的题号前缀与末尾分值标记不被误删
        if cursor.hasSelection():
            sel_s = cursor.selectionStart() - start_block.position()
            sel_e = cursor.selectionEnd() - start_block.position()
            if sel_s < prefix_len or sel_e > suffix_start:
                event.accept()
                return
        else:
            if key == Qt.Key.Key_Backspace and pos_in_block <= prefix_len:
                event.accept()
                return
            if key == Qt.Key.Key_Delete and pos_in_block >= suffix_start:
                event.accept()
                return
            if pos_in_block < prefix_len or pos_in_block > suffix_start:
                if event.text():
                    event.accept()
                    return

        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        """粘贴时自动转单行纯文本，防止破坏结构段落。"""

        if not source.hasText():
            return
        cursor = self.textCursor()
        start_block = self.document().findBlock(cursor.selectionStart())
        end_block = self.document().findBlock(cursor.selectionEnd())
        meta = start_block.userData()
        if not isinstance(meta, _BlockMeta) or not meta.editable:
            return
        if start_block.blockNumber() != end_block.blockNumber():
            return
        clean = re.sub(r"[\r\n]+", " ", source.text())
        cursor.insertText(clean)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.selectionReleased.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. 大纲定位时的淡蓝脉冲高亮底
        if self._highlighted_block_num >= 0:
            block = self.document().findBlockByNumber(self._highlighted_block_num)
            if block.isValid():
                c_start = QTextCursor(block)
                c_end = QTextCursor(block)
                c_end.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                r1 = self.cursorRect(c_start)
                r2 = self.cursorRect(c_end)
                band = QRect(0, r1.top() - 2, self.viewport().width(), max(r1.height(), r2.bottom() - r1.top()) + 4)
                painter.fillRect(band, QColor(0, 103, 192, 22))
                painter.fillRect(QRect(0, band.top(), 4, band.height()), QColor(PALETTE["accent"]))

        # 2. 精确绘制可见区域内的中文着重号实心圆点与断句字母方框
        vis_rect = self.viewport().rect()
        top_cursor = self.cursorForPosition(QPoint(0, 0))
        bottom_cursor = self.cursorForPosition(QPoint(vis_rect.width(), vis_rect.height()))
        first_num = max(0, top_cursor.blockNumber() - 1)
        last_num = min(self.document().blockCount() - 1, bottom_cursor.blockNumber() + 1)

        dot_radius = max(1.3, 1.55 * self._zoom)
        dot_color = QColor("#111111")
        box_pen = QPen(QColor("#334155"), max(0.8, 0.9 * self._zoom))

        for bnum in range(first_num, last_num + 1):
            block = self.document().findBlockByNumber(bnum)
            if not block.isValid():
                continue
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    has_dot = bool(fmt.property(EMPHASIS_DOT_PROP))
                    has_box = bool(fmt.property(SEGMENT_BOX_PROP))
                    if has_dot or has_box:
                        frag_pos = frag.position()
                        frag_text = frag.text()
                        for idx, ch in enumerate(frag_text):
                            if ch.isspace():
                                continue
                            c1 = QTextCursor(self.document())
                            c1.setPosition(frag_pos + idx)
                            c2 = QTextCursor(self.document())
                            c2.setPosition(frag_pos + idx + 1)
                            r1 = self.cursorRect(c1)
                            r2 = self.cursorRect(c2)
                            if r1.top() == r2.top() and r2.left() > r1.left():
                                char_w = max(8, r2.left() - r1.left())
                            else:
                                char_w = max(8, QFontMetrics(fmt.font()).horizontalAdvance(ch))
                            if has_dot:
                                painter.setPen(Qt.PenStyle.NoPen)
                                painter.setBrush(dot_color)
                                cx = r1.left() + char_w * 0.5
                                extra_y = 2.0 * self._zoom if fmt.fontUnderline() else 0.0
                                cy = r1.bottom() + max(1.6, 2.2 * self._zoom) + extra_y
                                painter.drawEllipse(QPointF(cx, cy), dot_radius, dot_radius)
                            if has_box:
                                painter.setPen(box_pen)
                                painter.setBrush(Qt.BrushStyle.NoBrush)
                                painter.drawRect(QRect(r1.left(), r1.top() + 1, char_w, max(10, r1.height() - 2)))
                it += 1
        painter.end()


class FloatingFormatToolbar(QFrame):
    """Word 同款选区浮动迷你格式栏：拖选文本松手即现，一键设定字体/字号/加粗/下划线/着重号/对齐。"""

    fontPicked = Signal(str)
    sizePicked = Signal(float)
    boldToggled = Signal(bool)
    underlineToggled = Signal(bool)
    emphasisToggled = Signal(bool)
    clearFormatRequested = Signal()
    alignmentPicked = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._syncing = False
        self.setObjectName("FloatingMiniBar")
        self.setStyleSheet(
            f"#FloatingMiniBar {{ background-color: #FFFFFF; border: 1px solid {PALETTE['border_strong']}; "
            "border-radius: 9px; }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(15, 23, 42, 48))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(4)

        self.font_combo = FluentComboBox(self)
        self.font_combo.addItems(list(FONT_CHOICES))
        self.font_combo.setFixedSize(84, 26)
        self.font_combo.activated.connect(lambda _i: self._emit_font())
        layout.addWidget(self.font_combo)

        self.size_combo = FluentComboBox(self)
        self.size_combo.setEditable(True)
        self.size_combo.addItems(list(SIZE_CHOICES))
        self.size_combo.setFixedSize(82, 26)
        self.size_combo.activated.connect(lambda _i: self._emit_size())
        if self.size_combo.lineEdit() is not None:
            self.size_combo.lineEdit().returnPressed.connect(self._emit_size)
        layout.addWidget(self.size_combo)

        self._add_sep(layout)

        self.bold_btn = self._icon_btn("bold", "加粗", checkable=True)
        self.bold_btn.clicked.connect(lambda checked: self._emit_if_live(self.boldToggled, checked))
        layout.addWidget(self.bold_btn)

        self.underline_btn = self._icon_btn("underline", "下划线", checkable=True)
        self.underline_btn.clicked.connect(lambda checked: self._emit_if_live(self.underlineToggled, checked))
        layout.addWidget(self.underline_btn)

        self.emphasis_btn = self._icon_btn("emphasis_dot", "着重号", checkable=True)
        self.emphasis_btn.clicked.connect(lambda checked: self._emit_if_live(self.emphasisToggled, checked))
        layout.addWidget(self.emphasis_btn)

        clear_btn = self._icon_btn("clear_format", "清除局部字符格式", checkable=False)
        clear_btn.clicked.connect(self.clearFormatRequested.emit)
        layout.addWidget(clear_btn)

        self._add_sep(layout)

        for label, icon_name in (
            ("左对齐", "align_left"),
            ("居中", "align_center"),
            ("右对齐", "align_right"),
        ):
            btn = self._icon_btn(icon_name, label, checkable=False)
            btn.clicked.connect(lambda _c=False, a=label: self.alignmentPicked.emit(a))
            layout.addWidget(btn)

        self.adjustSize()
        self.hide()

    def _icon_btn(self, icon_name: str, tip: str, *, checkable: bool) -> QPushButton:
        btn = QPushButton(self)
        btn.setProperty("variant", "chip")
        btn.setCheckable(checkable)
        btn.setIcon(fluent_icon(icon_name, PALETTE["text"], 14))
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(26, 26)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _add_sep(self, layout: QHBoxLayout) -> None:
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedHeight(16)
        sep.setStyleSheet(f"QFrame {{ color: {PALETTE['border']}; background-color: {PALETTE['border']}; max-width: 1px; }}")
        layout.addWidget(sep)

    def _emit_if_live(self, sig: Signal, value: Any) -> None:
        if not self._syncing:
            sig.emit(value)

    def _emit_font(self) -> None:
        if not self._syncing:
            self.fontPicked.emit(self.font_combo.currentText())

    def _emit_size(self) -> None:
        if self._syncing:
            return
        try:
            val = float(self.size_combo.currentText().strip())
        except ValueError:
            return
        if 5 <= val <= 72:
            self.sizePicked.emit(val)

    def sync_state(self, state: dict[str, Any]) -> None:
        self._syncing = True
        try:
            if state.get("font") in FONT_CHOICES:
                self.font_combo.setCurrentText(str(state["font"]))
            if state.get("size_pt") is not None:
                text = f"{float(state['size_pt']):.4f}".rstrip("0").rstrip(".")
                self.size_combo.setCurrentText(text)
                if self.size_combo.lineEdit() is not None:
                    self.size_combo.lineEdit().setCursorPosition(0)
            self.bold_btn.setChecked(bool(state.get("bold", False)))
            self.underline_btn.setChecked(bool(state.get("underline", False)))
            self.emphasis_btn.setChecked(bool(state.get("emphasis", "")))
        finally:
            self._syncing = False


class RichDocumentEditor(QWidget):
    """Word 风格所见即所得 A4 富文本直编容器（顶部快捷格式条 + 仿真 A4 纸面 + 浮动迷你栏）。"""

    cursorObjectChanged = Signal(object)  # ContentObject
    objectTextEdited = Signal(object, str)  # ContentObject, new_text
    inlineStyleRequested = Signal(dict)  # {"font"|"size_pt"|"bold"|"underline"|"emphasis": val}
    clearInlineRequested = Signal()
    paragraphStyleRequested = Signal(dict)  # {"alignment"|"line_spacing": val}
    batchApplyRequested = Signal()
    undoRequested = Signal()
    redoRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zoom: float = 0.86
        self._raw_exam: dict[str, Any] = {"metadata": {}, "blocks": []}
        self._layout_path: Path | None = None
        self._key_to_block_num: dict[str, int] = {}
        self._block_index_to_first_num: dict[int, int] = {}
        self._syncing_bar = False
        self._pending_edited_blocks: set[int] = set()

        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(220)
        self._commit_timer.timeout.connect(self._flush_edited_blocks)

        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.setInterval(1500)
        self._highlight_timer.timeout.connect(lambda: self.paper_edit.set_highlighted_block(-1))

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Word 风格单行快捷格式条（在富文本直编视图中随时触手可及）
        ribbon = QFrame(self)
        ribbon.setStyleSheet(
            f"QFrame {{ background-color: #FFFFFF; border-bottom: 1px solid {PALETTE['border']}; }}"
        )
        r_layout = QHBoxLayout(ribbon)
        r_layout.setContentsMargins(8, 5, 8, 5)
        r_layout.setSpacing(4)

        self.font_combo = FluentComboBox(ribbon)
        self.font_combo.addItems(list(FONT_CHOICES))
        self.font_combo.setFixedSize(84, 27)
        self.font_combo.setToolTip("选中文字或当前段落字体")
        self.font_combo.activated.connect(self._on_bar_font)
        r_layout.addWidget(self.font_combo)

        self.size_combo = FluentComboBox(ribbon)
        self.size_combo.setEditable(True)
        self.size_combo.addItems(list(SIZE_CHOICES))
        self.size_combo.setCurrentText("10.5")
        self.size_combo.setFixedSize(84, 27)
        self.size_combo.setToolTip("字号（磅）")
        self.size_combo.activated.connect(self._on_bar_size)
        if self.size_combo.lineEdit() is not None:
            self.size_combo.lineEdit().setCursorPosition(0)
            self.size_combo.lineEdit().returnPressed.connect(self._on_bar_size)
        r_layout.addWidget(self.size_combo)

        self._add_sep(r_layout, ribbon)

        self.bold_btn = self._bar_btn("bold", "加粗", checkable=True, parent=ribbon)
        self.bold_btn.clicked.connect(lambda checked: self._emit_inline({"bold": checked}))
        r_layout.addWidget(self.bold_btn)

        self.underline_btn = self._bar_btn("underline", "下划线", checkable=True, parent=ribbon)
        self.underline_btn.clicked.connect(lambda checked: self._emit_inline({"underline": checked}))
        r_layout.addWidget(self.underline_btn)

        self.emphasis_btn = self._bar_btn("emphasis_dot", "着重号（字符下方实心圆点）", checkable=True, parent=ribbon)
        self.emphasis_btn.clicked.connect(lambda checked: self._emit_inline({"emphasis": "dot" if checked else ""}))
        r_layout.addWidget(self.emphasis_btn)

        clear_btn = self._bar_btn("clear_format", "清除选区或当前段局部字符格式", checkable=False, parent=ribbon)
        clear_btn.clicked.connect(self.clearInlineRequested.emit)
        r_layout.addWidget(clear_btn)

        self._add_sep(r_layout, ribbon)

        self._align_group = QButtonGroup(self)
        self._align_group.setExclusive(True)
        self._align_buttons: dict[str, QPushButton] = {}
        for idx, (label, icon_name) in enumerate(
            (("左对齐", "align_left"), ("居中", "align_center"), ("右对齐", "align_right"), ("两端对齐", "align_justify"))
        ):
            btn = self._bar_btn(icon_name, label, checkable=True, parent=ribbon)
            self._align_group.addButton(btn, idx)
            self._align_buttons[label] = btn
            r_layout.addWidget(btn)
        self._align_buttons["左对齐"].setChecked(True)
        self._align_group.idClicked.connect(self._on_bar_alignment)

        self._add_sep(r_layout, ribbon)

        self.line_spacing_combo = FluentComboBox(ribbon)
        self.line_spacing_combo.setEditable(True)
        self.line_spacing_combo.addItems(list(LINE_SPACING_CHOICES))
        self.line_spacing_combo.setCurrentText("1.25")
        self.line_spacing_combo.setFixedSize(82, 27)
        self.line_spacing_combo.setToolTip("行距倍数")
        self.line_spacing_combo.activated.connect(self._on_bar_line_spacing)
        if self.line_spacing_combo.lineEdit() is not None:
            self.line_spacing_combo.lineEdit().setCursorPosition(0)
            self.line_spacing_combo.lineEdit().returnPressed.connect(self._on_bar_line_spacing)
        r_layout.addWidget(self.line_spacing_combo)

        batch_btn = QPushButton("刷同类", ribbon)
        batch_btn.setProperty("variant", "chip")
        batch_btn.setIcon(fluent_icon("apply_all", PALETTE["accent_text"], 13))
        batch_btn.setIconSize(QSize(13, 13))
        batch_btn.setFixedHeight(27)
        batch_btn.setToolTip("将当前段落格式批量应用到同类内容对象")
        batch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        batch_btn.clicked.connect(self.batchApplyRequested.emit)
        r_layout.addWidget(batch_btn)

        r_layout.addStretch(1)

        # 纸面缩放
        z_out = self._bar_btn("zoom_out", "缩小纸面 (Ctrl+向下滚轮)", checkable=False, parent=ribbon)
        z_out.clicked.connect(lambda: self.change_zoom(-0.08))
        r_layout.addWidget(z_out)

        self.zoom_label = QLabel("86%", ribbon)
        self.zoom_label.setObjectName("MutedHint")
        self.zoom_label.setFixedWidth(36)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r_layout.addWidget(self.zoom_label)

        z_in = self._bar_btn("zoom_in", "放大纸面 (Ctrl+向上滚轮)", checkable=False, parent=ribbon)
        z_in.clicked.connect(lambda: self.change_zoom(0.08))
        r_layout.addWidget(z_in)

        root.addWidget(ribbon)

        # 2. A4 仿真纸面滚动舞台
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet(f"QScrollArea {{ background-color: {PALETTE['stage']}; border: none; }}")
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda _v: self.mini_bar.hide())

        self.stage = QWidget(self.scroll_area)
        self.stage.setObjectName("RichPaperStage")
        self.stage.setStyleSheet(f"#RichPaperStage {{ background-color: {PALETTE['stage']}; }}")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(20, 18, 20, 36)
        stage_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self.paper_card = QFrame(self.stage)
        self.paper_card.setObjectName("RichPaperCard")
        self.paper_card.setStyleSheet(
            "#RichPaperCard { background-color: #FFFFFF; border: 1px solid #CFD8E3; border-radius: 3px; }"
        )
        shadow = QGraphicsDropShadowEffect(self.paper_card)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(15, 23, 42, 34))
        self.paper_card.setGraphicsEffect(shadow)

        self.paper_layout = QVBoxLayout(self.paper_card)
        self.paper_layout.setContentsMargins(90, 72, 90, 72)
        self.paper_layout.setSpacing(0)

        self.paper_edit = _RichPaperTextEdit(self.paper_card)
        self.paper_edit.cursorPositionChanged.connect(self._on_cursor_changed)
        self.paper_edit.selectionChanged.connect(self._on_selection_changed)
        self.paper_edit.selectionReleased.connect(self._maybe_show_mini_bar)
        self.paper_edit.blockTextModified.connect(self._on_block_text_modified)
        self.paper_edit.undoShortcutPressed.connect(self.undoRequested.emit)
        self.paper_edit.redoShortcutPressed.connect(self.redoRequested.emit)
        self.paper_edit.zoomWheelRequested.connect(self.change_zoom)
        self.paper_edit.document().documentLayout().documentSizeChanged.connect(self._sync_paper_height)
        self.paper_layout.addWidget(self.paper_edit)

        stage_layout.addWidget(self.paper_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.scroll_area.setWidget(self.stage)
        root.addWidget(self.scroll_area, 1)

        # 3. 浮动迷你格式栏（作为子浮层挂在 RichDocumentEditor 上，不抢焦点）
        self.mini_bar = FloatingFormatToolbar(self)
        self.mini_bar.fontPicked.connect(lambda f: self._emit_inline_and_refocus({"font": f}))
        self.mini_bar.sizePicked.connect(lambda s: self._emit_inline_and_refocus({"size_pt": s}))
        self.mini_bar.boldToggled.connect(lambda b: self._emit_inline({"bold": b}))
        self.mini_bar.underlineToggled.connect(lambda u: self._emit_inline({"underline": u}))
        self.mini_bar.emphasisToggled.connect(lambda e: self._emit_inline({"emphasis": "dot" if e else ""}))
        self.mini_bar.clearFormatRequested.connect(self.clearInlineRequested.emit)
        self.mini_bar.alignmentPicked.connect(lambda a: self.paragraphStyleRequested.emit({"alignment": a}))

    def _bar_btn(self, icon_name: str, tip: str, *, checkable: bool, parent: QWidget) -> QPushButton:
        btn = QPushButton(parent)
        btn.setProperty("variant", "chip")
        btn.setCheckable(checkable)
        btn.setIcon(fluent_icon(icon_name, PALETTE["text"], 14))
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(27, 27)
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _add_sep(self, layout: QHBoxLayout, parent: QWidget) -> None:
        sep = QFrame(parent)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedHeight(18)
        sep.setStyleSheet(f"QFrame {{ color: {PALETTE['border']}; background-color: {PALETTE['border']}; max-width: 1px; }}")
        layout.addWidget(sep)

    # ==================== 缩放与仿真纸面几何 ====================

    def fit_width(self) -> None:
        vw = self.scroll_area.viewport().width()
        if vw <= 120:
            return
        self.set_zoom(max(0.55, min(1.25, (vw - 56) / _A4_WIDTH_PX)))

    def change_zoom(self, delta: float) -> None:
        self.set_zoom(self._zoom + delta)

    def set_zoom(self, value: float) -> None:
        clamped = max(0.55, min(1.45, value))
        if abs(clamped - self._zoom) < 0.005:
            return
        self._zoom = clamped
        self.zoom_label.setText(f"{round(self._zoom * 100)}%")
        self.paper_edit.set_zoom_factor(self._zoom)
        if self._layout_path is not None:
            self.load_document(self._raw_exam, self._layout_path, preserve_scroll=True)

    def _apply_paper_geometry(self, page_spec: dict[str, Any]) -> None:
        scale = self._zoom
        paper_w = max(420, round(_A4_WIDTH_PX * scale))
        left = round(float(page_spec.get("margin_left_mm", 31.75)) * _PX_PER_MM * scale)
        right = round(float(page_spec.get("margin_right_mm", 31.75)) * _PX_PER_MM * scale)
        top = round(float(page_spec.get("margin_top_mm", 25.4)) * _PX_PER_MM * scale)
        bottom = round(float(page_spec.get("margin_bottom_mm", 25.4)) * _PX_PER_MM * scale)
        self.paper_card.setFixedWidth(paper_w)
        self.paper_layout.setContentsMargins(left, top, right, bottom)
        content_w = max(200, paper_w - left - right - 2)
        self.paper_edit.setFixedWidth(content_w)
        self.paper_edit.document().setTextWidth(content_w)
        self._sync_paper_height()

    def _sync_paper_height(self) -> None:
        doc_h = int(round(self.paper_edit.document().size().height()))
        min_page_h = round(1123 * self._zoom) - self.paper_layout.contentsMargins().top() - self.paper_layout.contentsMargins().bottom()
        target_h = max(min_page_h, doc_h + 24)
        self.paper_edit.setFixedHeight(target_h)
        margins = self.paper_layout.contentsMargins()
        self.paper_card.setFixedHeight(target_h + margins.top() + margins.bottom() + 4)

    # ==================== 全卷富文本渲染 ====================

    def load_document(
        self,
        raw_exam: dict[str, Any],
        layout_path: Path,
        *,
        preserve_scroll: bool = True,
        restore_key: str | None = None,
    ) -> None:
        """将 raw_exam 完整渲染为仿真 A4 可编辑富文本文档。"""

        self._commit_timer.stop()
        self._pending_edited_blocks.clear()
        self.mini_bar.hide()

        vbar = self.scroll_area.verticalScrollBar()
        saved_ratio = vbar.value() / max(1, vbar.maximum()) if preserve_scroll and vbar.maximum() > 0 else 0.0

        self._raw_exam = raw_exam
        self._layout_path = layout_path
        layout = adjusted_layout(load_layout(layout_path), raw_exam)
        page_spec = layout.get("page", {})

        source_images = _load_source_image_map(raw_exam)
        source_marks = _source_marks_lookup(raw_exam)

        self._key_to_block_num.clear()
        self._block_index_to_first_num.clear()

        self.paper_edit._suppress_change = True
        self.paper_edit.blockSignals(True)
        try:
            self.paper_edit.clear()
            self.paper_edit.set_zoom_factor(self._zoom)
            self._apply_paper_geometry(page_spec)
            cursor = self.paper_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            first_block = True

            def append_block(
                item: ContentObject | None,
                text: str,
                spec: dict[str, Any],
                *,
                prefix: str = "",
                suffix: str = "",
                editable: bool = True,
                block_index: int | None = None,
                inline_entries: list[dict[str, Any]] = (),
                extra_marks: list[dict[str, Any]] = (),
                muted: bool = False,
            ) -> QTextBlock:
                nonlocal first_block
                if not first_block:
                    cursor.insertBlock()
                first_block = False
                block = cursor.block()
                meta = _BlockMeta(item, prefix=prefix, suffix=suffix, editable=editable, block_index=block_index)
                block.setUserData(meta)
                full_text = f"{prefix}{text}{suffix}"
                self._apply_block_content_and_style(
                    cursor,
                    block,
                    full_text,
                    spec,
                    item=item,
                    prefix=prefix,
                    inline_entries=inline_entries,
                    extra_marks=extra_marks,
                    muted=muted,
                )
                num = block.blockNumber()
                if item is not None:
                    self._key_to_block_num[item.key] = num
                    if item.block_index is not None and item.block_index not in self._block_index_to_first_num:
                        self._block_index_to_first_num[item.block_index] = num
                elif block_index is not None and block_index not in self._block_index_to_first_num:
                    self._block_index_to_first_num[block_index] = num
                return block

            # 1) 卷首或答案标题
            if raw_exam.get("document_kind") == "answer":
                for line in raw_exam.get("metadata", {}).get("answer_title_lines", []):
                    append_block(
                        None,
                        str(line),
                        {"font": "黑体", "size_pt": 15, "bold": False, "alignment": "居中", "line_spacing": 1.25},
                        editable=False,
                    )
            else:
                meta_items = metadata_content_objects(raw_exam)
                notice_started = False
                notice_idx = 0
                for item in meta_items:
                    if item.target == "notice" and not notice_started:
                        notice_started = True
                        append_block(
                            None,
                            "注意事项：",
                            {"font": "宋体", "size_pt": 10.5, "bold": False, "alignment": "左对齐", "line_spacing": 1.25},
                            editable=False,
                        )
                    spec = paragraph_format_for(raw_exam, item)
                    if item.target == "notice":
                        notice_idx += 1
                        prefix = "" if _QUESTION_PREFIX_RE.match(item.text) else f"{notice_idx}．"
                        append_block(item, item.text, spec, prefix=prefix, editable=True)
                    elif item.text.strip():
                        append_block(item, item.text, spec, editable=True)

            # 2) 主体结构块
            for block_index, block_data in enumerate(raw_exam.get("blocks", [])):
                if not isinstance(block_data, dict):
                    continue
                kind = str(block_data.get("type", ""))
                if kind == "answer_table":
                    append_block(
                        None,
                        "[ 作文等级评分表（原表格，导出 Word 时原样保留） ]",
                        {"font": "宋体", "size_pt": 10.0, "alignment": "居中", "space_before_pt": 4, "space_after_pt": 4},
                        editable=False,
                        block_index=block_index,
                        muted=True,
                    )
                    continue

                objects = content_objects_for_block(block_data, block_index)
                if not objects:
                    append_block(
                        None,
                        "[ 原生图表对象（导出 Word 时完整保留） ]",
                        {"font": "宋体", "size_pt": 10.0, "alignment": "居中"},
                        editable=False,
                        block_index=block_index,
                        muted=True,
                    )
                    continue

                # subsection 合并 name 与 meta 展示在同一行，视觉 100% 贴合真题
                if kind in {"subsection", "answer_subsection"}:
                    name_obj = objects[0]
                    meta_text = str(block_data.get("meta", ""))
                    spec = paragraph_format_for(raw_exam, name_obj)
                    inlines = inline_formats_for(raw_exam, name_obj)
                    append_block(
                        name_obj,
                        name_obj.text,
                        spec,
                        suffix=meta_text,
                        editable=True,
                        inline_entries=inlines,
                    )
                    continue

                for item in objects:
                    stripped = item.text.strip()
                    native_match = _NATIVE_MARKER_RE.fullmatch(stripped)
                    if native_match:
                        obj_idx = int(native_match.group(1))
                        if "DRAWING" in stripped and obj_idx in source_images:
                            self._insert_image_block(cursor, source_images[obj_idx], block_index, first_block)
                            first_block = False
                        else:
                            label = "[ 原生表格（导出时原样保留） ]" if "TABLE" in stripped else "[ 原生图片（导出时原样保留） ]"
                            append_block(
                                None,
                                label,
                                {"font": "宋体", "size_pt": 9.5, "alignment": "居中", "space_before_pt": 3, "space_after_pt": 3},
                                editable=False,
                                block_index=block_index,
                                muted=True,
                            )
                        continue

                    prefix = ""
                    suffix = ""
                    if kind == "question" and item.target == "stem":
                        q = block_data.get("question", {})
                        number = q.get("number")
                        if number is not None and not _QUESTION_PREFIX_RE.match(item.text):
                            prefix = f"{number}．"
                        score = parse_score(q.get("score"))
                        if score is not None and not _SCORE_SUFFIX_RE.search(item.text):
                            suffix = f"（{format_score(score)}分）"

                    spec = paragraph_format_for(raw_exam, item)
                    inlines = inline_formats_for(raw_exam, item)
                    extra_marks = source_marks.get(paragraph_identity(item.text), [])
                    append_block(
                        item,
                        item.text,
                        spec,
                        prefix=prefix,
                        suffix=suffix,
                        editable=True,
                        inline_entries=inlines,
                        extra_marks=extra_marks,
                    )

            self._sync_paper_height()
            if restore_key and restore_key in self._key_to_block_num:
                self.scroll_to_key(restore_key, highlight=False)
            elif preserve_scroll and vbar.maximum() > 0:
                QTimer.singleShot(0, lambda r=saved_ratio: vbar.setValue(int(round(r * vbar.maximum()))))
        finally:
            self.paper_edit.blockSignals(False)
            self.paper_edit._suppress_change = False

    def _insert_image_block(
        self,
        cursor: QTextCursor,
        image: QImage,
        block_index: int,
        first_block: bool,
    ) -> None:
        if not first_block:
            cursor.insertBlock()
        block = cursor.block()
        block.setUserData(_BlockMeta(None, editable=False, block_index=block_index))
        bf = QTextBlockFormat()
        bf.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        bf.setTopMargin(4 * self._zoom)
        bf.setBottomMargin(4 * self._zoom)
        cursor.setBlockFormat(bf)

        max_w = max(120, self.paper_edit.width() - 24)
        scaled = image if image.width() <= max_w else image.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
        res_name = f"exam-img-{block_index}-{block.blockNumber()}"
        self.paper_edit.document().addResource(QTextDocument.ResourceType.ImageResource, res_name, scaled)
        img_fmt = QTextImageFormat()
        img_fmt.setName(res_name)
        img_fmt.setWidth(scaled.width())
        img_fmt.setHeight(scaled.height())
        cursor.insertImage(img_fmt)
        if block_index not in self._block_index_to_first_num:
            self._block_index_to_first_num[block_index] = block.blockNumber()

    def _apply_block_content_and_style(
        self,
        cursor: QTextCursor,
        block: QTextBlock,
        full_text: str,
        spec: dict[str, Any],
        *,
        item: ContentObject | None,
        prefix: str,
        inline_entries: Iterable[dict[str, Any]] = (),
        extra_marks: Iterable[dict[str, Any]] = (),
        muted: bool = False,
        replace_text: bool = True,
    ) -> None:
        """为单个 QTextBlock 精确设置段落版式、基础字形及全部行内字符格式。"""

        scale = self._zoom
        size_pt = float(spec.get("size_pt", 10.5))
        font_cn = str(spec.get("font", "宋体"))
        font_family = FONT_MAP.get(font_cn, font_cn)
        bold = bool(spec.get("bold", False))
        char_px = size_pt * _PT_TO_PX * scale

        bf = QTextBlockFormat()
        bf.setAlignment(_alignment_to_qt(str(spec.get("alignment", "左对齐"))))
        bf.setTopMargin(float(spec.get("space_before_pt", 0)) * _PT_TO_PX * scale)
        bf.setBottomMargin(float(spec.get("space_after_pt", 0)) * _PT_TO_PX * scale + 2.5 * scale)

        line_spacing = max(0.8, min(4.0, float(spec.get("line_spacing", 1.25))))
        bf.setLineHeight(line_spacing * 100.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)

        left_chars = float(spec.get("left_indent_chars", 0) or 0)
        right_chars = float(spec.get("right_indent_chars", 0) or 0)
        special = str(spec.get("special_indent", "无"))
        amount = float(spec.get("special_indent_chars", 0) or 0)
        left_px = max(0.0, left_chars * char_px)
        right_px = max(0.0, right_chars * char_px)
        text_indent_px = 0.0
        if special == "首行":
            text_indent_px = amount * char_px
        elif special == "悬挂":
            # 悬挂缩进：后续行左缩进，首行回退
            if spec.get("indent_origin") == "paragraph":
                left_px += amount * char_px
                text_indent_px = -amount * char_px
            else:
                left_px = max(left_px, amount * char_px)
                text_indent_px = -amount * char_px

        bf.setLeftMargin(left_px)
        bf.setRightMargin(right_px)
        bf.setTextIndent(text_indent_px)
        cursor.setBlockFormat(bf)

        base_fmt = QTextCharFormat()
        qfont = QFont(font_family)
        qfont.setPointSizeF(max(6.0, size_pt * scale))
        qfont.setBold(bold)
        base_fmt.setFont(qfont)
        base_fmt.setForeground(QBrush(QColor(PALETTE["text_muted"] if muted else "#111827")))
        base_fmt.setFontUnderline(False)
        base_fmt.clearProperty(EMPHASIS_DOT_PROP)
        base_fmt.clearProperty(SEGMENT_BOX_PROP)

        if replace_text:
            cursor.insertText(full_text, base_fmt)
        else:
            sel = QTextCursor(block)
            sel.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            sel.setCharFormat(base_fmt)

        block_pos = block.position()
        block_len = len(block.text())
        prefix_len = len(prefix)

        def apply_char_range(start: int, end: int, entry: dict[str, Any]) -> None:
            s = max(0, min(block_len, start))
            e = max(s, min(block_len, end))
            if s >= e:
                return
            rng = QTextCursor(self.paper_edit.document())
            rng.setPosition(block_pos + s)
            rng.setPosition(block_pos + e, QTextCursor.MoveMode.KeepAnchor)
            cf = QTextCharFormat()
            if entry.get("font"):
                f_name = FONT_MAP.get(str(entry["font"]), str(entry["font"]))
                cf.setFontFamilies([f_name])
            if entry.get("size_pt") is not None:
                cf.setFontPointSize(max(6.0, float(entry["size_pt"]) * scale))
            if "bold" in entry and entry["bold"] is not None:
                cf.setFontWeight(QFont.Weight.Bold if entry["bold"] else QFont.Weight.Normal)
            if "underline" in entry and entry["underline"] is not None:
                cf.setFontUnderline(bool(entry["underline"]))
            if "emphasis" in entry and entry["emphasis"] is not None:
                cf.setProperty(EMPHASIS_DOT_PROP, bool(entry["emphasis"]))
            rng.mergeCharFormat(cf)

        # 叠加原文装饰（下划线/着重号/加粗）
        for mark in extra_marks:
            apply_char_range(
                prefix_len + int(mark.get("start", 0)),
                prefix_len + int(mark.get("end", 0)),
                {
                    "bold": True if mark.get("bold") else None,
                    "underline": True if mark.get("underline") else None,
                    "emphasis": str(mark.get("emphasis", "")) or None,
                },
            )

        # 叠加用户显式设定的行内字符格式
        for entry in inline_entries:
            apply_char_range(
                prefix_len + int(entry.get("start", 0)),
                prefix_len + int(entry.get("end", 0)),
                entry,
            )

        # 断句题字母 A-H 方框标记
        if item is not None and item.role == "segmentation":
            text_now = block.text()
            for idx, ch in enumerate(text_now):
                if idx >= prefix_len and ch in "ABCDEFGH":
                    rng = QTextCursor(self.paper_edit.document())
                    rng.setPosition(block_pos + idx)
                    rng.setPosition(block_pos + idx + 1, QTextCursor.MoveMode.KeepAnchor)
                    cf = QTextCharFormat()
                    cf.setProperty(SEGMENT_BOX_PROP, True)
                    cf.setFontFamilies(["SimSun"])
                    rng.mergeCharFormat(cf)

    def refresh_objects(self, raw_exam: dict[str, Any], keys: Iterable[str]) -> None:
        """原地局部刷新指定对象的排版样式（不重构整文档，完美保持选区与滚动位置）。"""

        self._raw_exam = raw_exam
        source_marks = _source_marks_lookup(raw_exam)
        saved_cursor = self.paper_edit.textCursor()
        anchor = saved_cursor.anchor()
        pos = saved_cursor.position()

        self.paper_edit._suppress_change = True
        self.paper_edit.blockSignals(True)
        try:
            for key in keys:
                bnum = self._key_to_block_num.get(key)
                if bnum is None:
                    continue
                block = self.paper_edit.document().findBlockByNumber(bnum)
                if not block.isValid():
                    continue
                meta = block.userData()
                if not isinstance(meta, _BlockMeta) or meta.item is None:
                    continue
                item = meta.item
                spec = paragraph_format_for(raw_exam, item)
                inlines = inline_formats_for(raw_exam, item)
                extra_marks = source_marks.get(paragraph_identity(item.text), [])
                cursor = QTextCursor(block)
                self._apply_block_content_and_style(
                    cursor,
                    block,
                    block.text(),
                    spec,
                    item=item,
                    prefix=meta.prefix,
                    inline_entries=inlines,
                    extra_marks=extra_marks,
                    replace_text=False,
                )
            self._sync_paper_height()
            max_pos = max(0, self.paper_edit.document().characterCount() - 1)
            restore = QTextCursor(self.paper_edit.document())
            restore.setPosition(min(anchor, max_pos))
            restore.setPosition(min(pos, max_pos), QTextCursor.MoveMode.KeepAnchor)
            self.paper_edit.setTextCursor(restore)
        finally:
            self.paper_edit.blockSignals(False)
            self.paper_edit._suppress_change = False

        self._sync_toolbars_from_cursor()
        self.paper_edit.viewport().update()

    def update_object_text(self, item: ContentObject, new_text: str) -> None:
        """当外部（右栏检查器）修改文字时，原地同步到对应 QTextBlock。"""

        bnum = self._key_to_block_num.get(item.key)
        if bnum is None:
            return
        block = self.paper_edit.document().findBlockByNumber(bnum)
        if not block.isValid():
            return
        meta = block.userData()
        if not isinstance(meta, _BlockMeta):
            return
        expected = f"{meta.prefix}{new_text}{meta.suffix}"
        if block.text() == expected:
            meta.item = item
            return
        self.paper_edit._suppress_change = True
        self.paper_edit.blockSignals(True)
        try:
            meta.item = item
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            spec = paragraph_format_for(self._raw_exam, item)
            inlines = inline_formats_for(self._raw_exam, item)
            self._apply_block_content_and_style(
                cursor,
                block,
                expected,
                spec,
                item=item,
                prefix=meta.prefix,
                inline_entries=inlines,
                replace_text=True,
            )
            self._sync_paper_height()
        finally:
            self.paper_edit.blockSignals(False)
            self.paper_edit._suppress_change = False

    # ==================== 选区解析、定位与交互联动 ====================

    def selected_segments(self) -> list[SelectedSegment]:
        """解析当前光标或选区覆盖的全部可编辑语义对象及其精确字符区间。"""

        cursor = self.paper_edit.textCursor()
        doc = self.paper_edit.document()
        if not cursor.hasSelection():
            block = doc.findBlock(cursor.position())
            meta = block.userData() if block.isValid() else None
            if isinstance(meta, _BlockMeta) and meta.item is not None and meta.editable:
                return [SelectedSegment(meta.item, 0, len(meta.item.text), True)]
            return []

        sel_start = cursor.selectionStart()
        sel_end = cursor.selectionEnd()
        start_num = doc.findBlock(sel_start).blockNumber()
        end_num = doc.findBlock(sel_end).blockNumber()

        result: list[SelectedSegment] = []
        for bnum in range(start_num, end_num + 1):
            block = doc.findBlockByNumber(bnum)
            if not block.isValid():
                continue
            meta = block.userData()
            if not isinstance(meta, _BlockMeta) or meta.item is None or not meta.editable:
                continue
            b_pos = block.position()
            b_text = block.text()
            prefix_len = len(meta.prefix)
            body_len = max(0, len(b_text) - prefix_len - len(meta.suffix))

            local_s = max(prefix_len, sel_start - b_pos) if bnum == start_num else prefix_len
            local_e = min(prefix_len + body_len, sel_end - b_pos) if bnum == end_num else (prefix_len + body_len)
            rel_s = max(0, local_s - prefix_len)
            rel_e = max(rel_s, local_e - prefix_len)
            if rel_e > rel_s or (body_len == 0 and start_num == end_num):
                whole = rel_s == 0 and rel_e >= body_len
                result.append(SelectedSegment(meta.item, rel_s, rel_e, whole))
        return result

    def has_text_selection(self) -> bool:
        return self.paper_edit.textCursor().hasSelection()

    def current_object(self) -> ContentObject | None:
        block = self.paper_edit.document().findBlock(self.paper_edit.textCursor().position())
        meta = block.userData() if block.isValid() else None
        return meta.item if isinstance(meta, _BlockMeta) else None

    def scroll_to_key(self, key: str | int, *, highlight: bool = True) -> None:
        """根据内容对象键或 block_index 平滑滚动定位到对应仿真纸面段落。"""

        bnum = self._key_to_block_num.get(key) if isinstance(key, str) else self._block_index_to_first_num.get(int(key))
        if bnum is None:
            return
        block = self.paper_edit.document().findBlockByNumber(bnum)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        rect = self.paper_edit.cursorRect(cursor)
        paper_top = self.paper_card.y() + self.paper_edit.y()
        target_y = max(0, paper_top + rect.top() - int(self.scroll_area.viewport().height() * 0.32))
        self.scroll_area.verticalScrollBar().setValue(target_y)
        if highlight:
            self.paper_edit.set_highlighted_block(bnum)
            self._highlight_timer.start()

    def _on_block_text_modified(self, block_num: int) -> None:
        self._pending_edited_blocks.add(block_num)
        self._commit_timer.start()

    def flush_pending_text(self) -> None:
        self._commit_timer.stop()
        self._flush_edited_blocks()

    def _flush_edited_blocks(self) -> None:
        if not self._pending_edited_blocks:
            return
        nums = sorted(self._pending_edited_blocks)
        self._pending_edited_blocks.clear()
        for bnum in nums:
            block = self.paper_edit.document().findBlockByNumber(bnum)
            if not block.isValid():
                continue
            meta = block.userData()
            if not isinstance(meta, _BlockMeta) or meta.item is None or not meta.editable:
                continue
            new_text = _extract_body_text(block.text(), meta)
            if new_text != meta.item.text:
                old_item = meta.item
                meta.item = ContentObject(
                    old_item.key,
                    old_item.block_index,
                    old_item.target,
                    old_item.target_index,
                    old_item.role,
                    old_item.label,
                    new_text,
                    old_item.line_index,
                )
                self.objectTextEdited.emit(old_item, new_text)

    def _on_cursor_changed(self) -> None:
        item = self.current_object()
        self._sync_toolbars_from_cursor()
        if item is not None:
            self.cursorObjectChanged.emit(item)

    def _on_selection_changed(self) -> None:
        self._sync_toolbars_from_cursor()
        if self.paper_edit.textCursor().hasSelection():
            # 建立选区时关闭大纲定位脉冲条，保持纸面清爽聚焦
            self.paper_edit.set_highlighted_block(-1)
        else:
            self.mini_bar.hide()

    def _maybe_show_mini_bar(self) -> None:
        cursor = self.paper_edit.textCursor()
        if not cursor.hasSelection() or not self.selected_segments():
            self.mini_bar.hide()
            return
        self._sync_toolbars_from_cursor()
        # 计算选区起止中点坐标，优雅定位到选区上方（贴顶时自动翻到下方）
        c_start = QTextCursor(self.paper_edit.document())
        c_start.setPosition(cursor.selectionStart())
        c_end = QTextCursor(self.paper_edit.document())
        c_end.setPosition(cursor.selectionEnd())
        r_start = self.paper_edit.cursorRect(c_start)
        r_end = self.paper_edit.cursorRect(c_end)
        mid_x = (r_start.left() + r_end.right()) // 2
        top_pt = self.paper_edit.viewport().mapTo(self, QPoint(mid_x, min(r_start.top(), r_end.top())))
        bot_pt = self.paper_edit.viewport().mapTo(self, QPoint(mid_x, max(r_start.bottom(), r_end.bottom())))

        self.mini_bar.adjustSize()
        bar_w = self.mini_bar.width()
        bar_h = self.mini_bar.height()
        x = max(10, min(self.width() - bar_w - 10, top_pt.x() - bar_w // 2))
        y = top_pt.y() - bar_h - 10
        if y < 44:
            y = min(self.height() - bar_h - 10, bot_pt.y() + 10)
        self.mini_bar.move(x, y)
        self.mini_bar.raise_()
        self.mini_bar.show()

    def _current_format_snapshot(self) -> dict[str, Any]:
        cursor = self.paper_edit.textCursor()
        probe = QTextCursor(self.paper_edit.document())
        if cursor.hasSelection():
            probe.setPosition(min(cursor.selectionStart() + 1, self.paper_edit.document().characterCount() - 1))
        else:
            probe.setPosition(cursor.position())
        cf = probe.charFormat()
        item = self.current_object()
        para_spec = paragraph_format_for(self._raw_exam, item) if item is not None else {}

        raw_family = cf.font().family()
        reverse_map = {v.lower(): k for k, v in FONT_MAP.items()}
        font_cn = reverse_map.get(raw_family.lower(), str(para_spec.get("font", "宋体")))
        pt = cf.fontPointSize()
        size_pt = round(pt / self._zoom, 1) if pt > 0 else float(para_spec.get("size_pt", 10.5))

        return {
            "font": font_cn,
            "size_pt": size_pt,
            "bold": cf.fontWeight() >= QFont.Weight.Bold,
            "underline": cf.fontUnderline(),
            "emphasis": "dot" if cf.property(EMPHASIS_DOT_PROP) else "",
            "alignment": str(para_spec.get("alignment", "左对齐")),
            "line_spacing": float(para_spec.get("line_spacing", 1.25)),
        }

    def _sync_toolbars_from_cursor(self) -> None:
        state = self._current_format_snapshot()
        self._syncing_bar = True
        try:
            if state["font"] in FONT_CHOICES:
                self.font_combo.setCurrentText(state["font"])
            size_text = f"{state['size_pt']:.4f}".rstrip("0").rstrip(".")
            self.size_combo.setCurrentText(size_text)
            if self.size_combo.lineEdit() is not None:
                self.size_combo.lineEdit().setCursorPosition(0)
            self.bold_btn.setChecked(state["bold"])
            self.underline_btn.setChecked(state["underline"])
            self.emphasis_btn.setChecked(bool(state["emphasis"]))
            if state["alignment"] in self._align_buttons:
                self._align_buttons[state["alignment"]].setChecked(True)
            ls_text = f"{state['line_spacing']:.4f}".rstrip("0").rstrip(".")
            self.line_spacing_combo.setCurrentText(ls_text)
            if self.line_spacing_combo.lineEdit() is not None:
                self.line_spacing_combo.lineEdit().setCursorPosition(0)
            self.mini_bar.sync_state(state)
        finally:
            self._syncing_bar = False

    # ==================== 顶部快捷条触发 ====================

    def select_object_range(
        self,
        key: str,
        start: int,
        end: int,
        *,
        show_mini_bar: bool = False,
    ) -> bool:
        """按内容对象键与相对字符偏移精确选中一段文字（便于交互定位与自动化测试）。"""

        bnum = self._key_to_block_num.get(key)
        if bnum is None:
            return False
        block = self.paper_edit.document().findBlockByNumber(bnum)
        if not block.isValid():
            return False
        meta = block.userData()
        prefix_len = len(meta.prefix) if isinstance(meta, _BlockMeta) else 0
        body_len = max(0, len(block.text()) - prefix_len - (len(meta.suffix) if isinstance(meta, _BlockMeta) else 0))
        s = max(0, min(body_len, start))
        e = max(s, min(body_len, end))
        cursor = QTextCursor(self.paper_edit.document())
        cursor.setPosition(block.position() + prefix_len + s)
        cursor.setPosition(block.position() + prefix_len + e, QTextCursor.MoveMode.KeepAnchor)
        self.paper_edit.setTextCursor(cursor)
        self.paper_edit.setFocus()
        self.scroll_to_key(key, highlight=False)
        if show_mini_bar and e > s:
            self._maybe_show_mini_bar()
        return True

    def select_blocks_span(
        self,
        start_key: str,
        end_key: str,
        *,
        show_mini_bar: bool = False,
    ) -> bool:
        """跨多个内容对象建立连续选区，验证多段批量格式设定。"""

        s_num = self._key_to_block_num.get(start_key)
        e_num = self._key_to_block_num.get(end_key)
        if s_num is None or e_num is None:
            return False
        if s_num > e_num:
            s_num, e_num = e_num, s_num
        s_block = self.paper_edit.document().findBlockByNumber(s_num)
        e_block = self.paper_edit.document().findBlockByNumber(e_num)
        if not (s_block.isValid() and e_block.isValid()):
            return False
        cursor = QTextCursor(self.paper_edit.document())
        cursor.setPosition(s_block.position())
        cursor.setPosition(e_block.position() + len(e_block.text()), QTextCursor.MoveMode.KeepAnchor)
        self.paper_edit.setTextCursor(cursor)
        self.paper_edit.setFocus()
        if show_mini_bar:
            self._maybe_show_mini_bar()
        return True

    def _emit_inline(self, patch: dict[str, Any]) -> None:
        if not self._syncing_bar:
            self.inlineStyleRequested.emit(patch)

    def _emit_inline_and_refocus(self, patch: dict[str, Any]) -> None:
        if not self._syncing_bar:
            self.inlineStyleRequested.emit(patch)
            self.paper_edit.setFocus()

    def _on_bar_font(self, _idx: int) -> None:
        self._emit_inline_and_refocus({"font": self.font_combo.currentText()})

    def _on_bar_size(self, *_args) -> None:
        if self._syncing_bar:
            return
        try:
            val = float(self.size_combo.currentText().strip())
        except ValueError:
            return
        if 5 <= val <= 72:
            self.inlineStyleRequested.emit({"size_pt": val})
            self.paper_edit.setFocus()

    def _on_bar_alignment(self, idx: int) -> None:
        if not self._syncing_bar and 0 <= idx < len(ALIGNMENT_CHOICES):
            self.paragraphStyleRequested.emit({"alignment": ALIGNMENT_CHOICES[idx]})

    def _on_bar_line_spacing(self, *_args) -> None:
        if self._syncing_bar:
            return
        try:
            val = float(self.line_spacing_combo.currentText().strip())
        except ValueError:
            return
        if 0.5 <= val <= 4.0:
            self.paragraphStyleRequested.emit({"line_spacing": val})

    def set_locked(self, locked: bool) -> None:
        self.setEnabled(not locked)
        if locked:
            self.mini_bar.hide()


__all__ = ["FloatingFormatToolbar", "RichDocumentEditor", "SelectedSegment"]
