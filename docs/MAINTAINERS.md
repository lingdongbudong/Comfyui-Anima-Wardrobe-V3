# 维护者指南（MAINTAINERS）

改代码前必读。核心只有一句话：**每一类数据都有"唯一真源"，改了真源必须跑导出链，
只改导出产物会被下一次导出冲掉。**

## 一、模块地图

| 模块 | 职责 | 真源属性 |
|---|---|---|
| `__init__.py` | 节点注册 + 面板 HTTP 路由（`/anima_v3/*`） | — |
| `nodes.py` | 四个节点的 INPUT_TYPES / 运行逻辑 | — |
| `anima_v3_rules.py` | 随机引擎与规则层：五级闸门（先收敛→抽→体检→回滚）、三层权重、自检 | 消费 pools |
| `anima_v3_nsfw.py` | NSFW 判定表（**自动生成，请勿手改**） | 产物 |
| `anima_v3_overrides.py` | 人工校准层：段下限 / 上抬·降级白名单（「文本判档」与「选项准入」分离） | 真源 |
| `anima_v3_link.py` | 字段联动层：体位配对 / 表情配套 / 场景倾向 三张表 | 真源 |
| `anima_v3_store.py` | 用户数据持久化（预设 / 收藏 / 自定义角色覆盖层 / 用户缩略图） | — |
| `anima_v3_pngmeta.py` | 把正文注入 PNG 元数据（extra_pnginfo + 专用 tEXt 块） | — |
| `js/anima_wardrobe_v3_studio.js` | 面板前端（角色库 / 场景搭配 / 权重编辑 / 免责弹窗） | — |
| `locales/zh/`、`locales/en/` | 节点显示名与控件的中文化 | — |

## 二、数据文件与真源链

| 文件 | 真源 | 导出工具 |
|---|---|---|
| `presets/anima_v3_pools.json` | 《ANIMA提示词生成器.html》（维护者本机，**不在仓库内**） | `node tools/export_v3_pools.mjs` |
| `presets/anima_v3_levels.json` | pools + overrides + nsfw 判定表 | `python tools/classify_v3_levels.py` |
| `presets/anima_v3_characters.json` | safebooru / danbooru 抓取 + 造型台基线 | `python tools/build_characters.py` |
| `presets/anima_v3_scenes.json` | `build_scenes.py` 内置配方表 | `python tools/build_scenes.py` |
| `tools/playable_roles.json` | danbooru 官方角色列表页 | `python tools/build_playable_roles.py` |
| `anima_v3_nsfw.py`（NSFW 判定层） | 造型台 `wardrobe_rules.py`（V2 插件，不在仓库内） | `python tools/sync_nsfw_tables.py` |

运行时用户数据（**不进版本库**，`.gitignore` 已排除）：
`presets/user_presets.json` · `presets/user_favorites.json` ·
`presets/user_characters.json` · `presets/user_thumbs/`。
缺失时插件自动按空模板重建。

## 三、改词库的标准流程

```bash
# 1. 改《ANIMA提示词生成器.html》里的词库常量（唯一真源）
# 2. 导出 + 派生档位
node tools/export_v3_pools.mjs            # HTML → pools.json（--check 只对拍）
python tools/classify_v3_levels.py        # 派生 levels.json
# 3. 回归
python tests/run_all.py                   # 全部 0 失败才算完成
```

⚠️ 只改 `anima_v3_pools.json` 不改 HTML → 下次导出被原样冲回。
⚠️ 改判定表走 V2 造型台 `wardrobe_rules.py` → `python tools/sync_nsfw_tables.py`
（该测试在 V2 未安装时自动跳过，不影响他人克隆跑基线）。

## 四、改节点控件的流程

1. `nodes.py` 改 INPUT_TYPES（新控件**追加到末尾**，老工作流 widgets_values 不错位）；
2. `js/` 面板同步（面板 key 用**节点原生控件名**）；
3. `tools/make_workflows.py` 的 `GEN_WIDGETS` 同步；
4. 老工作流迁移：写 `tools/fix_workflows_v30X.py`（先 `--dry`）；
5. 重跑 `python tools/make_workflows.py` 再把两份 json 拷进仓库 `workflows/`
   （开源版注意把 `OUT_DIR_FOR_TXT` 保持为 `output/AnimaV3_prompts` 相对路径）；
6. `python tests/run_all.py` + `node tests/av3_ui_smoke.mjs`。

## 五、测试基线

`python tests/run_all.py [轮数]`（默认 200 次/档抽样），15 项只增不减：

| 项 | 文件 | 依赖 |
|---|---|---|
| 词库零漂移 + 档位表一致性 | `check_v3_pools.py` | 无 |
| NSFW 判定表与造型台对拍 | `check_nsfw_parity.py` | V2 造型台（缺则自动 SKIP） |
| 档位闸门 + 自检基线 | `check_v3_gate.py` | 无 |
| 字段联动 | `check_v3_link.py` | 无 |
| 姿势段基线 | `check_v3_pose.py` | 无 |
| 三层权重语义 | `check_v3_weights.py` | 无 |
| 检查修复基线 | `check_v3_repair.py` | 无 |
| 场景搭配库 | `check_v3_scenes.py` | 无 |
| 选中角色语义 | `check_v3_roles.py` | 无 |
| 角色库内容 | `check_v3_chars.py` | V2 造型台仅影响其中一条交叉验证（缺则跳过） |
| 用户数据存储 | `check_v3_store.py` | 无 |
| 用户角色覆盖层 | `check_v3_userdata.py` | 无 |
| PNG 元数据注入 | `check_v3_pngmeta.py` | 无 |
| 前端面板冒烟（jsdom） | `av3_ui_smoke.mjs` | node（缺则自动 SKIP） |

夹具由 `tools/dump_ui_fixtures.py` 从真后端导出，`run_all.py` 会自动先跑它。

## 六、发布前检查单

- [ ] `python tests/run_all.py` 全部 0 失败
- [ ] `presets/user_*.json` 是空模板（不含个人收藏/预设）
- [ ] `tools/.cache_*` 不在包里
- [ ] `grep -rnE "ghp_|sk-|C:/Users" --include="*.py" --include="*.js" .` 无命中
- [ ] README / NOTICE 提到的文件都在仓库里
- [ ] 版本号三处一致：`__init__.py` `__version__` / README 徽章 / 版本历史表
