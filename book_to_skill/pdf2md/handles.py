"""Process-local PDF handle cache: open once per (path, mtime, size).

Call ``close_all()`` (or the ``closing_handles`` context manager) to release
every cached document. ``convert_pdf`` does this in ``finally``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

# Tests assert ``len(_CACHE) == 0`` after convert_pdf returns or raises.
# Key: (resolved path, mtime_ns, size).
_CACHE: Dict[Tuple[str, int, int], "_Bundle"] = {}
_PATH_TO_KEY: Dict[str, Tuple[str, int, int]] = {}

PdfPath = Union[str, Path]


def cache_key(pdf_path: PdfPath) -> Tuple[str, int, int]:
    """Identity for a PDF file: resolved path + mtime + size."""
    path = Path(pdf_path).resolve()
    st = path.stat()
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


@dataclass
class _Bundle:
    plumber: Any = None
    plumber_nodes: Optional[List[Tuple[Any, dict]]] = None
    plumber_labels: Optional[List[Optional[str]]] = None
    plumber_pages: Dict[int, Any] = field(default_factory=dict)
    pypdf: Any = None
    pdfium: Any = None
    fitz_doc: Any = None
    n_pages: Optional[int] = None


def _get_bundle(pdf_path: PdfPath) -> _Bundle:
    key = cache_key(pdf_path)
    path_str = key[0]
    prev = _PATH_TO_KEY.get(path_str)
    if prev is not None and prev != key:
        stale = _CACHE.pop(prev, None)
        if stale is not None:
            _close_bundle(stale)
    if key not in _CACHE:
        _CACHE[key] = _Bundle()
        _PATH_TO_KEY[path_str] = key
    return _CACHE[key]


def page_count(pdf_path: PdfPath) -> int:
    """Page count via cached pypdf reader (``/Count`` / flatten once)."""
    bundle = _get_bundle(pdf_path)
    if bundle.n_pages is None:
        bundle.n_pages = len(get_pypdf(pdf_path).pages)
    return bundle.n_pages


def get_pypdf(pdf_path: PdfPath) -> Any:
    bundle = _get_bundle(pdf_path)
    if bundle.pypdf is None:
        from pypdf import PdfReader

        bundle.pypdf = PdfReader(cache_key(pdf_path)[0])
    return bundle.pypdf


def get_pdfium(pdf_path: PdfPath) -> Any:
    bundle = _get_bundle(pdf_path)
    if bundle.pdfium is None:
        import pypdfium2 as pdfium

        bundle.pdfium = pdfium.PdfDocument(cache_key(pdf_path)[0])
    return bundle.pdfium


def get_fitz(pdf_path: PdfPath) -> Any:
    bundle = _get_bundle(pdf_path)
    if bundle.fitz_doc is None:
        import fitz

        bundle.fitz_doc = fitz.open(cache_key(pdf_path)[0])
    return bundle.fitz_doc


def get_plumber_page(pdf_path: PdfPath, page_index: int) -> Any:
    """Return a pdfplumber ``Page`` for ``page_index``, or ``None`` if OOB.

    Does **not** touch ``pdf.pages`` (that property materializes every page).
    Walks the page tree once, then constructs ``PDFPage`` only for requested
    indices.
    """
    if page_index < 0:
        return None
    n = page_count(pdf_path)
    if page_index >= n:
        return None

    bundle = _get_bundle(pdf_path)
    cached = bundle.plumber_pages.get(page_index)
    if cached is not None:
        return cached

    import pdfplumber
    from pdfminer.pdfdocument import PDFNoPageLabels
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.page import Page

    if bundle.plumber is None:
        bundle.plumber = pdfplumber.open(cache_key(pdf_path)[0])

    if bundle.plumber_nodes is None:
        bundle.plumber_nodes = list(_iter_page_nodes(bundle.plumber.doc))

    nodes = bundle.plumber_nodes
    if page_index >= len(nodes):
        return None

    if bundle.plumber_labels is None:
        try:
            label_iter: Iterator[Optional[str]] = bundle.plumber.doc.get_page_labels()
        except PDFNoPageLabels:
            bundle.plumber_labels = [None] * len(nodes)
        else:
            labels: List[Optional[str]] = []
            for _ in range(len(nodes)):
                labels.append(next(label_iter, None))
            bundle.plumber_labels = labels

    objid, tree = nodes[page_index]
    miner_page = PDFPage(
        bundle.plumber.doc,
        objid,
        tree,
        bundle.plumber_labels[page_index],
    )
    # Page-local coords (y0/y1, top/bottom) do not use doctop; keep 0 to
    # avoid walking sibling pages just to accumulate heights.
    page = Page(
        bundle.plumber,
        miner_page,
        page_number=page_index + 1,
        initial_doctop=0,
    )
    bundle.plumber_pages[page_index] = page
    return page


def _iter_page_nodes(document: Any) -> Iterator[Tuple[Any, dict]]:
    """Same page-tree walk as ``PDFPage.create_pages``, without constructing pages."""
    from pdfminer import settings
    from pdfminer.pdfexceptions import PDFObjectNotFound
    from pdfminer.pdfpage import LITERAL_PAGE, LITERAL_PAGES, PDFPage
    from pdfminer.pdftypes import dict_value, list_value

    def depth_first_search(
        obj: Any,
        parent: dict,
        visited: Optional[set] = None,
    ) -> Iterator[Tuple[Any, dict]]:
        if isinstance(obj, int):
            object_id = obj
            object_properties = dict_value(document.getobj(object_id)).copy()
        else:
            object_id = obj.objid
            object_properties = dict_value(obj).copy()

        if visited is None:
            visited = set()
        if object_id in visited:
            return
        visited.add(object_id)

        for k, v in parent.items():
            if k in PDFPage.INHERITABLE_ATTRS and k not in object_properties:
                object_properties[k] = v

        object_type = object_properties.get("Type")
        if object_type is None and not settings.STRICT:
            object_type = object_properties.get("type")

        if object_type is LITERAL_PAGES and "Kids" in object_properties:
            for child in list_value(object_properties["Kids"]):
                yield from depth_first_search(child, object_properties, visited)
        elif object_type is LITERAL_PAGE:
            yield (object_id, object_properties)

    found = False
    if "Pages" in document.catalog:
        for item in depth_first_search(document.catalog["Pages"], document.catalog):
            found = True
            yield item
    if not found:
        for xref in document.xrefs:
            for objid in xref.get_objids():
                try:
                    obj = document.getobj(objid)
                    if isinstance(obj, dict) and obj.get("Type") is LITERAL_PAGE:
                        yield (objid, obj)
                except PDFObjectNotFound:
                    continue


def _close_one(fn) -> Optional[BaseException]:
    try:
        fn()
    except BaseException as exc:
        return exc
    return None


def _close_bundle(bundle: _Bundle) -> None:
    pages = list(bundle.plumber_pages.values())
    bundle.plumber_pages.clear()
    plumber = bundle.plumber
    bundle.plumber = None
    bundle.plumber_nodes = None
    bundle.plumber_labels = None
    reader = bundle.pypdf
    bundle.pypdf = None
    pdfium_doc = bundle.pdfium
    bundle.pdfium = None
    fitz_doc = bundle.fitz_doc
    bundle.fitz_doc = None
    bundle.n_pages = None

    first_err: Optional[BaseException] = None
    for page in pages:
        err = _close_one(page.close)
        if first_err is None:
            first_err = err
    if plumber is not None:
        err = _close_one(plumber.flush_cache)
        if first_err is None:
            first_err = err
        if not plumber.stream_is_external:
            err = _close_one(plumber.stream.close)
            if first_err is None:
                first_err = err
    if reader is not None:
        err = _close_one(reader.close)
        if first_err is None:
            first_err = err
    if pdfium_doc is not None:
        err = _close_one(pdfium_doc.close)
        if first_err is None:
            first_err = err
    if fitz_doc is not None:
        err = _close_one(fitz_doc.close)
        if first_err is None:
            first_err = err
    if first_err is not None:
        raise first_err


def close_all() -> None:
    """Close every cached document handle and empty the cache dict."""
    bundles = list(_CACHE.values())
    _CACHE.clear()
    _PATH_TO_KEY.clear()
    first_err: Optional[BaseException] = None
    for bundle in bundles:
        try:
            _close_bundle(bundle)
        except BaseException as exc:
            if first_err is None:
                first_err = exc
    if first_err is not None:
        raise first_err


@contextmanager
def closing_handles() -> Iterator[None]:
    try:
        yield
    finally:
        close_all()
