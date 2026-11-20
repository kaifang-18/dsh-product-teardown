# dsh-product-teardown

把一条淘宝 / 天猫商品链接，变成一份可复核的商品证据包和拆解报告。DeepSeek Harness（DSH）技能插件。

> 只读采集：只读取商品页公开可见区域，不登录、不绕过验证码，不读取账号、地址、订单等个人信息。

## 它做什么

输入一条商品链接，输出：

- `商品证据.json` / `商品证据.md`：标题、店铺、页面展示价、规格选项、商品参数、主图与详情图清单（含图片链接或本地副本、SHA-256）
- `图片/`：可选的公开 CDN 素材副本
- `商品拆解报告.md`：按证据拆解卖点、商家主张、页面结构、消费者观察、需求假设和候选关键词

拆解遵循「商品事实 / 商家主张 / 需求假设 / 观测搜索词」四分法：没采到的写未知，不把标题词当搜索需求，不编造搜索量、指数或转化率。

## 环境要求

- DSH 桌面端 `>= 0.2.1-rc.5, < 0.3.0`
- [browser-skill](https://github.com/Tencent/BrowserSkill) 插件（提供 `bsk_*` 浏览器工具）——实时采集需要
- Python 3.9+（脚本只用标准库）

没有可用浏览器时，可以用 Chrome 保存的完整网页回放（`--archive`）。

## 安装

### 方式一：DSH 界面（推荐）

1. 克隆本仓库到本地（私有仓库需要先接受邀请）：

   ```sh
   git clone https://github.com/kaifang-18/dsh-product-teardown.git
   ```

2. 打开 DSH → **Plugins / 插件** → **Add plugin / 添加插件**，选择克隆下来的目录。
3. 新开一个会话，确认技能 `product-teardown` 出现在技能列表。

### 方式二：让 DSH 助手安装

把本地仓库路径发给助手，说：

> 把 `<本地仓库绝对路径>` 作为 bundle 安装到当前 profile。

助手会调用 `plugin_manager install_bundle`。安装后同样需要新开会话验证。

卸载：在 Plugins 里移除 `dsh-product-teardown`，或让助手执行 `plugin_manager remove_bundle`。

## 使用

对 DSH 说：

> 用 product-teardown 拆解这个商品：`https://detail.tmall.com/item.htm?id=...`

它会打开浏览器采集、运行脚本、生成证据目录和报告。也可以直接处理已保存的网页：

```sh
python3 skills/product-teardown/scripts/collect.py \
  --url 'https://detail.tmall.com/item.htm?id=123456789' \
  --archive 'saved-page.html' \
  --captured-at '2026-11-20T10:00:00+08:00' \
  --out './out/2026-11-20-example'
```

实时采集时，把浏览器返回的 HTML 片段按范围传入：

```sh
python3 skills/product-teardown/scripts/collect.py \
  --url 'https://detail.tmall.com/item.htm?id=123456789' \
  --part 'product=购买区.html' \
  --part 'main_images=主图.html' \
  --part 'detail_images=详情.html' \
  --part 'spec_groups=规格.html' \
  --part 'attributes=参数.html' \
  --download-images \
  --out './out/2026-11-20-example'
```

`--part` 的范围：`auto`、`product`、`main_images`、`detail_images`、`spec_groups`、`attributes`。

## 目录结构

```text
.
├── cordis.patch.yml                  # 挂载 skill-filesystem 的 bundle 补丁
├── package.json
├── skills/product-teardown/
│   ├── SKILL.md                      # 技能主文件：采集与拆解流程
│   ├── references/
│   │   ├── analysis.md               # 证据分级、价格口径、关键词规则
│   │   ├── browser-capture.md        # BrowserSkill 采集方法
│   │   └── report-template.md        # 拆解报告模板
│   └── scripts/
│       ├── collect.py                # 入口：解析片段并生成证据目录
│       ├── extract_html.py           # 从 HTML 提取商品字段
│       └── build_dossier.py          # 生成商品证据.json/.md，可选下载图片
└── tests/                            # 离线冒烟测试与固定样例
```

## 已知限制

- 只支持淘宝 / 天猫的 HTTPS 商品详情页（`item.taobao.com`、`detail.tmall.com` 等），且链接必须带唯一商品 ID。
- 一次只处理一条商品链接。
- 不绕过登录、滑块或访问验证；遇到访问门槛时只保留已取得的局部证据，并报告缺口。
- 规格列表只是页面可见选项，不代表 SKU 组合、库存或逐规格成交价。
- 页面展示价保留「起价 / 券后 / 预估」等语义，不是订单实付价。
- 页面结构改版可能导致字段提取为空；此时按「未取得」处理，不猜。
- 主图和详情图只覆盖浏览器已加载、已被识别的区域。

## 隐私与合规边界

- 只读取执行当前拆解所需商品区域；账号区、登录信息、收货地址等不进入产物。
- 页面文本会过滤手机号与邮箱；图片只允许从受支持的公开 CDN 下载。
- 网页文字、图片和商品描述按研究材料处理；买家秀和原图不默认取得广告素材授权。
- 没有真实来源的搜索量、趋势、竞争度等指标一律留空，不估算、不填 0。

## 开发与测试

离线测试不访问网络：

```sh
python3 -m unittest discover -s tests -v
```

测试覆盖 HTML 字段提取，以及从样例网页生成完整证据目录。

## 协作与权限

本仓库为私有仓库。仓库所有者可以随时在 **Settings → Collaborators** 移除协作者：

- 移除后对方立即失去读写权限；
- 对方基于该私有仓库创建的 fork 会被 GitHub 一并删除；
- 但对方已经 clone 到本地的副本无法远程收回，已安装的插件副本也不会自动停用。

## License

[MIT](LICENSE)
