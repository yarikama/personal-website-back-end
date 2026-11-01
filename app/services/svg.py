"""
SVG uploads: drawings, such as a project's cover, that stay sharp at any
size and may animate with CSS.

An SVG is a document that can run script, so an upload is checked against
an allowlist of drawing elements and attributes and refused, whole, if
anything else is in it: no script, no event handlers, no links or images
that load from elsewhere, no embedded HTML, no SMIL (which can rewrite a
link). CSS is kept for its animations, but may not load anything. What is
stored is the parsed tree written out again, so comments, processing
instructions and anything the parser skipped are gone, and what the site
serves is exactly what was checked. Storage also serves it as an
attachment (services/storage.py): opened by its URL it downloads, and only
an <img> shows it, where no script would run anyway.
"""

import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "title", "desc", "style",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "linearGradient", "radialGradient", "stop",
        "clipPath", "mask", "pattern", "marker",
    }
)  # fmt: skip

ATTRIBUTES = frozenset(
    {
        # structure and geometry
        "id", "class", "style", "viewBox", "preserveAspectRatio", "version",
        "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy",
        "r", "rx", "ry", "fx", "fy", "d", "points", "pathLength",
        "transform", "dx", "dy", "rotate", "textLength", "lengthAdjust",
        "role", "aria-label", "aria-hidden", "lang", "focusable",
        # paint
        "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
        "stroke-opacity", "stroke-linecap", "stroke-linejoin",
        "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit",
        "opacity", "color", "display", "visibility", "vector-effect",
        "paint-order", "shape-rendering", "clip-path", "clip-rule", "mask",
        "marker-start", "marker-mid", "marker-end",
        # text
        "font-family", "font-size", "font-weight", "font-style",
        "text-anchor", "dominant-baseline", "alignment-baseline",
        "letter-spacing", "word-spacing", "text-decoration",
        # gradients, patterns, markers
        "offset", "stop-color", "stop-opacity", "gradientUnits",
        "gradientTransform", "spreadMethod", "patternUnits",
        "patternContentUnits", "patternTransform", "clipPathUnits",
        "maskUnits", "maskContentUnits", "markerWidth", "markerHeight",
        "markerUnits", "refX", "refY", "orient",
        # <use href="#id"> only: checked below
        "href",
    }
)  # fmt: skip

# Anything that could load, run, or hide from this check: an external
# url(), @import, legacy script hooks, and CSS escapes that could spell
# one of those in disguise.
UNSAFE_CSS = re.compile(
    r"@import|url\s*\(\s*['\"]?\s*(?!#)|expression\s*\(|javascript:|"
    r"-moz-binding|behavior\s*:|\\|</",
    re.IGNORECASE,
)
# A paint or reference attribute may point within the drawing only
UNSAFE_VALUE = re.compile(r"javascript:|data:|url\s*\(\s*['\"]?\s*(?!#)", re.I)

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class UnsafeSvgError(ValueError):
    """Not an SVG, or an SVG with something outside the allowlist. The
    message says what, for the admin."""


def looks_like_svg(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:4096].lower()


def _name(tag: str) -> tuple[str, str]:
    namespace, _, local = tag.rpartition("}")
    return namespace.lstrip("{"), local


def clean_svg(data: bytes) -> bytes:
    """The SVG written out again, or UnsafeSvgError."""
    # Entities are how XML bombs and external file reads start; a drawing
    # never needs them.
    if re.search(rb"<!(DOCTYPE|ENTITY)", data, re.IGNORECASE):
        raise UnsafeSvgError("a DOCTYPE or ENTITY declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise UnsafeSvgError(f"not well-formed XML ({error})") from error

    if root.tag != f"{{{SVG_NS}}}svg":
        raise UnsafeSvgError("the root is not an <svg> in the SVG namespace")

    for element in root.iter():
        namespace, tag = _name(element.tag)
        if namespace != SVG_NS or tag not in ELEMENTS:
            raise UnsafeSvgError(f"the element <{tag}>")
        if tag == "style" and UNSAFE_CSS.search(element.text or ""):
            raise UnsafeSvgError("CSS that loads or escapes something")
        for key, value in element.attrib.items():
            namespace, attribute = _name(key)
            if namespace not in ("", XLINK_NS, XML_NS) or (
                namespace == XLINK_NS and attribute != "href"
            ):
                raise UnsafeSvgError(f"the attribute {attribute}")
            if namespace == XML_NS:
                continue  # xml:space, xml:lang
            if attribute not in ATTRIBUTES:
                raise UnsafeSvgError(f"the attribute {attribute}")
            if attribute == "href" and not value.startswith("#"):
                raise UnsafeSvgError("a link out of the drawing")
            if attribute == "style" and UNSAFE_CSS.search(value):
                raise UnsafeSvgError("CSS that loads or escapes something")
            if UNSAFE_VALUE.search(value):
                raise UnsafeSvgError(f"the value of {attribute}")

    return ET.tostring(root, encoding="utf-8", xml_declaration=False)
