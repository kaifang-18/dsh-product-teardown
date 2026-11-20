# 淘宝与天猫浏览器采集方法

运行环境为 dsh 桌面端和能建立会话的 BrowserSkill。链接只用于当前商品的只读采集。浏览器插件按自身规则连接，未能读取时记录具体失败环节。

## 片段采集

1. `bsk_session(action=start)` 后 `bsk_page(action=navigate)`。`bsk_inspect(action=observe)` 核对实际 URL、标题与可见页面。
2. 查看购买区和主图。尽量读取能识别商品标题和价格的上层节点，再按需读取主图库。老页面常见结构为 `picGalleryEle`，新页面按实际观察确认。`bsk_inspect(action=html, session, tabId, ref, maxBytes)` 的 `ref` 使用当前 observe / snapshot 返回的 `@eN`。页面变化后重新观察。
3. 进入用户可见的“图文详情”，按 BrowserSkill 支持的滚动动作使懒加载区出现。用新鲜 ref 读取详情区域或其子片段。老样本详情容器为 `imageTextInfo-content`。按页面顺序传多个 `--part detail_images=文件`。
4. 规格选项从购买区的规格组读取，历史样本含 `skuOptionsArea`；参数区单独采集。遇截断先缩小区域，`truncated=false` 的响应才可交给 `collect.py`。详情、规格或参数缺失时，保留已实际取得的部分。
5. 对已确认且可读的页面，如果无法取得可靠 scoped ref，可以用足以包含目标区域的 HTML 响应。整页响应只作为本次处理的临时文件，结果保留脚本抽出的商品区域。关闭浏览器会话前核对商品 ID 和抓取范围。

可选范围参数：`product` 读取标题、店铺、价格，`main_images` 读主图，`detail_images` 读详情，`spec_groups` 读规格选项，`attributes` 读参数，`auto` 用于整页。多个片段按页面顺序传入；如果买区和参数区域已包含在完整片段中，可以简化为一个 `--part auto=文件`。

## 历史网页回放

```sh
python3 <技能目录>/scripts/collect.py --url '<已知商品URL>' \
  --archive '<保存的网页.html>' --captured-at '2026-09-28T10:41:16+08:00' \
  --out '<新结果目录>'
```

回放会从网页旁的配套素材目录复制相对引用图片。浏览器实时模式可加 `--download-images` 保存支持的淘宝公开 CDN 图片；未保存的图片在证据文件中带链接和状态。主图库可能提供缩略图，应标注实际版本。
