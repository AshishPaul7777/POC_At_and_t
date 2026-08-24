"""Render the generated deck to HTML for visual QA.

LibreOffice is not available on this machine, so instead of rasterising the
pptx we read its real geometry back out with python-pptx and lay the same
boxes out in a browser. Fonts are the ones the deck asks for, so text wrap and
overflow behave close enough to PowerPoint to catch the defect that actually
matters: text spilling out of its shape.
"""
from __future__ import annotations

import html
import pathlib

from pptx import Presentation
from pptx.util import Emu

DECK = pathlib.Path(__file__).with_name("org-cleanup-product.pptx")
OUT = pathlib.Path(__file__).with_name("qa.html")
PX = 96  # css px per inch


def inches(v) -> float:
    return Emu(v).inches if v is not None else 0.0


def solid(fmt):
    try:
        if fmt.type is not None and fmt.fore_color.type is not None:
            return "#" + str(fmt.fore_color.rgb)
    except Exception:
        pass
    return None


def shape_html(sh) -> str:
    x, y = inches(sh.left) * PX, inches(sh.top) * PX
    w, h = inches(sh.width) * PX, inches(sh.height) * PX
    css = [f"left:{x:.1f}px", f"top:{y:.1f}px",
           f"width:{w:.1f}px", f"height:{h:.1f}px"]

    fill = line = None
    try:
        fill = solid(sh.fill)
    except Exception:
        pass
    try:
        line = solid(sh.line.color) if sh.line.color.type is not None else None
    except Exception:
        pass

    # python-pptx reports shape_type as None for pptxgenjs autoshapes, so read
    # the preset geometry straight off the XML instead.
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    prst = ""
    alpha = 1.0
    el = sh._element
    for g in el.iter(f"{ns}prstGeom"):
        prst = g.get("prst") or ""
        break
    for a in el.iter(f"{ns}alpha"):
        alpha = int(a.get("val", "100000")) / 100000.0
        break

    # Connectors carry no fill, so without this they render as empty divs and
    # the whole point of a wiring diagram is invisible in QA.
    if prst == "line":
        lnel = next(el.iter(f"{ns}ln"), None)
        col = "#888"
        if lnel is not None:
            c = next((sf for sf in lnel.iter(f"{ns}srgbClr")), None)
            if c is not None:
                col = "#" + c.get("val")
        heads = {t.tag.split("}")[1]: t.get("type")
                 for t in (lnel if lnel is not None else [])
                 if t.tag in (f"{ns}headEnd", f"{ns}tailEnd")}
        vert = h > w
        parts = [f"<div style=\"position:absolute;left:{x:.1f}px;top:{y:.1f}px;"]
        if vert:
            parts.append(f"width:2px;height:{h:.1f}px;background:{col}\"></div>")
        else:
            parts.append(f"width:{w:.1f}px;height:2px;background:{col}\"></div>")
        tri = ("border-left:5px solid transparent;border-right:5px solid "
               "transparent;border-top:8px solid " + col)
        trh = ("border-top:5px solid transparent;border-bottom:5px solid "
               "transparent;border-left:8px solid " + col)
        if heads.get("tailEnd") == "triangle":
            parts.append(
                f"<div style=\"position:absolute;left:{(x - 4) if vert else (x + w - 8):.1f}px;"
                f"top:{(y + h - 8) if vert else (y - 4):.1f}px;"
                f"{tri if vert else trh}\"></div>")
        if heads.get("headEnd") == "triangle":
            parts.append(
                f"<div style=\"position:absolute;left:{(x - 4) if vert else x:.1f}px;"
                f"top:{y:.1f}px;transform:rotate(180deg);"
                f"{tri if vert else trh}\"></div>")
        return "".join(parts)

    if fill:
        if alpha < 1.0:
            r, g_, b = (int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16))
            css.append(f"background:rgba({r},{g_},{b},{alpha:.2f})")
        else:
            css.append(f"background:{fill}")
    if line:
        css.append(f"border:1px solid {line}")
    if prst == "ellipse":
        css.append("border-radius:50%")
    elif prst == "roundRect":
        css.append("border-radius:6px")
    elif prst == "triangle":
        css.append("clip-path:polygon(50% 0,100% 100%,0 100%);transform:rotate(90deg)")

    inner = ""
    if sh.has_text_frame and sh.text_frame.text.strip():
        paras = []
        for p in sh.text_frame.paragraphs:
            runs = []
            for r in p.runs:
                f = r.font
                rs = []
                if f.size:
                    rs.append(f"font-size:{f.size.pt}pt")
                if f.bold:
                    rs.append("font-weight:700")
                if f.italic:
                    rs.append("font-style:italic")
                if f.name:
                    rs.append(f"font-family:'{f.name}',sans-serif")
                try:
                    if f.color and f.color.type is not None:
                        rs.append(f"color:#{f.color.rgb}")
                except Exception:
                    pass
                runs.append(f"<span style=\"{';'.join(rs)}\">"
                            f"{html.escape(r.text)}</span>")
            align = str(p.alignment or "")
            a = "center" if "CENTER" in align else (
                "right" if "RIGHT" in align else "left")
            paras.append(f"<p style=\"text-align:{a}\">{''.join(runs) or '&nbsp;'}</p>")
        # The overflow marker: content taller than its box shows a red outline.
        inner = f"<div class='tf'>{''.join(paras)}</div>"
        css.append("overflow:visible")

    return f"<div class='sh' style=\"{';'.join(css)}\">{inner}</div>"


def main() -> None:
    prs = Presentation(DECK)
    sw = inches(prs.slide_width) * PX
    sh_ = inches(prs.slide_height) * PX

    parts = ["""<!doctype html><meta charset=utf-8><style>
body{background:#8a8a8a;margin:0;padding:18px;font-family:Calibri,sans-serif}
.slide{position:relative;background:#fff;margin:0 auto 18px;overflow:hidden;
  box-shadow:0 3px 14px rgba(0,0,0,.45)}
.num{position:absolute;left:-2px;top:-16px;color:#fff;font:700 13px Arial}
.wrap{position:relative;margin:0 auto 26px;width:%dpx}
.sh{position:absolute;box-sizing:border-box}
.tf{position:absolute;inset:0;display:flex;flex-direction:column;
  justify-content:center}
.tf p{margin:0;line-height:1.22}
</style>""" % int(sw)]

    for i, s in enumerate(prs.slides, 1):
        bg = "#FFFFFF"
        try:
            c = s.background.fill
            if c.type is not None and c.fore_color.type is not None:
                bg = "#" + str(c.fore_color.rgb)
        except Exception:
            pass
        parts.append(f"<div class='wrap'><div class='num'>slide {i}</div>"
                     f"<div class='slide' style='width:{sw:.0f}px;"
                     f"height:{sh_:.0f}px;background:{bg}'>")
        for shape in s.shapes:
            if shape.has_chart:
                x, y = inches(shape.left) * PX, inches(shape.top) * PX
                w, h = inches(shape.width) * PX, inches(shape.height) * PX
                parts.append(
                    f"<div class='sh' style=\"left:{x:.0f}px;top:{y:.0f}px;"
                    f"width:{w:.0f}px;height:{h:.0f}px;border:1px dashed #9aa;"
                    f"color:#556;font-size:12px;padding:6px\">[native chart]</div>")
                continue
            parts.append(shape_html(shape))
        parts.append("</div></div>")

    OUT.write_text("".join(parts), encoding="utf-8")
    print("wrote", OUT, "-", len(prs.slides), "slides")


if __name__ == "__main__":
    main()
