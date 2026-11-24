"""
Storefront colour themes. Staff pick one in Store settings (e.g. for a festival) and may override
individual colours. Header and strip colours carry white text, so they must stay dark enough.
"""

THEMES = {
    # key: (label, header bar, category strip, accent: search button / highlights)
    "default": ("Maroon & gold (standard)", "#3a0a13", "#5c1220", "#e8a93b"),
    "diwali": ("Diwali — royal purple & gold", "#2b0b3f", "#4b1a6b", "#f5b82e"),
    "ganesh": ("Ganesh Chaturthi — saffron & red", "#6b1a00", "#9a2e00", "#ffb000"),
    "navratri": ("Navratri — magenta & marigold", "#4f0a35", "#7d1352", "#ffc845"),
    "holi": ("Holi — pink & yellow", "#4a0e4e", "#8e1a5c", "#ffd600"),
    "tiranga": ("Independence / Republic Day — navy & saffron", "#0b2545", "#13315c", "#ff9933"),
    "festive_green": ("Christmas / New Year — green & gold", "#0f3d2e", "#155239", "#e3b23c"),
}
THEME_CHOICES = [(k, v[0]) for k, v in THEMES.items()]


def _luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_with_white(hex_color):
    return 1.05 / (_luminance(hex_color) + 0.05)


def theme_colors(store):
    """Final colours for the storefront: the preset, with any custom colours on top."""
    _, header, strip, accent = THEMES.get(store.theme, THEMES["default"])
    return {
        "header": store.theme_header_color or header,
        "strip": store.theme_strip_color or strip,
        "accent": store.theme_accent_color or accent,
    }
