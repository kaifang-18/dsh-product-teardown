#!/usr/bin/env python3
"""Extract product evidence from saved or browser_inspect HTML. Standard library only.

No requests, browser control, script execution, cookies or embedded app-state reads.
Usage: python extract_html.py page.html --url 'https://detail.tmall.com/item.htm?id=...'
Scoped inner HTML: --scope main_images|detail_images|spec_groups|attributes.
Relative asset references are intentionally preserved for an upstream normalizer.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
IGNORE = set('script style noscript template svg canvas iframe input textarea'.split())
PII_CLASSES = ('userName', 'userInfo', 'avatar', 'site-nav', 'mini-cart',
               'delivery', 'address', 'receiver', 'consignee', 'login',
               'Comment', 'comments', 'askAnswer', 'recommend', 'recShop')


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: 'Node | None' = None
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has_class(self, prefix):
        return any(c == prefix or c.startswith(prefix + '--')
                   for c in self.attrs.get('class', '').split())

    def hidden(self):
        cur = self
        while cur:
            style = re.sub(r'\s+', '', cur.attrs.get('style', '').lower())
            if ('hidden' in cur.attrs or cur.attrs.get('aria-hidden') == 'true'
                    or 'display:none' in style or 'visibility:hidden' in style):
                return True
            cur = cur.parent
        return False


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.current = self.root
        self.saved_url = ''

    def handle_starttag(self, tag, attrs):
        # HTML's optional closing tags matter for legacy attribute lists.
        if tag == 'li' and self.current.tag == 'li':
            self.current = self.current.parent
        node = Node(tag, {k: v if v is not None else '' for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, text):
        self.current.children.append(text)

    def handle_comment(self, data):
        match = re.search(r'saved from url=\(\d+\)(https?://\S+)', data)
        if match:
            self.saved_url = match.group(1)


def clean(value, limit=2000):
    value = re.sub(r'\s+', ' ', value or '').strip()
    value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[电话已省略]', value)
    value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[邮箱已省略]', value)
    return value[:limit]


def node_text(node, safe=True):
    if node.tag in IGNORE or node.hidden():
        return ''
    if safe and any(c.lower() in node.attrs.get('class', '').lower()
                    or c.lower() in node.attrs.get('id', '').lower() for c in PII_CLASSES):
        return ''
    return ''.join(node_text(c, safe) if isinstance(c, Node) else c for c in node.children)


def gate_text_only(node):
    """Avoid mistaking a buyer's review about login/CAPTCHA for a page barrier."""
    if node.tag in IGNORE or node.hidden():
        return ''
    marker = (node.attrs.get('class', '') + ' ' + node.attrs.get('id', '')).lower()
    if any(x in marker for x in ('comment', 'askanswer', 'recommend', 'recshop',
                                 'site-nav', 'mini-cart', 'delivery', 'address')):
        return ''
    return ''.join(gate_text_only(c) if isinstance(c, Node) else c for c in node.children)


def first(nodes, classes=(), ids=()):
    return next((n for n in nodes if not n.hidden() and
                 (n.attrs.get('id') in ids or any(n.has_class(c) for c in classes))), None)


def label(node):
    return clean(node.attrs.get('title') or node_text(node)) if node else ''


def public_url(raw):
    """Product URL keeps only item id; never emit login/session query strings."""
    try:
        p = urlsplit(raw)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return ''
        host = p.hostname.lower()
        item = parse_qs(p.query).get('id', [''])[0]
        query = urlencode({'id': item}) if item.isdigit() else ''
        return urlunsplit((p.scheme, host, p.path, query, ''))
    except ValueError:
        return ''


def image_ref(node):
    # Lazy attributes take priority over placeholder src. Do not invent full-size URLs.
    for key in ('data-original', 'data-src', 'data-lazy-src', 'data-ks-lazyload', 'src'):
        ref = (node.attrs.get(key) or '').strip()
        if not ref or ref.lower().startswith(('data:', 'javascript:', 'blob:', 'about:')):
            continue
        if re.search(r'(?:transparent|spacer|loading|pixel)(?:[_.-]|\.)', ref, re.I):
            continue
        if re.search(r'(?:token|signature|auth|cookie|session)=', ref, re.I):
            continue
        return ref
    return ''


def flag(node, name):
    if name == 'disabled' and 'disabled' in node.attrs:
        return True
    keys = ('data-' + name, 'aria-' + name)
    if name == 'selected':
        keys += ('aria-checked', 'data-checked')
    if any(node.attrs.get(k, '').lower() in ('true', '1') for k in keys):
        return True
    tokens = [re.split(r'--', c)[0].lower() for c in node.attrs.get('class', '').split()]
    accepted = {'selected', 'isselected', 'valueselected', 'valueitemselected',
                'tb-selected', 'tb-selected-hasdata', 'isactive', 'active'} if name == 'selected' else {
                'disabled', 'isdisabled', 'valuedisabled', 'valueitemdisabled', 'tb-out-of-stock'}
    return any(t in accepted for t in tokens)


def extract_html(html, url='', scope='auto'):
    tree = Tree()
    tree.feed(html)
    nodes = list(tree.root.walk())
    warnings = []
    result = dict(url=public_url(url or tree.saved_url), title='', shop='', price='',
                  main_images=[], detail_images=[], spec_groups=[], attributes=[],
                  page_text='', access_state='unknown', warnings=warnings)

    title = first(nodes, ('mainTitle',), ('J_ItemTitle',))
    if not title:
        title = next((n for n in nodes if n.tag == 'h1' and not n.hidden()
                      and (n.attrs.get('data-title') or n.parent.has_class('tb-detail-hd')
                           or n.parent.has_class('tb-title'))), None)
    result['title'] = label(title)
    result['shop'] = label(first(nodes, ('shopName', 'tb-shop-name'), ('J_ShopName',)))
    price = first(nodes, ('highlightPrice',), ('J_PromoPrice', 'J_StrPrice', 'J_priceStd'))
    if price:
        result['price'] = clean(node_text(price), 500)

    gallery = first(nodes, ('picGallery',), ('picGalleryEle', 'J_UlThumb', 'J_ThumbView', 'J_ThumbView1'))
    detail = first(nodes, ('desc-root', 'descV8-container'), ('imageTextInfo-content', 'description', 'J_DivItemDesc'))
    sku = first(nodes, (), ('skuOptionsArea', 'J_isku', 'J_SKU'))
    if scope == 'main_images' and not gallery:
        gallery = tree.root
    if scope == 'detail_images' and not detail:
        detail = tree.root
    if scope == 'spec_groups' and not sku:
        sku = tree.root

    def images(root, kind):
        if not root:
            return []
        found, seen = [], set()
        all_nodes = list(root.walk())
        thumbs = any(n.has_class('thumbnailPic') for n in all_nodes)
        for node in all_nodes:
            if node.tag != 'img' or node.hidden():
                continue
            parents, p = [], node
            while p and p is not root:
                parents.append(p)
                p = p.parent
            if any(any(x in q.attrs.get('class', '').lower() for x in
                       ('avatar', 'recommend', 'recshop', 'logo', 'icon', 'comment')) for q in parents):
                continue
            if kind == 'main' and thumbs and not node.has_class('thumbnailPic'):
                continue
            if kind == 'main' and (node.has_class('videoPic') or 'video' in node.attrs.get('id', '').lower()):
                continue
            w, h = node.attrs.get('width', ''), node.attrs.get('height', '')
            if w.isdigit() and h.isdigit() and int(w) <= 40 and int(h) <= 40:
                continue
            ref = image_ref(node)
            if ref and ref not in seen:
                seen.add(ref)
                found.append({'url': ref, 'alt': clean(node.attrs.get('alt', ''), 500)})
        return found

    result['main_images'] = images(gallery, 'main')
    result['detail_images'] = images(detail, 'detail')

    if sku:
        groups = [n for n in sku.walk() if n.has_class('skuItem')]
        if not groups:
            groups = [n for n in sku.walk() if n.tag == 'dl' and
                      any(c.tag == 'ul' and (c.attrs.get('data-property') or c.has_class('J_TSaleProp'))
                          for c in n.walk())]
        for group in groups:
            members = list(group.walk())
            name_node = first(members, ('ItemLabel', 'tb-property-type'))
            name_node = name_node or next((n for n in members if n.tag == 'dt'), None)
            options = [n for n in members if n.has_class('valueItem')]
            if not options:
                options = [n for n in members if n.tag == 'li' and
                           ('data-value' in n.attrs or 'data-pv' in n.attrs)]
            parsed, seen = [], set()
            for option in options:
                if option.hidden():
                    continue
                text_node = first(list(option.walk()), ('valueItemText',))
                name = label(text_node) or clean(option.attrs.get('title') or node_text(option))
                if not name:
                    image = next((n for n in option.walk() if n.tag == 'img'), None)
                    name = clean(image.attrs.get('alt', '')) if image else ''
                if name and name not in seen:
                    seen.add(name)
                    parsed.append(dict(label=name, selected=flag(option, 'selected'), disabled=flag(option, 'disabled')))
            if parsed:
                name = label(name_node)
                if not name:
                    ul = next((n for n in members if n.attrs.get('data-property')), None)
                    name = clean(ul.attrs['data-property']) if ul else '未识别规格组'
                result['spec_groups'].append(dict(name=name, options=parsed))
        if result['spec_groups']:
            warnings.append('规格为当前HTML中的可见选项，不是实际SKU组合、库存或逐SKU价格；未生成笛卡尔积。')

    seen_attrs = set()
    def add_attr(name, value):
        name, value = clean(name, 200), clean(value)
        if name and value and (name, value) not in seen_attrs:
            seen_attrs.add((name, value))
            result['attributes'].append(dict(name=name, value=value))
    for row in nodes:
        members = list(row.walk()) if (row.has_class('generalParamsInfoItem') or row.has_class('emphasisParamsInfoItem')) else []
        if row.has_class('generalParamsInfoItem'):
            add_attr(label(first(members, ('generalParamsInfoItemTitle',))),
                     label(first(members, ('generalParamsInfoItemSubTitle',))))
        elif row.has_class('emphasisParamsInfoItem'):
            add_attr(label(first(members, ('emphasisParamsInfoItemSubTitle',))),
                     label(first(members, ('emphasisParamsInfoItemTitle',))))
        elif row.tag == 'li' and not row.hidden():
            cur, allowed = row.parent, scope == 'attributes'
            while cur:
                allowed = allowed or cur.attrs.get('id') in ('J_AttrUL', 'attributes') or cur.has_class('attributes-list')
                cur = cur.parent
            if allowed:
                text = label(row)
                pair = re.split(r'[:：]', text, maxsplit=1)
                if len(pair) == 2:
                    add_attr(*pair)

    # Only whitelisted product fields and description text enter page_text.
    # Never return body.innerText: account navigation, buyer names and shipping locations are excluded.
    parts = [result[k] for k in ('title', 'shop', 'price') if result[k]]
    parts += [x['name'] + '：' + x['value'] for x in result['attributes']]
    parts += [x['name'] + '：' + ' / '.join(o['label'] for o in x['options']) for x in result['spec_groups']]
    if detail:
        description_text = clean(node_text(detail), 12000)
        if description_text:
            parts.append(description_text)
    result['page_text'] = '\n'.join(parts)[:20000]

    # Scan text only for access classification. Do not put these text fragments in output.
    gate_text = clean(gate_text_only(tree.root), 100000)
    product = bool(result['title'] or gallery or detail or result['spec_groups'] or result['attributes'])
    captcha = bool(re.search(r'请(?:拖动|按住|完成).*?(?:滑块|验证)|安全验证|人机验证|访问验证|验证码错误', gate_text))
    denied = bool(re.search(r'访问被拒绝|访问受限|请求被拦截|Access Denied|请求过于频繁', gate_text, re.I))
    login = bool(re.search(r'请先登录|登录后(?:查看|可见)|扫码登录|账号密码登录|密码登录|请登录后', gate_text))
    unavailable = bool(re.search(r'宝贝不存在|商品不存在|商品已下架|宝贝已失效|商品已失效|页面不存在', gate_text))
    visible_password = any(n.tag == 'input' and n.attrs.get('type') == 'password' and not n.hidden() for n in nodes)
    if captcha:
        result['access_state'] = 'verification_required'
    elif denied:
        result['access_state'] = 'blocked'
    elif login and (not product or visible_password):
        result['access_state'] = 'login_required'
    elif unavailable:
        result['access_state'] = 'unavailable'
    elif product:
        result['access_state'] = 'ok' if (result['title'] and result['price'] and result['main_images'] and result['detail_images']) else 'partial'
        if login:
            result['access_state'] = 'partial'
            warnings.append('页面有登录提示；已提取商品局部信息，不代表受限区已读取。')
    if result['access_state'] in ('verification_required', 'blocked', 'login_required'):
        warnings.append('检测到访问门槛；仅保留已提供HTML中的内容，不绕过验证，也不表示整页采集成功。')
    if not result['title']:
        warnings.append('未在商品标题区域找到标题；不以站点标题或推荐商品替代。')
    if not result['main_images']:
        warnings.append('未取得商品主图区域图片；可能是局部HTML、未加载或页面结构变更。')
    if not result['detail_images']:
        warnings.append('未取得详情区域图片；不等于商品没有详情图。')
    if not result['spec_groups']:
        warnings.append('未识别规格选项；可能未加载、单规格商品或页面结构变更。')
    if result['price']:
        warnings.append('价格仅为页面展示文案，保留优惠/起价语义；不是全部SKU成交价。')
    if tree.saved_url:
        warnings.append('来源为历史保存HTML；不代表当前页面、价格或库存。')
    if any(not re.match(r'^https?://|^//', x['url']) for key in ('main_images', 'detail_images') for x in result[key]):
        warnings.append('存在本地相对素材引用，已原样保留；需与HTML同目录素材配套使用。')
    warnings.append('仅解析已提供HTML；不执行脚本、不触发懒加载、不读取iframe/账户状态。')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', default='-', help='HTML文件；-从标准输入读取')
    parser.add_argument('--url', default='', help='浏览器观察所得页面URL；无参数时尝试saved-from注释')
    parser.add_argument('--scope', choices=['auto', 'product', 'main_images', 'detail_images', 'spec_groups', 'attributes'], default='auto')
    parser.add_argument('--output', '-o', help='输出JSON文件；默认标准输出')
    args = parser.parse_args()
    html = sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8-sig', errors='replace')
    result = extract_html(html, url=args.url, scope=args.scope)
    data = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(data, encoding='utf-8')
    else:
        sys.stdout.write(data)


if __name__ == '__main__':
    main()
