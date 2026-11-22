"""Draw an editable Word table's grid and paragraph layout for the internal preview."""
from itertools import accumulate

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from PIL import Image, ImageDraw

from .font_resolver import font_substitution_notice, load_preview_font
from .preview_cancellation import check_cancelled


def grid_geometry(table, width):
    weights = [float(c.width or 1) for c in table.columns] or [1.0]
    total = sum(weights)
    edges = [0, *(round(width * v / total) for v in accumulate(weights))]
    spans = {}
    for row_no, row in enumerate(table.rows):
        for col_no, cell in enumerate(row.cells, start=getattr(row, "grid_cols_before", 0)):
            if cell._tc not in spans:
                spans[cell._tc] = [cell, row_no, row_no, col_no, col_no]
            else:
                spans[cell._tc][2] = row_no
                spans[cell._tc][4] = max(col_no, spans[cell._tc][4])
    return edges, list(spans.values())


def _pixels(length):
    return round(length.pt * 4 / 3) if length is not None else 0


def _font(paragraph, run, warnings):
    fonts = [run.font, run.style.font, paragraph.style.font] if run is not None else [paragraph.style.font]
    faces = []
    for font in fonts:
        nodes = font._element.xpath("./w:rPr/w:rFonts")
        faces.append(nodes[0].get(qn("w:eastAsia")) if nodes else None)
        faces.append(font.name)
    family = next((name for name in faces if name), "SimSun")
    size = next((f.size.pt for f in fonts if f.size is not None), 10.5)
    bold = next((f.bold for f in fonts if f.bold is not None), False)
    font = load_preview_font(family, size, bold)
    if warnings is not None and (notice := font_substitution_notice(family, font)):
        warnings.add(f"表格预览字体替代：{notice}")
    return font


def _cell_layout(cell, width, warnings):
    y, lines = 4, []
    for p in cell.paragraphs:
        check_cancelled()
        fmt, style = p.paragraph_format, p.style.paragraph_format
        def setting(name):
            direct = getattr(fmt, name)
            return direct if direct is not None else getattr(style, name)
        y += _pixels(setting("space_before"))
        left = max(3, 5 + _pixels(setting("left_indent")))
        right = max(3, 5 + _pixels(setting("right_indent")))
        available = max(1, width - left - right)
        align = setting("alignment")
        spacing = setting("line_spacing")
        default = _font(p, None, warnings)
        pen, row, height = 0.0, [], default.size
        def flush():
            nonlocal pen, row, height, y
            shift = (available - pen) / 2 if align == WD_ALIGN_PARAGRAPH.CENTER else available - pen if align == WD_ALIGN_PARAGRAPH.RIGHT else 0
            lines.append((left + max(0, shift), y, row))
            if hasattr(spacing, "pt"):
                y += max(1, _pixels(spacing))
            else:
                y += max(1, round(height * 1.22 * (spacing or 1)))
            pen, row, height = 0.0, [], default.size
        for run in p.runs:
            font = _font(p, run, warnings)
            for ch in run.text:
                if ch == "\n":
                    flush()
                    continue
                step = font.getlength(ch)
                if row and pen + step > available:
                    flush()
                row.append((pen, ch, font))
                height = max(height, font.size)
                pen += step
        flush()
        y += _pixels(setting("space_after"))
    return max(22, y + 4), lines


def render_table_image(table, *, max_width=620, warnings=None):
    width = max(48, int(max_width))
    edges, cells = grid_geometry(table, width)
    heights = [max(20, _pixels(row.height)) for row in table.rows]
    layouts = []
    if warnings is not None and table._tbl.xpath(".//w:tc/w:tbl | .//w:drawing | .//w:pict | .//m:oMath"):
        warnings.add("表格含嵌套表格、公式或图片，细节请在导出的 Word 中核对。")
    for cell, r0, r1, c0, c1 in sorted(cells, key=lambda x: x[2]-x[1]):
        height, lines = _cell_layout(cell, edges[c1+1]-edges[c0], warnings)
        heights[r1] += max(0, height - sum(heights[r0:r1+1]))
        layouts.append((r0, r1, c0, c1, lines))
    ys = [0, *accumulate(heights)]
    image = Image.new("RGBA", (width + 1, max(24, ys[-1] + 1)), "white")
    draw = ImageDraw.Draw(image)
    for r0, r1, c0, c1, lines in layouts:
        x, y = edges[c0], ys[r0]
        draw.rectangle((x, y, edges[c1+1], ys[r1+1]), outline="#4F4F4F")
        for offset, top, chunks in lines:
            for dx, ch, font in chunks:
                draw.text((x + offset + dx, y + top), ch, fill="#111111", font=font,
                          stroke_width=getattr(font, "preview_stroke_width", 0))
    return image
