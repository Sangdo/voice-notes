"""Small line-icon set (24×24 SVG) rendered to crisp QIcons at any device pixel ratio."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
_FILL = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">{body}</svg>'

ICONS = {
    'speaker': (_STROKE, '<path d="M3 9h4l5-4v14l-5-4H3z M16 8q4 4 0 8 M19 5q7 7 0 14"/>'),
    'muted': (_STROKE, '<path d="M3 9h4l5-4v14l-5-4H3z M16 9l6 6 M22 9l-6 6"/>'),
    'play': (_FILL, '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/>'),
    'pause': (_FILL, '<rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/>'),
    'back10': (_STROKE, '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/>'
               '<text x="12" y="15.2" font-size="7.5" font-family="Helvetica" font-weight="700" '
               'text-anchor="middle" fill="{color}" stroke="none">10</text>'),
    'forward10': (_STROKE, '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v4h-4"/>'
                  '<text x="12" y="15.2" font-size="7.5" font-family="Helvetica" font-weight="700" '
                  'text-anchor="middle" fill="{color}" stroke="none">10</text>'),
    'expand': (_STROKE, '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
    'collapse': (_STROKE, '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>'),
    'list': (_STROKE, '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M7 10h6M7 14h10"/>'),
    'gear': (_STROKE, '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
             'a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3'
             'l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0'
             ' 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3'
             'a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0'
             '-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    'folder': (_STROKE, '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
    'sparkle': (_FILL, '<path d="M12 2.5l1.9 5.1a2 2 0 0 0 1.2 1.2l5.1 1.9-5.1 1.9a2 2 0 0 0-1.2 1.2L12 18.9l-1.9-5.1'
                'a2 2 0 0 0-1.2-1.2L3.8 10.7l5.1-1.9a2 2 0 0 0 1.2-1.2z"/><path d="M19 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
    'film': (_STROKE, '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M7.5 4.5v15M16.5 4.5v15M3 9h4.5M3 15h4.5'
             'M16.5 9H21M16.5 15H21"/>'),
    'search': (_STROKE, '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>'),
    'star': (_STROKE, '<path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9z"/>'),
    'starFilled': (_FILL, '<path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9z"/>'),
    'lock': (_STROKE, '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>'),
    'export': (_STROKE, '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5M5 14v4.5a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5V14"/>'),
    'wave': (_STROKE, '<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0"/>'),
}


@lru_cache(maxsize=None)
def svg(name, color='#ffffff', width=1.8):
    template, body = ICONS[name]
    return template.format(color=color, width=width, body=body.replace('{color}', color))


@lru_cache(maxsize=None)
def icon(name, color='#ffffff', size=24, width=1.8):
    """QIcon with 1× and 2× pixmaps so it stays sharp on Retina screens."""
    renderer = QSvgRenderer(QByteArray(svg(name, color, width).encode()))
    result = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result
