#!/usr/bin/env python3
"""统计各 case_{khCode} collection 中 positive / negative 文档数量与占比。

用法（从仓库根目录，即 case_refinery 的上一级）::

    python case_refinery/scripts/stats_case_polarity.py
    python case_refinery/scripts/stats_case_polarity.py --output case_polarity_stats.md
    python case_refinery/scripts/stats_case_polarity.py --kh-codes KH001,KH002

或在 case_refinery 目录内::

    python scripts/stats_case_polarity.py

依赖环境变量与主服务相同（``CASE_REFINERY_LANCEDB_*`` 等）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

# 支持 ``python scripts/stats_case_polarity.py`` 直跑。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CASE_REFINERY_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_CASE_REFINERY_DIR, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from case_refinery.config import Settings, get_settings
from case_refinery.pipeline.lancedb_client import LanceDBError, LanceDBV2Client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionPolarityStats:
    kh_code: str
    collection_id: str
    n_documents_meta: int | None
    total_active: int
    positive: int
    negative: int
    unknown: int
    tombstoned: int

    @property
    def positive_pct(self) -> float:
        return _pct(self.positive, self.total_active)

    @property
    def negative_pct(self) -> float:
        return _pct(self.negative, self.total_active)


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return n * 100.0 / total


def _fmt_pct(n: int, total: int) -> str:
    return f"{_pct(n, total):.1f}%"


def _parse_kh_codes(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    codes = {x.strip() for x in raw.split(",") if x.strip()}
    return codes or None


async def list_case_collections(
    cli: LanceDBV2Client,
    settings: Settings,
    *,
    kh_filter: set[str] | None = None,
) -> list[tuple[str, str, int | None]]:
    """返回 ``(kh_code, collection_id, n_documents_meta)`` 列表。"""
    data = await cli._request("GET", "/v2/collections")
    rows: list[tuple[str, str, int | None]] = []
    prefix = settings.lancedb_collection_prefix

    for item in (data or {}).get("collections") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("collection_id") or "")
        if not cid.startswith(prefix):
            continue
        kh_code = cid[len(prefix):]
        if kh_filter is not None and kh_code not in kh_filter:
            continue
        n_docs = item.get("n_documents")
        n_documents_meta = int(n_docs) if n_docs is not None else None
        rows.append((kh_code, cid, n_documents_meta))

    rows.sort(key=lambda x: x[0])
    return rows


async def count_collection_polarities(
    cli: LanceDBV2Client,
    collection_id: str,
    *,
    page_size: int,
) -> tuple[int, int, int, int, int]:
    """翻页统计 polarity。返回 (total_active, positive, negative, unknown, tombstoned)。"""
    positive = 0
    negative = 0
    unknown = 0
    tombstoned = 0
    offset = 0

    while True:
        params = {
            "include_content": "false",
            "limit": page_size,
            "offset": offset,
        }
        data = await cli._request(
            "GET",
            f"/v2/collections/{collection_id}/documents",
            params=params,
        )
        docs = (data or {}).get("documents") or []
        n = len(docs)

        for doc in docs:
            if not isinstance(doc, dict):
                unknown += 1
                continue
            md = doc.get("metadata") or {}
            if bool(md.get("tombstoned", False)):
                tombstoned += 1
                continue

            polarity = md.get("case_polarity")
            if polarity == "positive":
                positive += 1
            elif polarity == "negative":
                negative += 1
            else:
                unknown += 1

        if n < page_size:
            break
        offset += page_size

    total_active = positive + negative + unknown
    return total_active, positive, negative, unknown, tombstoned


async def collect_stats(
    cli: LanceDBV2Client,
    settings: Settings,
    *,
    kh_filter: set[str] | None = None,
) -> list[CollectionPolarityStats]:
    collections = await list_case_collections(cli, settings, kh_filter=kh_filter)
    if not collections:
        logger.warning("未找到匹配的 case_* collection")
        return []

    stats: list[CollectionPolarityStats] = []
    for kh_code, collection_id, n_documents_meta in collections:
        logger.info("[stats] 统计 %s ...", collection_id)
        total_active, positive, negative, unknown, tombstoned = (
            await count_collection_polarities(
                cli,
                collection_id,
                page_size=settings.lancedb_list_page_size,
            )
        )
        stats.append(
            CollectionPolarityStats(
                kh_code=kh_code,
                collection_id=collection_id,
                n_documents_meta=n_documents_meta,
                total_active=total_active,
                positive=positive,
                negative=negative,
                unknown=unknown,
                tombstoned=tombstoned,
            )
        )
    return stats


def render_markdown(
    stats: list[CollectionPolarityStats],
    *,
    settings: Settings,
    generated_at: datetime,
) -> str:
    ts = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Case Collection Polarity 统计",
        "",
        f"- 生成时间: {ts}",
        f"- LanceDB: `{settings.lancedb_base_url}`",
        f"- Collection 前缀: `{settings.lancedb_collection_prefix}`",
        "",
        "| khCode | collection | 有效总数 | positive | positive占比 | negative | negative占比 | 未知 | tombstoned |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    sum_active = 0
    sum_positive = 0
    sum_negative = 0
    sum_unknown = 0
    sum_tombstoned = 0

    for row in stats:
        sum_active += row.total_active
        sum_positive += row.positive
        sum_negative += row.negative
        sum_unknown += row.unknown
        sum_tombstoned += row.tombstoned
        lines.append(
            "| {kh} | `{cid}` | {total} | {pos} | {pos_pct} | {neg} | {neg_pct} | {unk} | {tomb} |".format(
                kh=row.kh_code,
                cid=row.collection_id,
                total=row.total_active,
                pos=row.positive,
                pos_pct=_fmt_pct(row.positive, row.total_active),
                neg=row.negative,
                neg_pct=_fmt_pct(row.negative, row.total_active),
                unk=row.unknown,
                tomb=row.tombstoned,
            )
        )

    if stats:
        lines.extend(
            [
                "",
                "## 汇总",
                "",
                f"- collection 数量: {len(stats)}",
                f"- 有效文档总数: {sum_active}",
                f"- positive: {sum_positive} ({_fmt_pct(sum_positive, sum_active)})",
                f"- negative: {sum_negative} ({_fmt_pct(sum_negative, sum_active)})",
                f"- 未知 polarity: {sum_unknown} ({_fmt_pct(sum_unknown, sum_active)})",
                f"- tombstoned: {sum_tombstoned}",
            ]
        )
    else:
        lines.extend(["", "_无数据_"])

    lines.append("")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    kh_filter = _parse_kh_codes(args.kh_codes)
    generated_at = datetime.now(timezone.utc)

    cli = LanceDBV2Client(settings=settings)
    try:
        stats = await collect_stats(cli, settings, kh_filter=kh_filter)
    except LanceDBError as e:
        logger.error("LanceDB 调用失败: %s", e)
        return 1
    finally:
        await cli.aclose()

    markdown = render_markdown(stats, settings=settings, generated_at=generated_at)
    print(markdown)

    if args.output:
        out_path = os.path.abspath(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        logger.info("已写入 %s", out_path)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="统计各 case_{khCode} collection 的 positive/negative 数量与占比",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="case_polarity_stats.md",
        help="Markdown 输出路径（默认: case_polarity_stats.md；传空字符串则只打印不写文件）",
    )
    parser.add_argument(
        "--kh-codes",
        default="",
        help="仅统计指定 khCode，逗号分隔（默认: 全部 case_* collection）",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("CASE_REFINERY_LOG_LEVEL", "INFO"),
        help="日志级别（默认: INFO）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if args.output == "":
        args.output = None
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
