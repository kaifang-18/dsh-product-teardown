#!/usr/bin/env python3
"""Turn parsed listing fields into a dated evidence folder and optional image copies."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_IMAGE_BYTES = 18 * 1024 * 1024
PRODUCT_HOSTS = {"detail.tmall.com", "item.taobao.com", "detail.tmall.hk", "detail.taobao.com"}
IMAGE_DOMAINS = ("alicdn.com", "tbcdn.cn", "taobaocdn.com")


def item_identity(url):
    parts = urlsplit(url)
    values = parse_qs(parts.query).get("id", [])
    if parts.scheme != "https" or parts.hostname not in PRODUCT_HOSTS:
        raise ValueError("请使用浏览器解析后的 HTTPS 淘宝或天猫商品详情链接。")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("商品链接包含不支持的登录信息或端口。")
    if len(values) != 1 or not re.fullmatch(r"[0-9]{5,32}", values[0]):
        raise ValueError("商品链接缺少唯一商品 ID。")
    return values[0], f"https://{parts.hostname}/item.htm?id={values[0]}"


def public_image(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (parts.scheme == "https" and not (parts.username or parts.password)
            and parts.port in (None, 443)
            and any(host == suffix or host.endswith("." + suffix) for suffix in IMAGE_DOMAINS))


class ImageRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_image(newurl):
            raise ValueError("图片跳转到未支持的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_extension(content):
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    if content[4:8] == b"ftyp" and content[8:12] in (b"avif", b"avis"):
        return ".avif"
    raise ValueError("响应不是受支持的图片。")


def image_bytes(url, source_file, download):
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        if source_file is None:
            return None, "缺少原始网页路径，无法定位相对图片。"
        root = source_file.parent.resolve()
        candidate = (root / unquote(parts.path)).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None, "网页的配套图片未保存。"
        if candidate.stat().st_size > MAX_IMAGE_BYTES:
            return None, "图片超过大小上限。"
        return candidate.read_bytes(), ""
    if not download or not public_image(url):
        return None, "保留来源链接。"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    with build_opener(ImageRedirects()).open(request, timeout=8) as response:
        content = response.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        return None, "图片超过大小上限。"
    return content, ""


def esc(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def gather_images(data, kind, prefix, output, source_file, download):
    rows = []
    seen = set()
    hashes = {}
    for item in data.get(f"{kind}_images", []):
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ref = f"{prefix}{len(rows) + 1:02}"
        row = {"id": ref, "url": url, "alt": item.get("alt") or "", "local_path": None}
        try:
            content, note = image_bytes(url, source_file, download)
            if content is not None:
                extension = image_extension(content)
                digest = hashlib.sha256(content).hexdigest()
                relative = hashes.get(digest) or f"图片/{kind}/{ref}{extension}"
                path = output / relative
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                hashes[digest] = relative
                row.update(local_path=relative, sha256=digest)
            elif note:
                row["note"] = note
        except (OSError, ValueError, URLError) as exc:
            row["note"] = f"素材保存失败（{type(exc).__name__}），保留来源。"
        rows.append(row)
    return rows


def render_evidence(product):
    listing = product["listing"]
    lines = ["# 商品页面证据", "", f"商品链接 {product['url']}", "",
             f"采集时间 {product['captured_at']}", "",
             f"采集方式 `{product['capture_mode']}`，页面状态 `{product['access_state']}`。", "",
             "| 证据编号 | 页面字段 | 页面值 |", "|---|---|---|"]
    for key, label in (("T01", "标题"), ("S01", "页面店铺"), ("P01", "页面展示价")):
        lines.append(f"| {key} | {label} | {esc(listing[key]) or '未采到'} |")
    for group in product["spec_groups"]:
        options = "、".join(opt["label"] + ("（当前不可选）" if opt["disabled"] else "")
                           + ("（当前选中）" if opt["selected"] else "") for opt in group["options"])
        lines.append(f"| {group['id']} | 规格选项 · {esc(group['name'])} | {esc(options)} |")
    for attribute in product["attributes"]:
        lines.append(f"| {attribute['id']} | 参数 · {esc(attribute['name'])} | {esc(attribute['value'])} |")
    lines.extend(["", "规格表保留页面展示的选项。具体组合、库存及成交价需要对应的页面证据。", "",
                  "## 主图和详情素材", "", "| 编号 | 区域 | 图片 |", "|---|---|---|"])
    for key, label in (("main_images", "主图"), ("detail_images", "详情")):
        for row in product[key]:
            link = f"[打开图片](<{row['local_path']}>)" if row["local_path"] else "来源地址见商品证据.json"
            lines.append(f"| {row['id']} | {label} | {link} |")
    if product["warnings"]:
        lines.extend(["", "## 采集备注", ""] + [f"- {warning}" for warning in product["warnings"]])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parsed", type=Path, help="JSON from extract_html.py")
    parser.add_argument("--url", required=True, help="Resolved product URL with item ID")
    parser.add_argument("--out", required=True, type=Path, help="A new result directory")
    parser.add_argument("--archive", type=Path, help="Original saved HTML, for local image references")
    parser.add_argument("--captured-at", help="Original capture timestamp for a replay")
    parser.add_argument("--download-images", action="store_true", help="Save public Taobao CDN images")
    args = parser.parse_args()

    data = json.loads(args.parsed.read_text(encoding="utf-8"))
    item_id, canonical = item_identity(args.url)
    if data.get("url"):
        source_id, _ = item_identity(data["url"])
        if source_id != item_id:
            parser.error("采集页商品 ID 与请求商品不一致。")
    state = data.get("access_state", "unknown")
    if state not in ("ok", "partial"):
        parser.error(f"页面采集结束于 {state}。请保留错误信息，待商品页可见时重试。")
    capture_mode = "saved_html_replay" if args.archive else "browser_snapshot"
    captured_at = args.captured_at or datetime.now().astimezone().isoformat(timespec="seconds")
    if args.archive and not args.captured_at:
        parser.error("旧网页回放需要原始采集时间 --captured-at。")
    datetime.fromisoformat(captured_at)
    if args.out.exists():
        parser.error("结果目录已存在。请为这次采集选择新目录。")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".product-teardown-", dir=args.out.parent))
    try:
        product = {
            "schema_version": 1, "item_id": item_id, "url": canonical,
            "captured_at": captured_at, "capture_mode": capture_mode, "access_state": state,
            "listing": {"T01": data.get("title", ""), "S01": data.get("shop", ""), "P01": data.get("price", "")},
            "spec_groups": [dict(group, id=f"V{i:02}") for i, group in enumerate(data.get("spec_groups", []), 1)],
            "attributes": [dict(attribute, id=f"A{i:02}") for i, attribute in enumerate(data.get("attributes", []), 1)],
            "main_images": gather_images(data, "main", "M", work, args.archive, args.download_images),
            "detail_images": gather_images(data, "detail", "D", work, args.archive, args.download_images),
            "page_text": {"id": "X01", "text": data.get("page_text", "")},
            "warnings": data.get("warnings", []),
        }
        if not product["listing"]["T01"]:
            parser.error("缺少商品标题，不能生成商品分析。")
        (work / "商品证据.json").write_text(json.dumps(product, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (work / "商品证据.md").write_text(render_evidence(product), encoding="utf-8")
        work.rename(args.out)
    finally:
        if work.exists():
            shutil.rmtree(work)
    counts = {k: len(product[k]) for k in ("main_images", "detail_images", "spec_groups", "attributes")}
    counts["local_images"] = sum(row["local_path"] is not None for key in ("main_images", "detail_images") for row in product[key])
    print(json.dumps({"output": str(args.out), "item_id": item_id, "capture_mode": capture_mode, **counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
