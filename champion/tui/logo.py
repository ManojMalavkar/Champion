"""The M logo in the header (T9.1; Thinking/Working pulse from T9.2).

Logo files: ``champion/tui/assets/m_logo.png`` (still) and ``m_logo.gif``
(animation for Thinking/Working, used from T9.2). With Pillow installed the PNG
is drawn with half blocks (two pixels per cell); its white or transparent
background becomes the terminal background, and the image is cropped to the
logo first. Without Pillow or the file, a text M in the logo's blue-to-cyan
gradient is shown. Error is always the text M in red.
"""

from __future__ import annotations

from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

ASSETS = Path(__file__).resolve().parent / "assets"
LOGO_PNG = ASSETS / "m_logo.png"
LOGO_GIF = ASSETS / "m_logo.gif"
LOGO_CELLS = (12, 5)  # width, height in cells (12 x 10 pixels); at 10 x 4 the M's notch and gap were lost
TEXT_M = ("█▙   ▟█", "██▙ ▟██", "█ ▜█▛ █", "█     █")
GRADIENT = ("#0b3dff", "#0a5cff", "#0a7dff", "#099eff", "#0bbcf7", "#0fd6f5", "#13ecf5")
ERROR_COLOR = "#ff4040"


def _hex(pixel) -> str:
    return "#{:02x}{:02x}{:02x}".format(*pixel[:3])


def _transparent(pixel) -> bool:
    r, g, b, a = pixel
    return a < 128 or (r > 235 and g > 235 and b > 235)


def image_lines(path: Path | None, width: int = LOGO_CELLS[0], height: int = LOGO_CELLS[1]) -> list[Text] | None:
    """The first frame of ``path`` as ``height`` lines of half blocks, or None if Pillow or the file is missing."""
    if path is None:
        return None
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            image.seek(0)
            frame = image.convert("RGBA")
    except (OSError, ValueError):
        return None
    ink = frame.convert("L").point(lambda v: 255 if v < 236 else 0)
    opaque = frame.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    box = ImageChops.multiply(ink, opaque).getbbox()
    if box:
        frame = frame.crop(box)
    scale = min(width / frame.width, height * 2 / frame.height)
    size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
    small = frame.resize(size, Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height * 2), (0, 0, 0, 0))
    canvas.paste(small, ((width - size[0]) // 2, (height * 2 - size[1]) // 2))
    lines = []
    for row in range(height):
        line = Text()
        for col in range(width):
            top, bottom = canvas.getpixel((col, 2 * row)), canvas.getpixel((col, 2 * row + 1))
            clear_top, clear_bottom = _transparent(top), _transparent(bottom)
            if clear_top and clear_bottom:
                line.append(" ")
            elif clear_top:
                line.append("▄", Style(color=_hex(bottom)))
            elif clear_bottom:
                line.append("▀", Style(color=_hex(top)))
            else:
                line.append("▀", Style(color=_hex(top), bgcolor=_hex(bottom)))
        lines.append(line)
    return lines


def text_lines(color: str | None = None) -> list[Text]:
    """The text M; one gradient colour per column, or ``color`` for every cell."""
    lines = []
    for row in TEXT_M:
        line = Text(" ")
        for col, char in enumerate(row):
            line.append(char, Style(color=color or GRADIENT[col % len(GRADIENT)], bold=True))
        lines.append(line)
    return lines


class LogoState(Widget):
    """The logo; ``state`` is one of ``state.RUNTIME_STATES``."""

    DEFAULT_CSS = "LogoState { width: 12; height: 5; }"
    state = reactive("idle")

    def __init__(self, image_path: Path | None = LOGO_PNG, **kwargs) -> None:
        super().__init__(**kwargs)
        self._image = image_lines(image_path)

    @property
    def uses_image(self) -> bool:
        return self._image is not None

    def lines(self) -> list[Text]:
        if self.state == "error":
            return text_lines(ERROR_COLOR)
        return self._image if self._image is not None else text_lines()

    def render(self) -> Text:
        return Text("\n").join(self.lines())
