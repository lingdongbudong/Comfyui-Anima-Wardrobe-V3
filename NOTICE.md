# 来源声明与第三方知识产权

本文件是**声明**，不是许可证条款。许可证见 [LICENSE](LICENSE)（MIT）。

## 一、移植来源

本插件的**词库与生成逻辑**移植自脚本 **`ANIMA提示词生成器.html`**
（HTML 版提示词生成器，ANIMA3 模型专用浏览器单文件工具）。

> 该脚本**已授权开源 —— 作者：飞蓬**。

移植对应关系：

| 原脚本里的东西 | 在本仓库里 |
|---|---|
| 全部词库（sections / canon / appear / mutex_groups） | `presets/anima_v3_pools.json` |
| 五级 NSFW 档位表 | `presets/anima_v3_levels.json` + `anima_v3_nsfw.py` |
| 候选池档位收敛 + 体检回滚 | `anima_v3_rules.py` |
| 角色库 / 场景库 | `presets/anima_v3_characters.json` / `anima_v3_scenes.json` |

> ⚠️ **词库的唯一真源仍是那份 HTML**（不在本仓库内）：改词库要**同时**改 HTML 与
> JSON，只改 JSON 会在下次导出时被冲回。维护流程见 [docs/MAINTAINERS.md](docs/MAINTAINERS.md)。

## 二、角色缩略图

`presets/thumbs/` 下的角色缩略图（约 2300 张 webp）通过
`tools/fetch_character_thumbs.py` 抓取自 **safebooru.donmai.us**
（danbooru 的 SFW 镜像，同一套 API）。

- 图片**著作权归各原画师所有**，本仓库不主张任何权利；
- 此处使用**仅作角色图鉴的索引缩略展示**，尺寸压缩、不用于任何商业用途；
- 如您是某张图片的原作者并希望移除，请提 issue，我们会尽快处理；
- 不想要这批图片的用户可直接删除 `presets/thumbs/` 目录，
  插件功能不受影响（图鉴显示占位图），也可运行抓取脚本重建。

## 三、角色名与作品名

仓库内出现的角色名、作品名、角色标签等，**知识产权均归各自权利人所有**。
本项目使用它们**仅作提示词的检索与描述**，不主张任何相关权利，
也不代表与原权利人存在任何关联或获得其认可。

## 四、本仓库的原创部分

除上述移植与第三方素材外，本仓库的其余内容（五级闸门机制、三层权重体系、
节点封装、检查修复与分级审查节点、前端面板等）为原创实现，
按 [LICENSE](LICENSE)（MIT）授权。
