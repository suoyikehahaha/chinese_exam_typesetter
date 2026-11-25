"""基于 QPainter 实时渲染的 Fluent 风格高 DPI 线性矢量图标库。"""

from __future__ import annotations

from functools import lru_cache
from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


@lru_cache(maxsize=256)
def fluent_pixmap(name: str, color: str = "#1A222E", size: int = 16, stroke: float = 1.65) -> QPixmap:
    """按 2x 像素比绘制抗锯齿矢量图标，确保在高分屏下边缘锐利。"""

    scale = 2
    pixel_size = size * scale
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(scale)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pen = QPen(QColor(color))
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # 统一在 24x24 虚拟逻辑画布中绘制，再等比缩放到目标逻辑尺寸
    painter.scale(size / 24.0, size / 24.0)
    _draw_glyph(painter, name, QColor(color))
    painter.end()
    return pixmap


def fluent_icon(
    name: str,
    color: str = "#1A222E",
    size: int = 16,
    *,
    disabled_color: str = "#A0AEC0",
    stroke: float = 1.65,
) -> QIcon:
    """返回包含正常态与禁用态的高清 QIcon。"""

    icon = QIcon()
    icon.addPixmap(fluent_pixmap(name, color, size, stroke), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(fluent_pixmap(name, disabled_color, size, stroke), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def _draw_glyph(p: QPainter, name: str, color: QColor) -> None:
    if name == "import_doc":
        # 文档边框 + 向内箭头
        p.drawRoundedRect(QRectF(5, 3, 14, 18), 2.5, 2.5)
        p.drawLine(QPointF(12, 8), QPointF(12, 16))
        p.drawLine(QPointF(8.8, 12.8), QPointF(12, 16))
        p.drawLine(QPointF(15.2, 12.8), QPointF(12, 16))
    elif name == "export_doc":
        # 文档边框 + 向外箭头
        path = QPainterPath()
        path.moveTo(14, 3.5)
        path.lineTo(7, 3.5)
        path.quadTo(5, 3.5, 5, 5.5)
        path.lineTo(5, 18.5)
        path.quadTo(5, 20.5, 7, 20.5)
        path.lineTo(17, 20.5)
        path.quadTo(19, 20.5, 19, 18.5)
        path.lineTo(19, 11)
        p.drawPath(path)
        p.drawLine(QPointF(12.5, 11.5), QPointF(19.5, 4.5))
        p.drawLine(QPointF(15, 4.5), QPointF(19.5, 4.5))
        p.drawLine(QPointF(19.5, 4.5), QPointF(19.5, 9))
    elif name == "save":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2.5, 2.5)
        p.drawRect(QRectF(8, 4, 8, 5))
        p.drawRoundedRect(QRectF(7.5, 13, 9, 7), 1.2, 1.2)
    elif name == "undo":
        path = QPainterPath()
        path.moveTo(9, 8.5)
        path.lineTo(4.5, 8.5)
        path.lineTo(4.5, 4)
        p.drawPath(path)
        arc = QPainterPath()
        arc.moveTo(5, 8.5)
        arc.cubicTo(8.5, 5, 15.5, 5, 18.5, 9.5)
        arc.cubicTo(20.5, 12.5, 19, 17.5, 14.5, 18.5)
        arc.lineTo(9.5, 18.5)
        p.drawPath(arc)
    elif name == "redo":
        path = QPainterPath()
        path.moveTo(15, 8.5)
        path.lineTo(19.5, 8.5)
        path.lineTo(19.5, 4)
        p.drawPath(path)
        arc = QPainterPath()
        arc.moveTo(19, 8.5)
        arc.cubicTo(15.5, 5, 8.5, 5, 5.5, 9.5)
        arc.cubicTo(3.5, 12.5, 5, 17.5, 9.5, 18.5)
        arc.lineTo(14.5, 18.5)
        p.drawPath(arc)
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3.3, 3.3)
        for index in range(8):
            angle = index * pi / 4
            p.drawLine(
                QPointF(12 + cos(angle) * 6.4, 12 + sin(angle) * 6.4),
                QPointF(12 + cos(angle) * 8.8, 12 + sin(angle) * 8.8),
            )
        p.drawEllipse(QPointF(12, 12), 6.2, 6.2)
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6.0, 6.0)
        p.drawLine(QPointF(15.0, 15.0), QPointF(19.8, 19.8))
    elif name == "clear" or name == "close":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
    elif name == "chevron_down":
        p.drawLine(QPointF(6.5, 9.5), QPointF(12, 15))
        p.drawLine(QPointF(12, 15), QPointF(17.5, 9.5))
    elif name == "chevron_up":
        p.drawLine(QPointF(6.5, 14.5), QPointF(12, 9))
        p.drawLine(QPointF(12, 9), QPointF(17.5, 14.5))
    elif name == "chevron_right":
        p.drawLine(QPointF(9.5, 6.5), QPointF(15, 12))
        p.drawLine(QPointF(15, 12), QPointF(9.5, 17.5))
    elif name == "chevron_left":
        p.drawLine(QPointF(14.5, 6.5), QPointF(9, 12))
        p.drawLine(QPointF(9, 12), QPointF(14.5, 17.5))
    elif name == "zoom_in":
        p.drawEllipse(QPointF(10.5, 10.5), 6.0, 6.0)
        p.drawLine(QPointF(15.0, 15.0), QPointF(19.8, 19.8))
        p.drawLine(QPointF(10.5, 7.6), QPointF(10.5, 13.4))
        p.drawLine(QPointF(7.6, 10.5), QPointF(13.4, 10.5))
    elif name == "zoom_out":
        p.drawEllipse(QPointF(10.5, 10.5), 6.0, 6.0)
        p.drawLine(QPointF(15.0, 15.0), QPointF(19.8, 19.8))
        p.drawLine(QPointF(7.6, 10.5), QPointF(13.4, 10.5))
    elif name == "fit_width":
        p.drawLine(QPointF(4, 5), QPointF(4, 19))
        p.drawLine(QPointF(20, 5), QPointF(20, 19))
        p.drawLine(QPointF(7.5, 12), QPointF(16.5, 12))
        p.drawLine(QPointF(10, 9.5), QPointF(7.5, 12))
        p.drawLine(QPointF(10, 14.5), QPointF(7.5, 12))
        p.drawLine(QPointF(14, 9.5), QPointF(16.5, 12))
        p.drawLine(QPointF(14, 14.5), QPointF(16.5, 12))
    elif name == "printer":
        p.drawRoundedRect(QRectF(7, 4, 10, 5), 1, 1)
        p.drawRoundedRect(QRectF(4, 9, 16, 8), 2, 2)
        p.drawRoundedRect(QRectF(7.5, 14, 9, 6), 1, 1)
        p.drawPoint(QPointF(16.8, 11.8))
    elif name == "refresh":
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(5, 5, 14, 14), 35)
        arc.arcTo(QRectF(5, 5, 14, 14), 35, 290)
        p.drawPath(arc)
        p.drawLine(QPointF(17.2, 5.2), QPointF(18.5, 9.2))
        p.drawLine(QPointF(18.5, 9.2), QPointF(14.5, 8.8))
    elif name == "warning":
        path = QPainterPath()
        path.moveTo(12, 4)
        path.lineTo(20.2, 19)
        path.lineTo(3.8, 19)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 9.8), QPointF(12, 14))
        p.drawPoint(QPointF(12, 16.5))
    elif name == "check":
        p.drawLine(QPointF(5.5, 12.5), QPointF(10.2, 17.2))
        p.drawLine(QPointF(10.2, 17.2), QPointF(18.8, 7.2))
    elif name == "panel_right":
        p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
        p.drawLine(QPointF(14.5, 4.5), QPointF(14.5, 19.5))
    elif name == "panel_left":
        p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
        p.drawLine(QPointF(9.5, 4.5), QPointF(9.5, 19.5))
    elif name == "align_left":
        p.drawLine(QPointF(4, 6), QPointF(20, 6))
        p.drawLine(QPointF(4, 10.5), QPointF(15, 10.5))
        p.drawLine(QPointF(4, 15), QPointF(19, 15))
        p.drawLine(QPointF(4, 19.5), QPointF(13, 19.5))
    elif name == "align_center":
        p.drawLine(QPointF(4, 6), QPointF(20, 6))
        p.drawLine(QPointF(6.5, 10.5), QPointF(17.5, 10.5))
        p.drawLine(QPointF(4.5, 15), QPointF(19.5, 15))
        p.drawLine(QPointF(7.5, 19.5), QPointF(16.5, 19.5))
    elif name == "align_right":
        p.drawLine(QPointF(4, 6), QPointF(20, 6))
        p.drawLine(QPointF(9, 10.5), QPointF(20, 10.5))
        p.drawLine(QPointF(5, 15), QPointF(20, 15))
        p.drawLine(QPointF(11, 19.5), QPointF(20, 19.5))
    elif name == "align_justify":
        for y in (6, 10.5, 15, 19.5):
            p.drawLine(QPointF(4, y), QPointF(20, y))
    elif name == "bold":
        pen = p.pen()
        pen.setWidthF(2.1)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(7, 5)
        path.lineTo(13.2, 5)
        path.cubicTo(16.8, 5, 16.8, 11.5, 13.2, 11.5)
        path.lineTo(7, 11.5)
        path.lineTo(7, 5)
        path.moveTo(7, 11.5)
        path.lineTo(14.2, 11.5)
        path.cubicTo(18.2, 11.5, 18.2, 19, 14.2, 19)
        path.lineTo(7, 19)
        path.lineTo(7, 11.5)
        p.drawPath(path)
    elif name == "reset":
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(5, 5, 14, 14), 145)
        arc.arcTo(QRectF(5, 5, 14, 14), 145, -280)
        p.drawPath(arc)
        p.drawLine(QPointF(5.5, 5.5), QPointF(5.5, 10))
        p.drawLine(QPointF(5.5, 10), QPointF(10, 9.6))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3.5, 7)
        path.quadTo(3.5, 5, 5.5, 5)
        path.lineTo(9.5, 5)
        path.lineTo(11.5, 7.5)
        path.lineTo(18.5, 7.5)
        path.quadTo(20.5, 7.5, 20.5, 9.5)
        path.lineTo(20.5, 17.5)
        path.quadTo(20.5, 19.5, 18.5, 19.5)
        path.lineTo(5.5, 19.5)
        path.quadTo(3.5, 19.5, 3.5, 17.5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "open_file":
        p.drawRoundedRect(QRectF(5, 3.5, 14, 17), 2.2, 2.2)
        p.drawLine(QPointF(8.5, 9), QPointF(15.5, 9))
        p.drawLine(QPointF(8.5, 13), QPointF(15.5, 13))
        p.drawLine(QPointF(8.5, 16.5), QPointF(12.8, 16.5))
    elif name == "expand_all":
        p.drawLine(QPointF(7, 8.5), QPointF(12, 4.5))
        p.drawLine(QPointF(12, 4.5), QPointF(17, 8.5))
        p.drawLine(QPointF(7, 15.5), QPointF(12, 19.5))
        p.drawLine(QPointF(12, 19.5), QPointF(17, 15.5))
    elif name == "collapse_all":
        p.drawLine(QPointF(7, 5.5), QPointF(12, 9.5))
        p.drawLine(QPointF(12, 9.5), QPointF(17, 5.5))
        p.drawLine(QPointF(7, 18.5), QPointF(12, 14.5))
        p.drawLine(QPointF(12, 14.5), QPointF(17, 18.5))
    elif name == "apply_all":
        p.drawRoundedRect(QRectF(4, 4, 11, 11), 2, 2)
        p.drawRoundedRect(QRectF(9, 9, 11, 11), 2, 2)
    elif name == "underline":
        path = QPainterPath()
        path.moveTo(7, 4.5)
        path.lineTo(7, 12.2)
        path.cubicTo(7, 16.5, 17, 16.5, 17, 12.2)
        path.lineTo(17, 4.5)
        p.drawPath(path)
        p.drawLine(QPointF(5.5, 19.5), QPointF(18.5, 19.5))
    elif name == "emphasis_dot":
        # 汉字“文”字轮廓 + 下方实心着重号圆点
        p.drawLine(QPointF(12, 4), QPointF(12, 6.2))
        p.drawLine(QPointF(6, 7.2), QPointF(18, 7.2))
        p.drawLine(QPointF(9, 7.2), QPointF(15.5, 15))
        p.drawLine(QPointF(15, 7.2), QPointF(8.5, 15))
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 19.2), 1.7, 1.7)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "clear_format":
        # 橡皮擦图标
        path = QPainterPath()
        path.moveTo(6.5, 14.5)
        path.lineTo(14, 7)
        path.quadTo(15.2, 5.8, 16.4, 7)
        path.lineTo(19.2, 9.8)
        path.quadTo(20.4, 11, 19.2, 12.2)
        path.lineTo(13, 18.4)
        path.lineTo(8.5, 18.4)
        path.lineTo(6.5, 16.4)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(11.5, 18.4), QPointF(19.5, 18.4))
    elif name == "edit_doc":
        p.drawRoundedRect(QRectF(4.5, 3.5, 13, 17), 2.2, 2.2)
        p.drawLine(QPointF(7.5, 8.5), QPointF(14.5, 8.5))
        p.drawLine(QPointF(7.5, 12), QPointF(12.2, 12))
        p.drawLine(QPointF(13.5, 17.5), QPointF(19.8, 11.2))
    elif name == "pages_view":
        p.drawRoundedRect(QRectF(4.5, 3.5, 11.5, 14), 2, 2)
        p.drawRoundedRect(QRectF(8, 6.5, 11.5, 14), 2, 2)
    else:
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 12), 3, 3)


__all__ = ["fluent_icon", "fluent_pixmap"]
