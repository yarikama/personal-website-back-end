from contextlib import asynccontextmanager

import pytest

from services import svg
from services.storage import R2StorageService, UploadRefusedError

NS = 'xmlns="http://www.w3.org/2000/svg"'

# A drawing like a project cover: shapes, a class, and CSS that animates
COVER = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- drawn by hand -->
<svg {NS} viewBox="0 0 240 150">
  <style>
    .draw {{ stroke-dasharray: 1; animation: draw 0.8s 0.2s both; }}
    @keyframes draw {{ from {{ stroke-dashoffset: 1; }} }}
    .soft {{ fill: url(#fade); }}
  </style>
  <defs>
    <linearGradient id="fade"><stop offset="0" stop-color="#6f9691"/></linearGradient>
  </defs>
  <g style="--d: 0.4s">
    <path class="draw" d="M0,0 L10,10" pathLength="1" fill="none" stroke="#6f9691"/>
    <rect class="soft" x="1" y="2" width="3" height="4" rx="1"/>
    <use href="#fade"/>
  </g>
</svg>""".encode()


def test_a_drawing_is_kept_and_written_out_again():
    cleaned = svg.clean_svg(COVER)

    assert cleaned.startswith(b"<svg ")
    assert b"@keyframes draw" in cleaned and b'pathLength="1"' in cleaned
    # Comments and the XML declaration are not carried over
    assert b"drawn by hand" not in cleaned and b"<?xml" not in cleaned
    assert svg.clean_svg(cleaned) == cleaned


def test_xml_attributes_and_an_inner_xlink_are_kept():
    data = (
        f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<defs><path id="p" d="M0,0 H9"/></defs>'
        '<text xml:space="preserve" xml:lang="en">a  b</text>'
        '<use xlink:href="#p"/></svg>'
    ).encode()

    cleaned = svg.clean_svg(data)

    assert b'xml:space="preserve"' in cleaned
    assert b'xlink:href="#p"' in cleaned


@pytest.mark.parametrize(
    "body, reason",
    [
        ("<script>alert(1)</script>", "<script>"),
        ('<rect onload="alert(1)"/>', "onload"),
        ('<g><circle r="1" onclick="x()"/></g>', "onclick"),
        (
            '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml"/></foreignObject>',
            "foreignObject",
        ),
        ('<image href="https://evil.test/x.png"/>', "<image>"),
        ('<a href="javascript:alert(1)"><rect/></a>', "<a>"),
        ('<use href="https://evil.test/sprite.svg#x"/>', "link out"),
        (
            '<use xmlns:xlink="http://www.w3.org/1999/xlink"'
            ' xlink:href="data:image/svg+xml,x"/>',
            "link out",
        ),
        ('<set attributeName="href" to="javascript:alert(1)"/>', "<set>"),
        ('<animate attributeName="href" values="javascript:alert(1)"/>', "<animate>"),
        ('<style>@import "https://evil.test/a.css";</style>', "CSS"),
        ("<style>rect { fill: url(https://evil.test/track) }</style>", "CSS"),
        ("<style>rect { background: u\\72l(x) }</style>", "CSS"),
        ('<rect style="fill: url(//evil.test/x)"/>', "CSS"),
        ('<rect fill="url(https://evil.test/x)"/>', "fill"),
        ('<rect xmlns:ev="http://evil.test/ns" ev:x="1"/>', "attribute"),
        (
            '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:title="x"'
            ' href="#a"/>',
            "attribute title",
        ),
    ],
)
def test_anything_that_could_run_or_load_is_refused(body, reason):
    with pytest.raises(svg.UnsafeSvgError, match=reason):
        svg.clean_svg(f"<svg {NS}>{body}</svg>".encode())


@pytest.mark.parametrize(
    "data",
    [
        # An XML bomb and an external file read both need a DOCTYPE
        b'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b'<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg xmlns="http://www.w3.org/2000/svg"/>',
        b"<svg><rect/></svg>",  # no SVG namespace: browsers show it as XML
        b'<html xmlns="http://www.w3.org/1999/xhtml"><svg/></html>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect>',  # not well-formed
    ],
)
def test_what_is_not_a_plain_svg_document_is_refused(data):
    with pytest.raises(svg.UnsafeSvgError):
        svg.clean_svg(data)


def test_only_svg_looking_bytes_are_taken_for_svg():
    assert svg.looks_like_svg(COVER)
    assert svg.looks_like_svg(b"  <svg xmlns='x'/>")
    assert not svg.looks_like_svg(b"\x89PNG\r\n")
    assert not svg.looks_like_svg(b"<html><svg/></html>")


@pytest.fixture
def storage(monkeypatch):
    service = R2StorageService()
    service.public_url = "https://assets.test"
    stored = []

    class FakeS3:
        async def put_object(self, **kwargs):
            stored.append(kwargs)

    @asynccontextmanager
    async def fake_client():
        yield FakeS3()

    monkeypatch.setattr(service, "_client", fake_client)
    return service, stored


@pytest.mark.anyio
async def test_an_svg_is_stored_cleaned_and_as_an_attachment(storage):
    service, stored = storage
    url = await service.upload_bytes(COVER, "covers")

    assert url.startswith("https://assets.test/covers/") and url.endswith(".svg")
    assert stored[0]["ContentType"] == "image/svg+xml"
    assert stored[0]["ContentDisposition"] == "attachment"
    assert stored[0]["Body"] == svg.clean_svg(COVER)


@pytest.mark.anyio
async def test_an_unsafe_svg_is_refused_with_the_reason(storage):
    service, stored = storage
    data = f"<svg {NS}><script>alert(1)</script></svg>".encode()

    with pytest.raises(UploadRefusedError, match="SVG refused: the element <script>"):
        await service.upload_bytes(data, "covers")
    assert stored == []
