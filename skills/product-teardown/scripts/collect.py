#!/usr/bin/env python3
"""Parse rendered HTML or BrowserSkill HTML responses and build a product evidence folder."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from build_dossier import item_identity
from extract_html import extract_html


FIELDS = ("main_images", "detail_images", "spec_groups", "attributes")
GATED = {"blocked", "login_required", "verification_required", "unavailable"}


def load_capture(path):
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.lstrip().startswith("{"):
        return raw, False
    reply = json.loads(raw)
    if not isinstance(reply.get("html"), str):
        raise ValueError(f"{path.name} 缺少 BrowserSkill 返回的 html 字段。")
    if reply.get("truncated") is not False:
        raise ValueError(f"{path.name} 未确认完整。请读取更小的区域后再保存。")
    return reply["html"], True


def parts_from(values, url):
    merged = {key: [] for key in (*FIELDS, "warnings")}
    for key in ("title", "price", "shop", "page_text"):
        merged[key] = ""
    item_id, merged["url"] = item_identity(url)

    text_chunks = []
    for scope, path in values:
        html, official_reply = load_capture(path)
        row = extract_html(html, scope=scope)
        if row["url"] and item_identity(row["url"])[0] != item_id:
            raise ValueError(f"{path.name} 的商品 ID 与目标不一致。")
        if row["access_state"] in GATED:
            raise ValueError(f"{path.name} 的页面状态为 {row['access_state']}。")
        for key in ("title", "price", "shop"):
            if row[key] and not merged[key]:
                merged[key] = row[key]
        if row["page_text"] and row["page_text"] not in text_chunks:
            text_chunks.append(row["page_text"])
        for key in FIELDS:
            for element in row[key]:
                if element not in merged[key]:
                    merged[key].append(element)
        if official_reply:
            merged["warnings"].append(f"{path.name} 的网页片段完整性标记通过。")
        if any("页面有登录提示" in warning for warning in row["warnings"]):
            merged["warnings"].append("商品页出现登录提示，缺失区域需要单独核对。")

    merged["page_text"] = "\n\n".join(text_chunks)[:20000]
    missing = [label for key, label in (("title", "标题"), ("shop", "店铺"), ("price", "页面展示价"),
               ("main_images", "主图"), ("detail_images", "详情图")) if not merged[key]]
    if not (merged["spec_groups"] or merged["attributes"]):
        missing.append("规格或商品参数")
    merged["access_state"] = "partial" if missing else "ok"
    if missing:
        merged["warnings"].append("缺少 " + "、".join(missing) + "。")
    merged["warnings"].append("主图与详情只覆盖浏览器已加载并被识别的区域。")
    if merged["spec_groups"]:
        merged["warnings"].append("规格列表记录页面选项，实际组合、库存及逐规格价格需另行取证。")
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Observed final Taobao/Tmall item URL")
    parser.add_argument("--part", action="append", default=[], metavar="SCOPE=FILE",
                        help="HTML/JSON file, scope: auto|product|main_images|detail_images|spec_groups|attributes")
    parser.add_argument("--archive", type=Path, help="A single Chrome saved HTML with companion assets")
    parser.add_argument("--captured-at", help="Original ISO 8601 capture time for an archive replay")
    parser.add_argument("--out", required=True, type=Path, help="New result folder")
    parser.add_argument("--download-images", action="store_true", help="Download public listing images from supported CDNs")
    args = parser.parse_args()
    if bool(args.archive) == bool(args.part):
        parser.error("请选择 --archive 历史网页回放，或 --part 浏览器采集文件。")
    values = [("auto", args.archive)] if args.archive else []
    for arg in args.part:
        scope, sep, path = arg.partition("=")
        if not sep or scope not in ("auto", "product", *FIELDS) or not path:
            parser.error("--part 需要 SCOPE=FILE。")
        values.append((scope, Path(path)))
    try:
        merged = parts_from(values, args.url)
        with tempfile.TemporaryDirectory(prefix="product-html-") as temp:
            normalized = Path(temp) / "fields.json"
            normalized.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, str(Path(__file__).with_name("build_dossier.py")), str(normalized),
                       "--url", args.url, "--out", str(args.out)]
            if args.archive:
                command += ["--archive", str(args.archive)]
            if args.captured_at:
                command += ["--captured-at", args.captured_at]
            if args.download_images:
                command += ["--download-images"]
            return subprocess.run(command, check=False).returncode
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
