"""
Ikonica aplikacije, nacrtana u kodu (nema spoljnih fajlova).

Zaobljen plavi kvadrat sa stilizovanom "glavom" Android robota.
Verzija za tray ima malu tačku statusa u uglu (zelena = Android radi).
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

from ui import theme

SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def _draw(size: int, dot: str | None) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size

    # pozadina
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)

    # glava robota (polukrug)
    p.setBrush(QColor("white"))
    head = QRectF(s * 0.22, s * 0.32, s * 0.56, s * 0.56)
    p.drawPie(head, 0, 180 * 16)

    # antene
    pen_w = max(1.0, s * 0.045)
    p.setPen(QColor("white"))
    pen = p.pen()
    pen.setWidthF(pen_w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(int(s * 0.36), int(s * 0.36), int(s * 0.30), int(s * 0.24))
    p.drawLine(int(s * 0.64), int(s * 0.36), int(s * 0.70), int(s * 0.24))

    # oči
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    r = s * 0.045
    p.drawEllipse(QRectF(s * 0.38 - r, s * 0.49 - r, 2 * r, 2 * r))
    p.drawEllipse(QRectF(s * 0.62 - r, s * 0.49 - r, 2 * r, 2 * r))

    # tačka statusa
    if dot:
        d = s * 0.34
        p.setBrush(QColor("#1b1d22"))
        p.drawEllipse(QRectF(s - d, s - d, d, d))
        p.setBrush(QColor(dot))
        inset = d * 0.18
        p.drawEllipse(QRectF(s - d + inset, s - d + inset, d - 2 * inset, d - 2 * inset))

    p.end()
    return pix


def app_icon(dot: str | None = None) -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(_draw(size, dot))
    return icon
