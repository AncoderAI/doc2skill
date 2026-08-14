"""P5: offline figure/table describe export + merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_to_skill.pdf2md.assemble import assemble_markdown
from book_to_skill.pdf2md.cli import main
from book_to_skill.pdf2md.convert import convert_pdf
from book_to_skill.pdf2md.describe import (
    export_requests,
    merge_descriptions,
    write_jsonl,
)
from book_to_skill.pdf2md.ir import (
    Block,
    BlockType,
    FigureBlock,
    TableBlock,
    TableCell,
    load_document_ir,
)
from book_to_skill.pdf2md.optimize.net_guard import (
    NetworkBlocked,
    install_guard,
    uninstall_guard,
)

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _text(block_id: str, page: int, text: str) -> Block:
    return Block(block_id=block_id, type=BlockType.TEXT, page=page, text=text)


def _figure(
    block_id: str,
    page: int,
    *,
    asset_path: str,
    caption: str | None = "图3-1 工作流示例",
    description: str | None = None,
    ocr_labels: list[str] | None = None,
    category: str = "diagram",
    bbox: tuple[float, float, float, float] = (72.0, 100.0, 520.0, 380.0),
    meta: dict | None = None,
) -> Block:
    return Block(
        block_id=block_id,
        type=BlockType.FIGURE,
        page=page,
        text=caption or "",
        bbox=bbox,
        figure=FigureBlock(
            asset_path=asset_path,
            category=category,
            caption=caption,
            ocr_labels=list(ocr_labels or []),
            description=description,
            bbox=bbox,
            round_trip="pending",
        ),
        meta=dict(meta or {}),
    )


def _table(block_id: str, page: int) -> Block:
    table = TableBlock(
        rows=2,
        cols=2,
        cells=[
            TableCell(text="A", row=0, col=0, is_header=True),
            TableCell(text="B", row=0, col=1, is_header=True),
            TableCell(text="1", row=1, col=0),
            TableCell(text="2", row=1, col=1),
        ],
        caption="表1 示例",
        bbox=(72.0, 400.0, 300.0, 500.0),
    )
    return Block(
        block_id=block_id,
        type=BlockType.TABLE,
        page=page,
        text=table.caption or "",
        bbox=table.bbox,
        table=table,
    )


def _make_bundle(tmp_path: Path, blocks: list[Block]) -> Path:
    """Build a bundle from the public synthetic PDF, then pin controlled blocks."""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    pdf = FIXTURES / "native_text.pdf"
    assert pdf.is_file()
    out = tmp_path / "bundle"
    convert_pdf(pdf, out, profile="fast", strict=False)
    ir = load_document_ir(out / "document.ir.json")
    ir.blocks = list(blocks)
    (out / "document.md").write_text(assemble_markdown(ir), encoding="utf-8")
    ir.to_json(out / "document.ir.json")
    for block in blocks:
        if block.figure and block.figure.asset_path:
            dest = out / block.figure.asset_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_PNG)
    return out


def _vlm_record(block_id: str, description: str, **extra) -> dict:
    rec = {
        "block_id": block_id,
        "description": description,
        "round_trip": "reproducible",
        "entities": ["输入", "求解器"],
        "relations": [{"from": "输入", "to": "求解器"}],
        "chart_data": None,
        "model": "test-vlm",
        "generated_at": "2026-08-13T00:00:00Z",
    }
    rec.update(extra)
    return rec


def test_export_requests_one_per_figure_relative_asset_path(tmp_path):
    bundle = _make_bundle(
        tmp_path,
        [
            _figure("p0002-fig-0000", 2, asset_path="assets/figures/p0002_fig0000.png"),
            _figure("p0001-fig-0001", 1, asset_path="assets/figures/p0001_fig0001.png"),
            _table("p0001-tbl-0000", 1),
        ],
    )
    records = export_requests(bundle)
    assert [r["block_id"] for r in records] == ["p0001-fig-0001", "p0002-fig-0000"]
    assert all(r["kind"] == "figure" for r in records)
    for rec in records:
        asset = rec["asset_path"]
        assert not Path(asset).is_absolute()
        assert asset.startswith("assets/")
        assert rec["caption"] == "图3-1 工作流示例"
        assert rec["category"] == "diagram"
        assert rec["bbox"] == [72.0, 100.0, 520.0, 380.0]
    with_tables = export_requests(bundle, include_tables=True)
    kinds = [r["kind"] for r in with_tables]
    assert kinds.count("figure") == 2
    assert kinds.count("table") == 1
    table_rec = next(r for r in with_tables if r["kind"] == "table")
    assert "table_markdown" in table_rec
    assert table_rec["caption"] == "表1 示例"


def test_export_requests_same_page_context_truncated_400(tmp_path):
    before = ("前" * 100) + ("近" * 400)
    after = ("后" * 400) + ("远" * 100)
    other_page = "其他页正文" * 50
    bundle = _make_bundle(
        tmp_path,
        [
            _text("p0002-text-0000", 2, other_page),
            _text("p0001-text-0000", 1, before),
            _figure("p0001-fig-0001", 1, asset_path="assets/figures/p0001_fig0001.png"),
            _text("p0001-text-0001", 1, after),
        ],
    )
    records = export_requests(bundle)
    assert len(records) == 1
    rec = records[0]
    assert rec["context_before"] == "近" * 400
    assert rec["context_after"] == "后" * 400
    assert len(rec["context_before"]) == 400
    assert len(rec["context_after"]) == 400
    assert "其他页" not in rec["context_before"]
    assert "其他页" not in rec["context_after"]


def test_merge_applies_vlm_quote_and_meta(tmp_path):
    fig_id = "p0001-fig-0001"
    tbl_id = "p0001-tbl-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png"),
            _table(tbl_id, 1),
        ],
    )
    desc = "流程自左向右：输入参数经求解器计算后输出目标值。\n第二行补充。"
    report = merge_descriptions(
        bundle,
        [
            _vlm_record(fig_id, desc),
            _vlm_record(tbl_id, "两列表格列出 A/B 两列取值。"),
        ],
    )
    assert report["described"] == 2
    assert report["unknown_ids"] == []
    assert report["rejected"] == {"empty": 0, "bad_round_trip": 0, "wrong_type": 0}

    md = (bundle / "document.md").read_text(encoding="utf-8")
    assert "> **【图：图3-1 工作流示例】**" in md
    assert "> 流程自左向右：输入参数经求解器计算后输出目标值。" in md
    assert "> 第二行补充。" in md
    assert "> **【表：表1 示例】**" in md
    assert "> 两列表格列出 A/B 两列取值。" in md

    ir = json.loads((bundle / "document.ir.json").read_text(encoding="utf-8"))
    fig = next(b for b in ir["blocks"] if b["block_id"] == fig_id)
    assert fig["meta"]["description_source"] == "vlm"
    assert fig["meta"]["description_model"] == "test-vlm"
    assert fig["meta"]["described_at"] == "2026-08-13T00:00:00Z"
    assert fig["figure"]["description"] == desc
    assert fig["figure"]["entities"] == ["输入", "求解器"]
    assert fig["figure"]["round_trip"] == "reproducible"


def test_merge_unknown_id_strict_cli_exit_2(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    original_md = (bundle / "document.md").read_bytes()
    records = [
        _vlm_record("p9999-fig-0000", "这段描述绝不能写进去。"),
    ]
    report = merge_descriptions(bundle, records)
    assert "p9999-fig-0000" in report["unknown_ids"]
    assert report["described"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    fig = next(b for b in ir.blocks if b.block_id == fig_id)
    assert fig.figure is not None
    assert fig.figure.description is None
    assert fig.meta.get("description_source") != "vlm"
    assert "【图：" not in (bundle / "document.md").read_text(encoding="utf-8")
    assert (bundle / "document.md").read_bytes() == original_md

    desc_path = tmp_path / "unknown.jsonl"
    write_jsonl(desc_path, records)
    rc = main(
        [
            "describe-merge",
            "--bundle",
            str(bundle),
            "--descriptions",
            str(desc_path),
            "--strict",
        ]
    )
    assert rc == 2


def test_merge_empty_description_rejected(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    report = merge_descriptions(
        bundle,
        [
            _vlm_record(fig_id, "", round_trip="reproducible"),
            {**_vlm_record(fig_id, "x"), "description": "   \n"},
        ],
    )
    assert report["rejected"]["empty"] == 2
    assert report["described"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    fig = next(b for b in ir.blocks if b.block_id == fig_id)
    assert fig.figure is not None
    assert fig.figure.description is None
    assert fig.meta.get("description_source") != "vlm"
    assert "【图：" not in (bundle / "document.md").read_text(encoding="utf-8")


def test_merge_bad_round_trip_rejected(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    report = merge_descriptions(
        bundle,
        [_vlm_record(fig_id, "合法句子，但 round_trip 非法。", round_trip="ok")],
    )
    assert report["rejected"]["bad_round_trip"] == 1
    assert report["described"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    fig = next(b for b in ir.blocks if b.block_id == fig_id)
    assert fig.figure is not None
    assert fig.figure.description is None
    assert fig.meta.get("description_source") != "vlm"
    assert "【图：" not in (bundle / "document.md").read_text(encoding="utf-8")


def test_merge_idempotent_document_md(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    records = [_vlm_record(fig_id, "流程自左向右：输入经求解器后输出。")]
    merge_descriptions(bundle, records)
    first = (bundle / "document.md").read_bytes()
    merge_descriptions(bundle, records)
    second = (bundle / "document.md").read_bytes()
    assert first == second
    assert b"\xe3\x80\x90\xe5\x9b\xbe\xef\xbc\x9a" in first  # 【图：


def test_ocr_label_description_not_rendered_as_quote(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(
                fig_id,
                1,
                asset_path="assets/figures/p0001_fig0001.png",
                caption=None,
                description="Labels: a; b",
            )
        ],
    )
    md = (bundle / "document.md").read_text(encoding="utf-8")
    assert "![Labels: a; b](assets/figures/p0001_fig0001.png)" in md
    assert "【图：" not in md
    assert not any(line.startswith(">") for line in md.splitlines())


def test_describe_no_network_under_net_guard(tmp_path):
    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    uninstall_guard()
    install_guard(allow_loopback=True)
    try:
        records = export_requests(bundle)
        merge_descriptions(bundle, [_vlm_record(fig_id, "离线合并的真实描述。")])
        assert records
        md = (bundle / "document.md").read_text(encoding="utf-8")
        assert "> **【图：图3-1 工作流示例】**" in md
    except NetworkBlocked:
        pytest.fail("describe export+merge must not raise NetworkBlocked")
    finally:
        uninstall_guard()


def test_no_caption_vlm_alt_is_not_description(tmp_path):
    fig_id = "p0001-fig-0001"
    tbl_id = "p0001-tbl-0000"
    desc = (
        "流程自左向右：输入参数经求解器计算后输出目标值。\n"
        "第二行：反馈回路从输出指回输入。"
    )
    table = TableBlock(
        rows=2,
        cols=2,
        cells=[
            TableCell(text="A", row=0, col=0, is_header=True),
            TableCell(text="B", row=0, col=1, is_header=True),
            TableCell(text="1", row=1, col=0),
            TableCell(text="2", row=1, col=1),
        ],
        caption=None,
        bbox=(72.0, 400.0, 300.0, 500.0),
    )
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(
                fig_id,
                1,
                asset_path="assets/figures/p0001_fig0001.png",
                caption=None,
            ),
            Block(
                block_id=tbl_id,
                type=BlockType.TABLE,
                page=1,
                text="",
                bbox=table.bbox,
                table=table,
            ),
        ],
    )
    merge_descriptions(
        bundle,
        [
            _vlm_record(fig_id, desc),
            _vlm_record(tbl_id, "两列取值对照。"),
        ],
    )
    md = (bundle / "document.md").read_text(encoding="utf-8")
    img_line = next(
        line for line in md.splitlines() if line.startswith("![") and "](" in line
    )
    alt = img_line[2 : img_line.index("](")]
    assert alt != desc
    assert "\n" not in alt
    assert alt == fig_id
    assert "> **【图：】**" not in md
    assert f"> **【图：{fig_id}】**" in md
    assert "> **【表：】**" not in md
    assert f"> **【表：{tbl_id}】**" in md


def test_no_caption_without_vlm_render_byte_identical():
    from book_to_skill.pdf2md.ir import DocumentIR, PageInfo, PageType

    fig_id = "p0001-fig-0001"
    asset = "assets/figures/a.png"
    ir = DocumentIR(
        schema_version="1.0.0",
        source_path="x.pdf",
        source_sha256="0" * 64,
        page_count=1,
        pages=[
            PageInfo(
                page=1,
                width=612.0,
                height=792.0,
                rotation=0,
                page_type=PageType.NATIVE_TEXT,
            )
        ],
        blocks=[
            _figure(
                fig_id,
                1,
                asset_path=asset,
                caption=None,
                description="Labels: a; b",
            )
        ],
    )
    md = assemble_markdown(ir)
    expected = (
        "<!-- page: 1 -->\n"
        "\n"
        f"<!-- block: {fig_id} -->\n"
        f"![Labels: a; b]({asset})\n"
    )
    assert md == expected
    ir_empty = DocumentIR(
        schema_version="1.0.0",
        source_path="x.pdf",
        source_sha256="0" * 64,
        page_count=1,
        pages=list(ir.pages),
        blocks=[
            _figure(fig_id, 1, asset_path=asset, caption=None, description=None)
        ],
    )
    assert assemble_markdown(ir_empty) == (
        "<!-- page: 1 -->\n"
        "\n"
        f"<!-- block: {fig_id} -->\n"
        f"![figure]({asset})\n"
    )


def test_report_figure_table_coverage_is_self_consistent(tmp_path):
    fig_id = "p0001-fig-0001"
    tbl_id = "p0001-tbl-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png"),
            _table(tbl_id, 1),
        ],
    )
    report = merge_descriptions(
        bundle,
        [
            _vlm_record(fig_id, "流程自左向右：输入经求解器后输出。"),
            _vlm_record(tbl_id, "两列表格列出 A/B 两列取值。"),
        ],
    )
    assert report["total_figures"] == 1
    assert report["described_figures"] == 1
    assert report["total_tables"] == 1
    assert report["described_tables"] == 1
    assert report["described_figures"] <= report["total_figures"]
    assert report["described_tables"] <= report["total_tables"]
    fig_coverage = report["described_figures"] / report["total_figures"]
    tbl_coverage = report["described_tables"] / report["total_tables"]
    assert fig_coverage == 1.0
    assert tbl_coverage == 1.0
    dumped = json.loads((bundle / "describe-report.json").read_text(encoding="utf-8"))
    assert dumped["described_figures"] == 1
    assert dumped["described_tables"] == 1
    assert dumped["total_figures"] == 1
    assert dumped["total_tables"] == 1


def test_pending_only_skips_already_described(tmp_path):
    ids = [
        ("p0003-fig-0000", 3),
        ("p0001-fig-0000", 1),
        ("p0002-fig-0000", 2),
        ("p0001-fig-0001", 1),
    ]
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(bid, page, asset_path=f"assets/figures/{bid}.png")
            for bid, page in ids
        ],
    )
    all_ids = [r["block_id"] for r in export_requests(bundle)]
    assert all_ids == [
        "p0001-fig-0000",
        "p0001-fig-0001",
        "p0002-fig-0000",
        "p0003-fig-0000",
    ]
    half = all_ids[:2]
    merge_descriptions(bundle, [_vlm_record(bid, f"已描述 {bid}。") for bid in half])
    pending = export_requests(bundle, pending_only=True)
    assert [r["block_id"] for r in pending] == ["p0002-fig-0000", "p0003-fig-0000"]
    assert all(r["block_id"] not in half for r in pending)


def test_limit_truncates_sorted_prefix(tmp_path):
    bundle = _make_bundle(
        tmp_path,
        [
            _figure("p0003-fig-0000", 3, asset_path="assets/figures/p0003_fig0000.png"),
            _figure("p0001-fig-0001", 1, asset_path="assets/figures/p0001_fig0001.png"),
            _figure("p0002-fig-0000", 2, asset_path="assets/figures/p0002_fig0000.png"),
            _figure("p0001-fig-0000", 1, asset_path="assets/figures/p0001_fig0000.png"),
        ],
    )
    records = export_requests(bundle, limit=2)
    assert [r["block_id"] for r in records] == ["p0001-fig-0000", "p0001-fig-0001"]
    assert len(records) == 2
    full = export_requests(bundle)
    assert [r["block_id"] for r in full[:2]] == [r["block_id"] for r in records]


def test_pending_only_and_limit_combine(tmp_path):
    bundle = _make_bundle(
        tmp_path,
        [
            _figure("p0003-fig-0000", 3, asset_path="assets/figures/p0003_fig0000.png"),
            _figure("p0001-fig-0001", 1, asset_path="assets/figures/p0001_fig0001.png"),
            _figure("p0002-fig-0000", 2, asset_path="assets/figures/p0002_fig0000.png"),
            _figure("p0001-fig-0000", 1, asset_path="assets/figures/p0001_fig0000.png"),
        ],
    )
    merge_descriptions(
        bundle,
        [_vlm_record("p0001-fig-0000", "排序后的第一条已描述。")],
    )
    records = export_requests(bundle, pending_only=True, limit=2)
    assert [r["block_id"] for r in records] == ["p0001-fig-0001", "p0002-fig-0000"]
    pending_all = export_requests(bundle, pending_only=True)
    assert [r["block_id"] for r in pending_all] == [
        "p0001-fig-0001",
        "p0002-fig-0000",
        "p0003-fig-0000",
    ]
    assert records == pending_all[:2]


def test_describe_status_counts_and_does_not_touch_files(tmp_path):
    from book_to_skill.pdf2md.describe import describe_status

    fig_a = "p0001-fig-0000"
    fig_b = "p0002-fig-0000"
    tbl = "p0001-tbl-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_a, 1, asset_path="assets/figures/p0001_fig0000.png"),
            _figure(fig_b, 2, asset_path="assets/figures/p0002_fig0000.png"),
            _table(tbl, 1),
        ],
    )
    merge_descriptions(bundle, [_vlm_record(fig_a, "仅描述第一张图。")])

    def snapshot() -> dict[str, tuple[int, bytes]]:
        snap: dict[str, tuple[int, bytes]] = {}
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                st = path.stat()
                snap[str(path.relative_to(bundle))] = (st.st_mtime_ns, path.read_bytes())
        return snap

    before = snapshot()
    status = describe_status(bundle)
    after = snapshot()
    assert before == after
    assert status == {
        "total_figures": 2,
        "described_figures": 1,
        "pending_figures": 1,
        "total_tables": 1,
        "described_tables": 0,
        "pending_tables": 1,
        "done": False,
    }


def test_describe_status_done_after_all_described(tmp_path):
    from book_to_skill.pdf2md.describe import describe_status

    fig_id = "p0001-fig-0001"
    tbl_id = "p0001-tbl-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png"),
            _table(tbl_id, 1),
        ],
    )
    assert describe_status(bundle)["done"] is False
    merge_descriptions(
        bundle,
        [
            _vlm_record(fig_id, "流程自左向右：输入经求解器后输出。"),
            _vlm_record(tbl_id, "两列表格列出 A/B 两列取值。"),
        ],
    )
    status = describe_status(bundle)
    assert status["pending_figures"] == 0
    assert status["pending_tables"] == 0
    assert status["done"] is True


def test_describe_status_cli_json(tmp_path, capsys):
    from book_to_skill.pdf2md.describe import describe_status

    fig_id = "p0001-fig-0001"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path="assets/figures/p0001_fig0001.png")],
    )
    rc = main(["describe-status", "--bundle", str(bundle), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == describe_status(bundle)
    assert payload["total_figures"] == 1
    assert payload["pending_figures"] == 1
    assert payload["done"] is False


def _not_a_figure_record(block_id: str, reason: str | None, **extra) -> dict:
    rec = {
        "block_id": block_id,
        "verdict": "not_a_figure",
        "reason": reason,
        "model": "test-vlm",
        "generated_at": "2026-08-13T00:00:00Z",
    }
    rec.update(extra)
    return rec


def test_not_a_figure_removes_block_asset_and_anchor(tmp_path):
    fig_keep = "p0001-fig-0000"
    fig_drop = "p0001-fig-0001"
    keep_asset = "assets/figures/p0001_fig0000.png"
    drop_asset = "assets/figures/p0001_fig0001.png"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_keep, 1, asset_path=keep_asset),
            _figure(fig_drop, 1, asset_path=drop_asset),
        ],
    )
    assert (bundle / drop_asset).is_file()
    report = merge_descriptions(
        bundle,
        [
            _not_a_figure_record(
                fig_drop,
                "正文提示框：CAUTION！The life expectancy is limited！",
            )
        ],
    )
    assert report["removed_not_a_figure"] == 1
    assert report["removed_block_ids"] == [fig_drop]
    ir = load_document_ir(bundle / "document.ir.json")
    ids = [b.block_id for b in ir.blocks]
    assert fig_drop not in ids
    assert fig_keep in ids
    assert not (bundle / drop_asset).exists()
    assert (bundle / keep_asset).is_file()
    md = (bundle / "document.md").read_text(encoding="utf-8")
    assert f"<!-- block: {fig_drop} -->" not in md
    assert f"<!-- block: {fig_keep} -->" in md


def test_not_a_figure_empty_reason_kept(tmp_path):
    fig_id = "p0001-fig-0001"
    asset = "assets/figures/p0001_fig0001.png"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path=asset)],
    )
    original_md = (bundle / "document.md").read_bytes()
    report = merge_descriptions(bundle, [_not_a_figure_record(fig_id, "")])
    assert report["rejected"]["missing_reason"] == 1
    assert report["removed_not_a_figure"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    assert any(b.block_id == fig_id for b in ir.blocks)
    assert (bundle / asset).is_file()
    assert (bundle / "document.md").read_bytes() == original_md
    assert not (bundle / "removed-blocks.jsonl").exists()


def test_illegal_verdict_rejected_block_kept(tmp_path):
    fig_id = "p0001-fig-0001"
    asset = "assets/figures/p0001_fig0001.png"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path=asset)],
    )
    original_md = (bundle / "document.md").read_bytes()
    rec = _vlm_record(fig_id, "这段描述绝不能写进去。", verdict="maybe")
    report = merge_descriptions(bundle, [rec])
    assert report["rejected"]["bad_verdict"] == 1
    assert report["described"] == 0
    assert report["removed_not_a_figure"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    fig = next(b for b in ir.blocks if b.block_id == fig_id)
    assert fig.figure is not None
    assert fig.figure.description is None
    assert (bundle / asset).is_file()
    assert (bundle / "document.md").read_bytes() == original_md


def test_already_described_not_a_figure_kept(tmp_path):
    fig_id = "p0001-fig-0001"
    asset = "assets/figures/p0001_fig0001.png"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path=asset)],
    )
    merge_descriptions(bundle, [_vlm_record(fig_id, "流程自左向右：输入经求解器后输出。")])
    report = merge_descriptions(
        bundle,
        [_not_a_figure_record(fig_id, "正文提示框，但此图已经描述过。")],
    )
    assert report["rejected"]["already_described"] == 1
    assert report["removed_not_a_figure"] == 0
    ir = load_document_ir(bundle / "document.ir.json")
    fig = next(b for b in ir.blocks if b.block_id == fig_id)
    assert fig.meta.get("description_source") == "vlm"
    assert fig.figure is not None
    assert fig.figure.description == "流程自左向右：输入经求解器后输出。"
    assert (bundle / asset).is_file()
    md = (bundle / "document.md").read_text(encoding="utf-8")
    assert f"<!-- block: {fig_id} -->" in md
    assert "> **【图：图3-1 工作流示例】**" in md


def test_removed_blocks_jsonl_fields(tmp_path):
    fig_id = "p0001-fig-0001"
    asset = "assets/figures/p0001_fig0001.png"
    reason = "正文提示框：CAUTION！The life expectancy is limited！"
    bundle = _make_bundle(
        tmp_path,
        [_figure(fig_id, 1, asset_path=asset, caption=None)],
    )
    merge_descriptions(
        bundle,
        [_not_a_figure_record(fig_id, reason, model="cursor-grok-4.6")],
    )
    path = bundle / "removed-blocks.jsonl"
    assert path.is_file()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    for key in ("block_id", "page", "asset_path", "reason", "model", "removed_at"):
        assert key in row
        assert row[key] not in (None, "")
    assert row["block_id"] == fig_id
    assert row["page"] == 1
    assert row["asset_path"] == asset
    assert row["reason"] == reason
    assert row["model"] == "cursor-grok-4.6"


def test_status_and_pending_only_after_removal(tmp_path):
    from book_to_skill.pdf2md.describe import describe_status

    fig_keep = "p0001-fig-0000"
    fig_drop = "p0002-fig-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_keep, 1, asset_path="assets/figures/p0001_fig0000.png"),
            _figure(fig_drop, 2, asset_path="assets/figures/p0002_fig0000.png"),
        ],
    )
    before = describe_status(bundle)
    assert before["total_figures"] == 2
    assert before["pending_figures"] == 2
    merge_descriptions(
        bundle,
        [_not_a_figure_record(fig_drop, "整页混合内容，不是单一可描述的图。")],
    )
    after = describe_status(bundle)
    assert after["total_figures"] == before["total_figures"] - 1
    assert after["pending_figures"] == before["pending_figures"] - 1
    pending = export_requests(bundle, pending_only=True)
    assert [r["block_id"] for r in pending] == [fig_keep]
    assert all(r["block_id"] != fig_drop for r in pending)


def test_no_verdict_document_md_byte_identical(tmp_path):
    fig_id = "p0001-fig-0001"
    asset = "assets/figures/p0001_fig0001.png"
    desc = "流程自左向右：输入经求解器后输出。"

    def figures():
        return [_figure(fig_id, 1, asset_path=asset)]

    bundle_plain = _make_bundle(tmp_path / "plain", figures())
    original = (bundle_plain / "document.md").read_bytes()
    merge_descriptions(bundle_plain, [])
    assert (bundle_plain / "document.md").read_bytes() == original

    records = [_vlm_record(fig_id, desc)]
    assert "verdict" not in records[0]
    merge_descriptions(bundle_plain, records)
    md_plain = (bundle_plain / "document.md").read_bytes()
    assert md_plain != original
    assert not (bundle_plain / "removed-blocks.jsonl").exists()

    bundle_flag = _make_bundle(tmp_path / "flag", figures())
    merge_descriptions(bundle_flag, [{**records[0], "verdict": "figure"}])
    assert (bundle_flag / "document.md").read_bytes() == md_plain
    ir = load_document_ir(bundle_plain / "document.ir.json")
    assert any(b.block_id == fig_id for b in ir.blocks)


def test_mixed_batch_describe_delete_and_reject(tmp_path):
    fig_desc = "p0001-fig-0000"
    fig_drop = "p0001-fig-0001"
    fig_bad = "p0002-fig-0000"
    fig_empty = "p0003-fig-0000"
    bundle = _make_bundle(
        tmp_path,
        [
            _figure(fig_desc, 1, asset_path="assets/figures/p0001_fig0000.png"),
            _figure(fig_drop, 1, asset_path="assets/figures/p0001_fig0001.png"),
            _figure(fig_bad, 2, asset_path="assets/figures/p0002_fig0000.png"),
            _figure(fig_empty, 3, asset_path="assets/figures/p0003_fig0000.png"),
        ],
    )
    report = merge_descriptions(
        bundle,
        [
            _vlm_record(fig_desc, "流程自左向右：输入经求解器后输出。"),
            _not_a_figure_record(fig_drop, "正文提示框：CAUTION！"),
            {**_vlm_record(fig_bad, "非法判定不应应用。"), "verdict": "maybe"},
            _not_a_figure_record(fig_empty, ""),
        ],
    )
    assert report["described"] == 1
    assert report["described_figures"] == 1
    assert report["removed_not_a_figure"] == 1
    assert report["removed_block_ids"] == [fig_drop]
    assert report["rejected"]["bad_verdict"] == 1
    assert report["rejected"]["missing_reason"] == 1
    assert report["rejected"]["empty"] == 0
    assert report["rejected"]["bad_round_trip"] == 0
    assert report["rejected"]["wrong_type"] == 0
    assert report["rejected"]["already_described"] == 0
    assert report["total_figures"] == 3

    ir = load_document_ir(bundle / "document.ir.json")
    ids = [b.block_id for b in ir.blocks]
    assert fig_drop not in ids
    assert fig_desc in ids
    assert fig_bad in ids
    assert fig_empty in ids
    assert not (bundle / "assets/figures/p0001_fig0001.png").exists()
    assert (bundle / "assets/figures/p0002_fig0000.png").is_file()
    assert (bundle / "assets/figures/p0003_fig0000.png").is_file()

    md = (bundle / "document.md").read_text(encoding="utf-8")
    assert f"<!-- block: {fig_drop} -->" not in md
    assert f"<!-- block: {fig_desc} -->" in md
    assert f"<!-- block: {fig_bad} -->" in md
    assert f"<!-- block: {fig_empty} -->" in md
    assert "> **【图：图3-1 工作流示例】**" in md
    described = next(b for b in ir.blocks if b.block_id == fig_desc)
    assert described.meta.get("description_source") == "vlm"
    bad = next(b for b in ir.blocks if b.block_id == fig_bad)
    assert bad.meta.get("description_source") != "vlm"
