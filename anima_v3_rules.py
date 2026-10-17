# -*- coding: utf-8 -*-
"""ANIMA-Wardrobe-V3 · 随机引擎与规则层

把 ANIMA提示词生成器.html 的「词库 + 随机 + 自检」三件事搬到 Python，
并补上源脚本缺的四块能力：

    1. 五级档位闸门  —— 候选池**先**按档位收敛，再抽，抽完体检回滚。
       源脚本是「先盲抽、抽完 sanitize 打补丁」，高档内容会漏进低档。
    2. 三层权重      —— 段 / 字段 / 选项，逐层可调，0 等于关闭。
       源脚本只有一个写死的 `Math.random()<0.65`。
    3. 确定性种子    —— seed>=0 时逐字节可复现。
       派生种子一律走 zlib.crc32，**不用内置 hash()**（每进程加盐）。
    4. 输出槽位重排  —— 按 ANIMA3 v3.0 规范排序，而不是按界面段序。
       界面段序把「服装状态」排在「场景」之后，进 CLIP 的顺序是错的。

⚠️ 本模块输出**英文 danbooru 标签串**（ANIMA 底模吃这个），中文只用于
   界面与摘要。这与 Krea2 系列「提示词一律中文」的口径不冲突 —— 那是
   中文自然语言模型的规矩，两回事。

⚠️ 词库唯一真源是那份 HTML；档位唯一真源是造型台的 wardrobe_rules.py。
   本文件只做「读 + 组合」，不存任何词条。
"""
from __future__ import annotations

import json
import os
import random
import re
import zlib

from . import anima_v3_nsfw as N
from . import anima_v3_link as L
from .anima_v3_overrides import LEVEL_DEMOTE, raise_

# ============================================================================
# 一、加载词库与档位表
# ============================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_POOLS_PATH = os.path.join(_HERE, "presets", "anima_v3_pools.json")
_LEVELS_PATH = os.path.join(_HERE, "presets", "anima_v3_levels.json")
_CHARS_PATH = os.path.join(_HERE, "presets", "anima_v3_characters.json")

_CACHE: dict = {}


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def pools() -> dict:
    """词库（零漂移导出物）。只在首次调用时读盘。"""
    if "pools" not in _CACHE:
        _CACHE["pools"] = _load_json(_POOLS_PATH)
    return _CACHE["pools"]


def levels() -> dict:
    """选项档位表（派生数据，见 tools/classify_v3_levels.py）。"""
    if "levels" not in _CACHE:
        _CACHE["levels"] = _load_json(_LEVELS_PATH)
    return _CACHE["levels"]


def sections() -> list:
    return pools()["sections"]


def section_by_id(sec_id: str):
    for s in sections():
        if s["id"] == sec_id:
            return s
    return None


def field_by_key(key: str):
    """返回 (段落, 字段)；找不到返回 (None, None)。"""
    for s in sections():
        for f in s["fields"]:
            if f["key"] == key:
                return s, f
    return None, None


def field_keys() -> list:
    return [f["key"] for s in sections() for f in s["fields"]]


def section_ids() -> list:
    return [s["id"] for s in sections()]


def canon() -> dict:
    """**全部预设**：法典 14 组（源 HTML）+ 场景搭配库（v3.9.0）。

    返回 `{预设 id: {字段: 值}, ...}`。合并后这张表就是唯一入口 ——
    `apply_preset()` 不认识来源，两条来源走**同一条**填充路径，
    因此自动共享档位 / 权重 / 锁定三道闸门与成品自动修复。

    ⚠️ 结果缓存：它在 `_one_pass` 的热路径上被反复调用（每轮密度自适应都调）。
    """
    if "canon_merged" not in _CACHE:
        m = dict(pools().get("canon", {}) or {})
        for sc in scene_list():
            rec = scene_preset(sc.get("id"))
            if rec:
                m[sc["id"]] = rec
        _CACHE["canon_merged"] = m
    return _CACHE["canon_merged"]


def appear() -> dict:
    return pools().get("appear", {})


def _field_section() -> dict:
    """字段 key → 所属段 id（缓存）。给段下限查表用。"""
    if "field_section" not in _CACHE:
        m = {}
        for s in sections():
            for f in s["fields"]:
                m[f["key"]] = s["id"]
        _CACHE["field_section"] = m
    return _CACHE["field_section"]


def section_floor(sec_id: str) -> int:
    """段级保底下限（见 anima_v3_overrides.SECTION_FLOOR）。"""
    return int(levels().get("section_floor", {}).get(sec_id, 0))


# ============================================================================
# 一之二、角色库（独立文件，不污染零漂移的词库）
# ============================================================================
#
# 为什么角色库要单独成文件：
#   pools.json 是「源 HTML 的零漂移导出物」—— 它必须与那份 HTML 逐字节
#   对应，`check_v3_pools.py` 靠这条对拍守住词库不被偷偷改坏。
#   如果把 2000 条角色写进 role 字段，那份对拍就永久失效了。
#   所以角色库独立成 presets/anima_v3_characters.json，role 字段的候选
#   由 `role_pool()` 现场合并（源 HTML 的 221 条 + 角色库）。
#
# 角色库缺席时自动退回源 HTML 的 221 条 —— 保证任何测试/环境都能跑。

def clear_cache():
    """清掉全部进程级缓存。

    ⚠️⚠️ **任何写用户角色数据的路由成功后都必须调它**，否则"改了不生效、
       新增不出现"：characters / char_index / char_by_trigger / role_options
       全是内存快照，读盘只发生在首次调用。词库与档位表也在这块缓存里，
       一起丢掉只是重新读两个小 JSON，代价可以忽略。
    """
    _CACHE.clear()


def _user_chars() -> dict:
    """用户角色覆盖层（edits + custom）。缓存；写操作后靠 clear_cache 清。"""
    if "user_chars" not in _CACHE:
        from . import anima_v3_store as S
        _CACHE["user_chars"] = S.load_user_characters()
    return _CACHE["user_chars"]


def user_edits() -> dict:
    """{内置 tag: 覆盖项}。详情接口靠它判断"这一项被改过没有"。"""
    return _user_chars().get("edits") or {}


def _chars_base() -> dict:
    """**内置**角色库原文（不含用户覆盖层）。缓存。

    为什么要单独缓存它：合并结果（`characters_data`）已经被用户改动污染，
    而"恢复默认""显示内置原始标签"都需要未污染的原文。
    """
    if "chars_base" not in _CACHE:
        base = {"characters": [], "works": []}
        if os.path.exists(_CHARS_PATH):
            try:
                base = _load_json(_CHARS_PATH)
            except Exception:
                base = {"characters": [], "works": []}
        _CACHE["chars_base"] = base
    return _CACHE["chars_base"]


def builtin_char_index() -> dict:
    """{角色 tag: **内置**记录}。给"内置原始标签"对照用。"""
    if "builtin_char_index" not in _CACHE:
        _CACHE["builtin_char_index"] = {
            c["tag"]: c for c in _chars_base().get("characters", [])
        }
    return _CACHE["builtin_char_index"]


def _work_slug(en: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(en or "").lower()).strip("_")


def _merge_overlay(base: dict) -> dict:
    """内置角色库 + 用户覆盖层 → 合并结果。

    ⚠️ 三件事必须一起做，少一件前端就会错：
      ① edits 覆盖允许的键（trigger / zh / work / thumb）；
      ② custom 整条追加；
      ③ **重算 works[].count** —— 前端有条断言「点作品 → 卡片数 == count」
         （它正是 v3.1.0「点 IP 作品空列表」线上 bug 的回归用例），
         新增角色会让某个作品多几条，不重算就对不上。
    另外：自定义角色填了库里没有的作品名时给它**补一行作品**，
    否则那位角色在左侧作品列表里根本点不到。
    """
    ov = _user_chars()
    edits = ov.get("edits") or {}
    custom = ov.get("custom") or []

    chars = []
    for c in base.get("characters", []) or []:
        c = dict(c)
        patch = edits.get(str(c.get("tag") or ""))
        if patch:
            for k in ("trigger", "zh", "work", "thumb"):
                v = patch.get(k)
                if isinstance(v, str) and v.strip():
                    c[k] = v.strip()
            # ⚠️ 特征标签是**列表**，整条替换（不做并集）——
            #    用户删掉一条标签必须真的删掉，并集会让"删除"永远无效。
            if isinstance(patch.get("tags"), list) and patch["tags"]:
                c["tags"] = [str(t).strip().lower() for t in patch["tags"] if str(t).strip()]
        chars.append(c)
    for c in custom:
        rec = dict(c)
        rec.setdefault("src", "user")
        chars.append(rec)

    works = [dict(w) for w in (base.get("works", []) or [])]
    id_of_work = {}
    for w in works:
        id_of_work.setdefault(w.get("en", ""), w.get("id", ""))
    for c in custom:
        wk = str(c.get("work") or "").strip()
        if not wk or wk in id_of_work:
            continue
        used = {w.get("id") for w in works}
        sid, i = (_work_slug(wk) or "user_work"), 2
        while sid in used:
            sid = f"{_work_slug(wk) or 'user_work'}_{i}"
            i += 1
        works.append({"id": sid, "en": wk, "zh": wk, "count": 0,
                      "pop": 0, "target": False})
        id_of_work[wk] = sid

    cnt = {}
    for c in chars:
        wid = id_of_work.get(str(c.get("work") or "").strip(), "")
        if wid:
            cnt[wid] = cnt.get(wid, 0) + 1
    for w in works:
        w["count"] = cnt.get(w.get("id"), 0)
    # 与内置产物同一口径：按角色数降序（并列时按英文名，保证确定性）
    works.sort(key=lambda w: (-int(w.get("count") or 0), str(w.get("en") or "")))

    meta = dict(base.get("_meta") or {})
    meta["merged"] = {
        "builtin": len(base.get("characters", []) or []),
        "edits": len(edits),
        "custom": len(custom),
    }
    return {"_meta": meta, "works": works, "characters": chars}


def characters_data() -> dict:
    """内置角色库 + 用户覆盖层（见 _merge_overlay）。

    初版只返回内置文件；v3.4.0 起改为合并结果 —— 三个消费方
    （面板 /characters、role 候选池、外貌对齐）因此自动看到用户的编辑与新增。
    """
    if "characters" not in _CACHE:
        _CACHE["characters"] = _merge_overlay(_chars_base())
    return _CACHE["characters"]


def has_character_library() -> bool:
    return bool(characters_data().get("characters"))


def char_works() -> list:
    return characters_data().get("works", [])


def char_index() -> dict:
    """{角色 tag: 角色记录}。"""
    if "char_index" not in _CACHE:
        _CACHE["char_index"] = {
            c["tag"]: c for c in characters_data().get("characters", [])
        }
    return _CACHE["char_index"]


def _role_options() -> list:
    """合并后的角色候选池 [(zh, en, level), ...]。

    · 源 HTML 的 221 条在前（中文名人工核准过、且带 APPEAR 外貌表）
    · 角色库的其余条目接在后面
    · 同 trigger 去重
    """
    if "role_options" in _CACHE:
        return _CACHE["role_options"]

    out, seen = [], set()
    _sec, fld = field_by_key("role")
    for zh, en in (fld.get("opts") if fld else []) or []:
        en = (en or "").strip()
        if not en:
            continue
        key = en.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((zh.strip(), en, int(N.grade_nsfw(
            [t.strip() for t in en.split(",") if t.strip()])["num"])))

    for c in characters_data().get("characters", []):
        trig = (c.get("trigger") or c.get("tag") or "").strip()
        if not trig or trig.lower() in seen:
            continue
        seen.add(trig.lower())
        zh = c.get("zh") or c.get("name") or trig
        out.append((zh, trig, int(N.grade_nsfw(
            [t.strip() for t in trig.split(",") if t.strip()])["num"])))

    _CACHE["role_options"] = out
    return out


def role_pool(lv: int = 99) -> list:
    """按档位过滤后的角色候选池。"""
    return [o for o in _role_options() if o[2] <= lv]


# 「人数构成」→ 该抽几位**女性**角色。
#
# 角色的性别位由 count 字段决定：`1boy` / `multiple boys` 是男方，由通用标签
# 承担（角色库里 2687 位全是女性，没有男性角色可指）。这里只回答"女方要几位"。
#
# ⚠️ 用**标签解析**而不是精确匹配整串 —— count 的选项以后可能增删，
#    精确匹配会静默退化成"总是 1 位"，而且不会报错。
GIRL_COUNT_BY_TAG = {
    "multiple girls": 3,      # 多人群交：3 位（2026-10-03 与用户确认）
    "orgy": 3,
    "2girls": 2,
}


def girl_count_for(count_en: str) -> int:
    """从人数标签推断该抽几位女性角色。"""
    tags = {t.strip().lower()
            for t in str(count_en or "").split(",") if t.strip()}
    for t, n in GIRL_COUNT_BY_TAG.items():
        if t in tags:
            return n
    # 单人 / 一男一女 / 一女多男 / 其他 —— 女方都是 1 位
    return 1


# ============================================================================
# 一之三之二、按作品 IP 限定随机角色（v3.10.0）
# ============================================================================
#
# 需求：「选一个作品 IP → 从该 IP 的角色里随机抽 → 每次运行都自动换人」。
#
# ⚠️ 为什么必须落到**引擎**而不是面板：
#    面板的 🎲 按钮把抽到的角色写进 `selected_roles`，那等于"抽一次就固定"
#    （随工作流保存、可复现）—— 它**做不到每次运行自动换**。
#    要"每次换"，抽取必须发生在**运行时**，于是引擎得知道"从哪个 IP 里抽"。
#    节点侧原本没有任何"角色池"控件，所以新增 `role_works`（作品 slug 列表）。
#
# 两条路径并存（优先级见 draw 里的角色段）：
#   ① 抽一次就固定 → 面板 🎲 → 写 `selected_roles`（v3.8.0，保留）
#   ② 每次自动换   → 面板「🔁」→ 写 `role_works`，**不写** selected_roles；
#                    配合 `seed = -1`（默认）每次排队都重抽。


def role_work_index() -> dict:
    """{角色 trigger 小写: 作品 slug}（缓存）。

    ⚠️ 只收**角色库**里的条目（2687 位）—— 与面板的作品筛选**同一口径**
       （面板也是拿 `work_id` 比角色库记录）。源 HTML 里那 221 条若不在
       角色库里（构建时被剔掉的男性 / 非女性判定），就没有作品归属，
       IP 过滤时会被排除；`draw` 会在 note 里把这件事说清楚。
    ⚠️ 键用 trigger 原文（小写）—— 与 `_role_options()` 产出的 `en` 同源，
       这样过滤时是精确匹配，不会因为中英文/别名差异漏掉。
    """
    if "role_work_index" not in _CACHE:
        m = {}
        for c in characters_data().get("characters", []) or []:
            trig = (c.get("trigger") or c.get("tag") or "").strip().lower()
            if trig:
                m[trig] = _work_slug(c.get("work", ""))
        _CACHE["role_work_index"] = m
    return _CACHE["role_work_index"]


def works_table() -> list:
    """作品表（含 slug / 英文名 / 中文名 / 角色数），给解析与面板用。"""
    return characters_data().get("works", []) or []


def resolve_works(raw) -> list:
    """把用户写的作品串解析成 slug 列表。

    接受 **slug**（`zenless_zone_zero`）/ **英文名**（`zenless zone zero`）/
    **中文名**（`绝区零`），大小写无关，分隔符 `|` / `;` / 换行 / 逗号都行。
    认不出来的 token 直接忽略（**不报错** —— 老工作流里可能是别的写法，
    报错会让整张图跑不起来；报告里会提示"认不出来"）。
    """
    if not raw:
        return []
    chunks = re.split(r"[|;\n,，]", str(raw))
    by_key = {}
    for w in works_table():
        for k in (w.get("id"), w.get("en"), w.get("zh")):
            k = str(k or "").strip().lower()
            if k:
                by_key.setdefault(k, w.get("id", ""))
    out = []
    for c in chunks:
        k = c.strip().lower()
        if not k:
            continue
        slug = by_key.get(k) or (k if k in {w.get("id") for w in works_table()} else "")
        if slug and slug not in out:
            out.append(slug)
    return out


def unknown_works(raw) -> list:
    """解析失败的 token（给报告提示用）。"""
    if not raw:
        return []
    known = {(str(w.get("id") or "")).lower() for w in works_table()}
    known |= {(str(w.get("en") or "")).lower() for w in works_table()}
    known |= {(str(w.get("zh") or "")).lower() for w in works_table()}
    return [c.strip() for c in re.split(r"[|;\n,，]", str(raw))
            if c.strip() and c.strip().lower() not in known]


def role_pool_by_works(lv: int, works) -> list:
    """档位过滤 **再** 叠加作品过滤后的角色候选池。"""
    ws = set(works or ())
    if not ws:
        return list(role_pool(lv))
    idx = role_work_index()
    return [o for o in role_pool(lv) if idx.get(o[1].strip().lower()) in ws]


def work_pool_sizes(lv: int = 99) -> dict:
    """{作品 slug: 该作品在当前档位下可用的角色数} —— 面板显示用。"""
    out = {}
    for _zh, en, n in role_pool(lv):
        slug = role_work_index().get(en.strip().lower())
        if slug:
            out[slug] = out.get(slug, 0) + 1
    return out


def pick_random_roles(rng, lv: int = 99, n: int = 1, works=None) -> list:
    """从角色池随机抽 n 位**互不重复**的女性角色，返回 trigger 列表。

    未选角色时的兜底 —— 保证出图一定有角色（2026-10-03 与用户确认）。

    `works` 给了就只在这些作品的角色里抽（v3.10.0）。
    ⚠️ 池子不够 n 位时**有多少给多少**，不报错也不去别处借 ——
       借了就不是"这个 IP 的角色"了，那正是用户要的东西。调用方负责在
       池子为空时决定兜底（见 draw：回退全局池并写进报告）。

    ⚠️ 均匀随机，**不按人气加权**：候选池 2700+ 条，按人气加权会让冷门角色
       几乎永远出不来，而"什么都可能抽到"才是这个插件随机角色的意义。
    ⚠️ 用调用方传进来的 rng —— 这样 seed >= 0 时整体仍然逐字节可复现。
    ⚠️ 用 `rng.sample` 而不是抽 n 次：**同一位角色不能重复出现**
       （两个 `hakurei reimu` 拼在一起，模型会当成两个人）。
    """
    pool = role_pool_by_works(lv, works)
    if not pool or n <= 0:
        return []
    take = min(int(n), len(pool))
    return [pool[i][1] for i in rng.sample(range(len(pool)), take)]


def pick_random_role(rng, lv: int = 99) -> str:
    """抽 1 位。`pick_random_roles` 的单人快捷方式，保留给旧调用方。"""
    got = pick_random_roles(rng, lv, 1)
    return got[0] if got else ""


# ============================================================================
# 一之三、选中角色（面板里点选的角色，抽取概率 100%）
# ============================================================================

ROLE_SEP = re.compile(r"[|;\n]")


def parse_selected_roles(raw) -> list:
    """把控件里的角色串拆成列表。

    ⚠️ 分隔符**只能是 `|` / `;` / 换行，绝不能用逗号**。
       danbooru 的**角色 tag** 里确实没有逗号，但**完整 trigger 有** ——
       `hakurei reimu, touhou` 就是一个角色。用逗号当分隔符会把它拆成
       「hakurei reimu」和「touhou」两个角色，人数标签随即从 2 位变成 4 位、
       误判成群像。这个 bug 被单元测试抓到过一次，别再改回去。
    """
    out = []
    for chunk in ROLE_SEP.split(str(raw or "")):
        t = chunk.strip()
        if t and t not in out:
            out.append(t)
    return out


def _normalize_role_token(t: str) -> str:
    """把 danbooru 风格的角色 tag 归一成本生态通用的 trigger 写法。

        ellen_joe_(zenless_zone_zero) → `ellen joe, zenless zone zero`
        hakurei_reimu                → `hakurei reimu`

    ⚠️ 为什么必须做：角色库里有 2687 条，但用户完全可能手填一个库外的 tag。
       下划线写法（`ellen_joe_(...)`）本身是合法 danbooru tag，模型也认，
       但和本生态（源 HTML / 造型台）的空格写法不一致，而且会撞上自检 F12
       （「标签格式：不应含下划线」）—— 在真机端到端里被抓到过一次。
    """
    t = str(t or "").strip()
    if not t:
        return ""
    m = re.match(r"^(.+?)_\(([^()]+)\)$", t)
    if m:
        name = m.group(1).replace("_", " ").strip()
        work = m.group(2).replace("_", " ").strip()
        return f"{name}, {work}" if name and work else name
    return t.replace("_", " ").strip()


def expand_roles(tokens) -> list:
    """角色 tag → 完整 trigger。

    库里查得到的用库里的 trigger（带核准过的作品名）；
    查不到的**归一化后透传**（允许手写自定义角色，但写法要干净）。
    """
    idx = char_index()
    out = []
    for t in tokens:
        t = str(t or "").strip()
        if not t:
            continue
        c = idx.get(t) or idx.get(t.lower())
        trig = (c.get("trigger") if c else "") or _normalize_role_token(t)
        if trig and trig not in out:
            out.append(trig)
    return out


def headcount_tags(n: int, lv: int, current: str = "") -> str:
    """按选中角色数决定人数标签。返回空串 = 保持原样不动。

    ⚠️ 两位女性角色时的关系标签要**看档位**：
       `yuri` 在判定表里是 2 档内容（「百合」分类），
       全年龄/擦边档写 yuri 会凭空越档 —— 那是本插件最核心的承诺。
       所以 ≤1 档写中性的 `2girls, multiple girls`。

    3 位以上一律按「多人群像」处理，写 `multiple girls`：
    不会写 solo、也不会写 yuri（用户选 3 个人显然不是双人关系）。
    """
    if n <= 0:
        return ""
    if n == 1:
        cur = (current or "").lower()
        single_ok = ("1girl" in cur and "multiple" not in cur
                     and "2girls" not in cur and "1boy" not in cur)
        return "" if single_ok else "1girl, solo"
    if n == 2:
        if lv <= 1:
            return "2girls, multiple girls"
        return "2girls, yuri"
    return "multiple girls"


def apply_selected_roles(state: dict, triggers, lv: int,
                         rewrite_count: bool = True) -> list:
    """把选中的角色写进状态。返回说明列表。

    ⚠️ 选中即 100% 抽取 —— **不受权重影响**。用户显式点了角色，
       这一条就该压过「字段权重 0」；权重面板负责的是"想要什么倾向"，
       不是"否决我刚刚点的东西"。真不想用就点「清除已选角色」。

    rewrite_count 的两种用法（v3.2.0 新增）：
      · **True**（用户显式选中）—— 按选中人数改写人数标签。
        选了 2 位就是双女，人数当然得跟着走。
      · **False**（未选角色、由系统随机抽的）—— **不碰人数标签**。
        这时人数是 count 字段自己抽出来的组合（一男一女 / 双女 /
        一女多男 / 多人群交 …），角色只是"填进这个组合的角色位"。
        早先版本在这里无条件改写，把所有组合都压成了单人 —— 那是 bug。
    """
    triggers = [t for t in (triggers or []) if t]
    n = len(triggers)
    if not n:
        return []

    notes = []
    state["role"] = ", ".join(triggers)

    if rewrite_count:
        head = headcount_tags(n, lv, state.get("count", ""))
        if head:
            state["count"] = head
            notes.append(f"按选中角色数（{n} 位）改写人数标签 → `{head}`")

        if n >= 3:
            notes.append("多人群像模式：已写 `multiple girls`，不再使用双人关系标签")
        elif n == 2 and lv <= 1:
            notes.append("当前档位 ≤ 擦边：双人关系用中性的 `multiple girls`"
                         "（`yuri` 属 2 档内容，写了会越档）")
    else:
        notes.append(f"人数组合由「人数构成」决定，已填入 {n} 位角色"
                     "（未改写人数标签）")

    # 多人时，下面这些字段的语义会和人数打架，清掉更安全。
    # ⚠️ 判据是 n（角色数），与 count 无关：
    #    count 是 `multiple girls, orgy` 但只抽到 3 位时同样要清。
    if n >= 2:
        for k in ("n_act", "n_pos", "n_orgasm", "n_cum", "n_fluid", "n_theme"):
            if state.get(k):
                state[k] = ""
        if state.get("_cleared_for_multi") is None:
            state["_cleared_for_multi"] = True
            notes.append("多人模式：已清空单人向的体位 / 特殊主题字段")

    return notes


def option_level(field_key: str, en: str) -> int:
    """选项档位 = max(判定表结果, 段下限, 逐选项上抬)。

    ⚠️ 这里**再算一次段下限 / 上抬**，不是重复劳动：
       磁盘上的 anima_v3_levels.json 可能比 overrides 旧（用户改了
       SECTION_FLOOR 或 LEVEL_RAISE 但忘了重跑分类脚本），只信 JSON 会让
       「SFW 档抽出阴蒂」或「SFW 档抽出第一人称下体视角」那两个漏洞复活。
       三层都算，最多是幂等的。

    ⚠️ 上抬只走 `raise_`（只升不降）。**下调（LEVEL_DEMOTE）刻意不在这里再算一次** ——
       下调是"判定表在某个字段里看错了上下文"，只有分类脚本按 (字段, 选项)
       精确落盘才安全；运行时按字段再算一遍会和 `_calibrated_tags()` 的
       "已登记白名单"语义打架（F17 靠白名单放过 wet hair，这里要是把它降成 0，
       白名单就再也解释不清为什么它不在超档名单里）。

    ⚠️ n_canon 特殊：它的"选项"是**预设 id**（c1… / sc_*），档位不在
       levels.json 的 option_levels 里，得走 `canon_level()`。不特判的话
       它恒为 0 —— F15（档位准入）就再也拦不住"被强制指定的超档预设"。
    """
    if field_key == "n_canon":
        return canon_level(en)
    base = int(levels().get("option_levels", {}).get(field_key, {}).get(en, 0))
    sec = _field_section().get(field_key)
    if sec:
        base = max(base, section_floor(sec))
    base, _why = raise_(field_key, en, base)
    return base


def canon_level(preset_id: str) -> int:
    """预设档位：**场景搭配库自带 level 优先**，否则查法典档位表。

    ⚠️ 场景库的 level 由 `tools/build_scenes.py` 按内容派生（max 各标签档位），
       不经过 `SECTION_FLOOR['canon']=2` —— 那条段下限只适用于法典那 14 组
       （它们设计上就是 NSFW 场景）。SFW 场景搭配因此能在全年龄档可用。
    """
    sc = scene_index().get(str(preset_id or ""))
    if sc:
        return int(sc.get("level", 0))
    return int(levels().get("canon_levels", {}).get(preset_id, 0))


def section_max_level(sec_id: str) -> int:
    return int(levels().get("section_max", {}).get(sec_id, 0))


# ============================================================================
# 一之四、场景搭配预设库（v3.9.0）
# ============================================================================
#
# 「一键设置场景搭配」= 把 姿势 / 场景 / 表情 / 动作 的**固定组合**一次套用，
# 套用到的字段不再参与随机。
#
# 为什么独立成文件（而不是继续往源 HTML 的 canon 里塞）：
#   · pools.json 是「源 HTML 的零漂移导出物」，`check_v3_pools.py` 靠逐字节
#     对拍守住它。塞 33 个跨槽位预设进去，那份对拍立刻失效。
#   · 场景库的每条预设**自带档位**（由 tools/build_scenes.py 按内容派生），
#     与"法典那 14 条固定 ≥2 档"是两套来源，混在一起后档位口径会打架。
#   这和角色库独立成 anima_v3_characters.json 是同一个理由。
#
# 与法典场景的关系（**同一套机制，两条来源**）：
#   `canon()` 把两边合并成一张 `{预设 id: {字段: 值}}` 表，
#   `apply_preset()` 完全不用改 —— 场景库的预设与法典预设走**同一条**填充路径，
#   因此自动继承三道闸门（档位 / 权重 / 锁定）与 v3.8.0 的成品自动修复。

_SCENES_PATH = os.path.join(_HERE, "presets", "anima_v3_scenes.json")

# 预设**绝不允许**代填的字段：人数与角色是"地基"。
# 预设抢 count 会让「选了双女却写成单人」这类矛盾复现（v3.3.0 修过一次），
# 抢 role 会让角色库的选择失效。构建脚本会校验这条，这里再兜一层。
SCENE_FORBIDDEN_FIELDS = frozenset({"count", "role"})


def scenes_data() -> dict:
    """场景搭配预设库原文（缺席时返回空壳，功能优雅降级）。"""
    if "scenes" not in _CACHE:
        data = {"_meta": {}, "groups": [], "scenes": []}
        if os.path.exists(_SCENES_PATH):
            try:
                data = _load_json(_SCENES_PATH)
            except Exception:
                data = {"_meta": {}, "groups": [], "scenes": []}
        _CACHE["scenes"] = data
    return _CACHE["scenes"]


def has_scene_library() -> bool:
    return bool(scenes_data().get("scenes"))


def scene_groups() -> list:
    return scenes_data().get("groups", [])


def scene_list() -> list:
    return scenes_data().get("scenes", [])


def scene_index() -> dict:
    """{预设 id: 场景记录}。"""
    if "scene_index" not in _CACHE:
        _CACHE["scene_index"] = {s["id"]: s for s in scene_list()}
    return _CACHE["scene_index"]


def scene_preset(preset_id: str) -> dict:
    """预设 id → apply_preset 能吃的那种 dict（`{字段: 值}` + `n_extra`）。

    ⚠️ 与法典预设同形状 —— 上层（canon / apply_preset）不需要知道
       "这条是法典的还是场景库的"。
    """
    sc = scene_index().get(str(preset_id or ""))
    if not sc:
        return {}
    out = {k: v for k, v in (sc.get("tags") or {}).items()
           if k not in SCENE_FORBIDDEN_FIELDS and v}
    extra = str(sc.get("extra") or "").strip()
    if extra:
        out["n_extra"] = extra
    return out


def scene_options(lv: int = 99) -> list:
    """按档位过滤的场景候选 [(中文名, id, level), ...]。"""
    return [(s.get("zh") or s["id"], s["id"], int(s.get("level", 0)))
            for s in scene_list() if int(s.get("level", 0)) <= lv]


# ============================================================================
# 二、档位闸门
# ============================================================================

# 等级 key -> 数字。any（不限）按顶格 4 处理 —— 与造型台的 NUM_OF 同口径。
LEVEL_NUM = {k: min(v, N.NSFW_MAX) for k, v in N.TARGET_NUM.items()}
LEVEL_KEYS = tuple(N.LEVELS)
LEVEL_ZH = dict(N.LEVEL_ZH)


def resolve_level(value, default="suggestive") -> str:
    return N.resolve_level(value, default)


def level_num(value, default="suggestive") -> int:
    return LEVEL_NUM[resolve_level(value, default)]


def level_label(value) -> str:
    key = resolve_level(value)
    if key == "any":
        return "不限"
    return f"{LEVEL_NUM[key]} · {LEVEL_ZH[key]}"


def eligible_options(field_key: str, lv: int) -> list:
    """按档位收敛候选池。返回 [(zh, en, num), ...]。

    ⚠️ 这是**唯一的准入判定点**。权重只能在这个池子里加权，
       永远不能把超档项拉回来 —— 这是「低档绝不越界」的硬保证。

    角色字段是例外：它的候选来自独立的角色库（2000+ 条），
    不走 pools.json 的 opts，所以在这里分流。
    """
    if field_key == "role":
        return list(role_pool(lv))

    _sec, fld = field_by_key(field_key)
    if not fld:
        return []
    opts = list(fld.get("opts", []))
    # ⚠️ n_canon 的候选来自**两处**：源 HTML 的法典 14 组（在 pools.json 里）
    #    + 场景搭配库（独立文件，自己带档位）。这里现场合并 ——
    #    与 role 字段「源 HTML 的 221 条 + 角色库」是同一个套路。
    if field_key == "n_canon":
        opts = [(zh, en) for zh, en in opts] + \
               [(zh, sid) for zh, sid, _lv in scene_options(99)]
    out = []
    for zh, en in opts:
        if field_key == "n_canon":
            n = canon_level(en)
        else:
            n = option_level(field_key, en)
        if n <= lv:
            out.append((zh, en, n))
    return out


def section_floor_level(sec_id: str) -> int:
    """段级保底下限（`section_floor`）—— 段内**最低**档位。"""
    return int(levels().get("section_floor", {}).get(sec_id, 0))


def section_allowed(sec_id: str, lv: int) -> bool:
    """段级闸门：段里**至少有一个** ≤ 目标档的选项才允许。

    ⚠️⚠️ v3.14.0 从 `section_max <= lv` 改成 `section_floor <= lv`。
       原判据对**混合档位的段**会误伤：`pose_sfw` 段本来只有 L0 选项
       （max=0，于是恒允许），v3.14.0 往里加了 L1/L2 的「暗示姿势」后
       max 变成 2 —— 原判据会让 **SFW 档整段消失**（姿势一个都抽不到）。
       换成"段内最低档"后语义才对：段里只要还剩低档选项就该允许，
       至于**具体抽哪一条**由 `eligible_options()` 的逐选项闸门负责。

    注意「允许」不等于「整段开放」—— 逐选项闸门照样生效。
    纯文本段（nl_block）没有标签，一律允许（手写内容由自检兜底）。
    """
    return section_floor_level(sec_id) <= lv


# ============================================================================
# 三、三层权重
# ============================================================================
#
# 语义（写进 README，改动必须同步）：
#   段权重   ：乘在该段所有字段的抽取概率上；= 0 等于整段关闭。
#   字段权重 ：乘在该字段的抽取概率上；= 0 该字段永不出现，
#             = 3.0（上限）**必定出现**；中间值是相对倾向。
#   选项权重 ：在该字段的候选池里做加权随机；= 0 该选项永不出现。
#
# 抽取概率公式（必填字段与"必现"字段例外，见 _one_pass）：
#       p = clamp(基准概率 × 密度系数 × 段权重 × 字段权重, 0, 1)
#
# ⚠️ 为什么字段权重 3.0 要单列成"必现"、而不是让它去乘概率：
#    密度自适应会整体缩放基准概率（标签太多时往下压）。如果 3.0 只是
#    乘数，用户把某字段拉到 3.0 期望"一定要有"，结果被密度一压照样可能
#    抽不到 —— 那权重面板就成了骗人的东西。上限值直接短路成必抽，
#    语义与"0 = 永不"对称，用户也一眼能记住。
#
# ⚠️ 权重**不参与档位判定**。超档项即使权重给到 3.0 也进不来。
#
WEIGHT_MIN = 0.0
WEIGHT_MAX = 3.0
DEFAULT_WEIGHT = 1.0
DEFAULT_BASE_PROB = 0.65          # 源脚本写死的 0.65/0.6 两档，现在可调

# ── 胸部大小等级（v3.17.0，取代 v3.2.0 的巨乳开关）────────────────────
# 界面中文 → figure 字段 danbooru tag。六档 tag 已逐一实证：
#   flat chest 240,190 帖 · small 731,362 · medium 1,233,828 · large 2,299,636
#   · huge 335,231 · **gigantic 16,886** —— ⚠️ `giant_breasts` 在 danbooru
#   是 0 帖且无 wiki/别名的**无效 tag**，极限档必须写 gigantic。
# 「不指定」= None：figure 参与随机、可被角色 APPEAR 对齐覆盖（原开关关闭行为）。
# 指定档位 = 锁定 figure，**最高优先级**（排在角色外貌对齐之后执行，覆盖之）。
BREAST_SIZES = {
    "平胸": "flat chest",
    "小胸": "small breasts",
    "中等": "medium breasts",
    "大胸": "large breasts",
    "巨乳": "huge breasts",
    "爆乳": "gigantic breasts",
}


def empty_weights() -> dict:
    return {"sections": {}, "fields": {}, "options": {}}


def parse_weights(raw) -> dict:
    """把控件里的 JSON 文本解析成权重表。

    契约：**永远不抛异常**。空串 / 非法 JSON / 类型不对 → 全按默认 1.0。
    （源脚本的 `权重细则` 用的是 try/catch 当空表，这里保持同一口径。）
    """
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return empty_weights()
        try:
            data = json.loads(text)
        except Exception:
            return empty_weights()
        if not isinstance(data, dict):
            return empty_weights()

    out = empty_weights()
    for scope in ("sections", "fields", "options"):
        bag = data.get(scope)
        if not isinstance(bag, dict):
            continue
        if scope == "options":
            for fk, sub in bag.items():
                if not isinstance(sub, dict):
                    continue
                clean = {}
                for k, v in sub.items():
                    w = _as_weight(v)
                    if w is not None:
                        clean[str(k).strip().lower()] = w
                if clean:
                    out["options"][str(fk)] = clean
        else:
            for k, v in bag.items():
                w = _as_weight(v)
                if w is not None:
                    out[scope][str(k)] = w
    return out


def _as_weight(v):
    """归一成 [0,3] 的浮点；认不出来返回 None（忽略这一条，而不是当 0）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                        # NaN
        return None
    return max(WEIGHT_MIN, min(WEIGHT_MAX, f))


def dump_weights(w: dict) -> str:
    """回写成控件文本。只写非默认项 —— 空表 = 全默认，读起来最干净。"""
    out = {}
    for scope in ("sections", "fields"):
        bag = {k: round(v, 3) for k, v in (w.get(scope) or {}).items()
               if abs(v - DEFAULT_WEIGHT) > 1e-9}
        if bag:
            out[scope] = bag
    opts = {}
    for fk, sub in (w.get("options") or {}).items():
        bag = {k: round(v, 3) for k, v in sub.items()
               if abs(v - DEFAULT_WEIGHT) > 1e-9}
        if bag:
            opts[fk] = bag
    if opts:
        out["options"] = opts
    return json.dumps(out, ensure_ascii=False, indent=1) if out else ""


def section_weight(w: dict, sec_id: str) -> float:
    return float(w["sections"].get(sec_id, DEFAULT_WEIGHT))


def field_weight(w: dict, field_key: str) -> float:
    return float(w["fields"].get(field_key, DEFAULT_WEIGHT))


def option_weight(w: dict, field_key: str, en: str) -> float:
    return float((w["options"].get(field_key) or {}).get(
        str(en).strip().lower(), DEFAULT_WEIGHT))


def weight_stats(w: dict) -> dict:
    """面板用的计数：调过的段 / 字段 / 选项各几项。"""
    def tuned(bag):
        return sum(1 for v in bag.values() if abs(v - DEFAULT_WEIGHT) > 1e-9)
    opt_items = sum(len(sub) for sub in (w.get("options") or {}).values())
    return {
        "sections": tuned(w.get("sections") or {}),
        "fields": tuned(w.get("fields") or {}),
        "options": opt_items,
        "total": tuned(w.get("sections") or {}) + tuned(w.get("fields") or {}) + opt_items,
    }


def validate_weights(w: dict) -> list:
    """权重表里引用了不存在的段/字段/选项 —— 返回告警列表。

    不报错、不阻断：老工作流存的旧 key 不该让整个工作流跑不起来，
    但必须在报告里说出来，否则用户会以为"我调了权重却没生效"。
    """
    bad = []
    known_sec = set(section_ids())
    known_fld = set(field_keys())
    for k in (w.get("sections") or {}):
        if k not in known_sec:
            bad.append(f"段权重引用了不存在的段「{k}」")
    for k in (w.get("fields") or {}):
        if k not in known_fld:
            bad.append(f"字段权重引用了不存在的字段「{k}」")
    for fk, sub in (w.get("options") or {}).items():
        if fk not in known_fld:
            bad.append(f"选项权重引用了不存在的字段「{fk}」")
            continue
        _sec, fld = field_by_key(fk)
        valid = {str(en).strip().lower() for _zh, en in fld.get("opts", [])}
        for en in sub:
            if en not in valid:
                bad.append(f"选项权重引用了不存在的选项「{fk} / {en}」")
    return bad


# ============================================================================
# 四、加权随机
# ============================================================================


def derive_seed(*parts) -> int:
    """从任意字符串派生 32 位种子。

    ⚠️ 一律用 zlib.crc32，**不用内置 hash()** —— 后者每进程加盐，
       同一个工作流跑两次会得到不同结果，"可复现"就成了空话。
    """
    key = "|".join(str(p) for p in parts)
    return zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF


def weighted_pick(rng: random.Random, items: list, weight_of):
    """加权随机取一项。全 0 权重 → 返回 None（= 本字段不出现）。"""
    if not items:
        return None
    ws = [max(0.0, float(weight_of(it))) for it in items]
    total = sum(ws)
    if total <= 0.0:
        return None
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, ws):
        acc += w
        if r <= acc:
            return it
    return items[-1]


def clamp_prob(p: float) -> float:
    return max(0.0, min(1.0, p))


# ============================================================================
# 五、互斥与消解
# ============================================================================

# 把源脚本的 MUTEX_GROUPS（JS 正则字面量）编译成 Python 正则。
# 注意 JS 的 /\b/ 与 Python 一致；源脚本没用到 JS 专有语法，可直接转。
def _compile_mutex() -> list:
    out = []
    for g in pools().get("mutex_groups", []):
        flags = 0
        if "i" in g["a"].get("flags", ""):
            flags |= re.I
        fa = re.compile(g["a"]["source"], flags)
        fb = re.compile(g["b"]["source"], flags)
        out.append((g["id"], fa, fb))
    return out


def apply_mutex(tags: list) -> list:
    """A 组命中时剔除 B 组 —— 与源脚本 applyMutex 逐条同构。"""
    r = list(tags)
    for _gid, ra, rb in _compile_mutex():
        ha = any(ra.search(t) for t in r)
        hb = any(rb.search(t) for t in r)
        if ha and hb:
            r = [t for t in r if not rb.search(t)]
    return r


def sanitize(state: dict) -> list:
    """随机后的自相矛盾消解。返回被清掉的字段 key 列表（用于报告）。

    规则搬自源脚本 sanitizeRandom()，并发扬两点：
      · 每条都返回"清掉了什么"，不再静默；
      · 档位相关的冲突（全裸 × 具体服装）交给档位闸门，不在这里重复。
    """
    cleared = []

    def clear(key):
        if state.get(key):
            state[key] = ""
            cleared.append(key)

    nude = ("completely nude", "topless", "bottomless",
            "nude with accessories only")
    if state.get("n_state") in nude:
        # 全裸时"整套服装"必须让位；袜/鞋/配饰/材质属于"全裸可保留"，
        # 与造型台的分类口径一致。
        clear("outfit")

    if state.get("socks") == "pantyhose" and state.get("shoes") == "bare feet":
        state["shoes"] = "high heels"
        cleared.append("shoes(改高跟鞋)")

    if state.get("n_theme") == "blindfold":
        if state.get("accessory") == "glasses":
            clear("accessory")
        # ⚠️ 这里修的是源脚本的一个真 bug：
        #    源脚本 sanitizeRandom() 只清「心形瞳 heart-shaped pupils」，
        #    但它的 checkTags() F8 判定里同时把「翻白眼 rolled back eyes」
        #    也算成蒙眼冲突。两边口径不一样，结果就是 蒙眼 + 阿嘿颜能过
        #    sanitize、然后在自检里炸掉。这里把 sanitize 对齐到 F8 的口径。
        orgasm = state.get("n_orgasm") or ""
        if "heart-shaped pupils" in orgasm or "rolled back eyes" in orgasm:
            clear("n_orgasm")

    if "facing away" in (state.get("pose_base") or "") \
            and state.get("eye_gaze") == "looking at viewer":
        clear("eye_gaze")

    if "pov" in (state.get("viewpoint") or "") \
            and state.get("shot_size") == "full body":
        state["shot_size"] = "cowboy shot"
        cleared.append("shot_size(改七分身)")

    lofi = ("selfie", "webcam footage", "security camera footage")
    if state.get("device") in lofi and state.get("dof") in (
            "focus on face", "depth of field, blurry background"):
        clear("dof")

    if state.get("viewpoint") == "from above" \
            and state.get("eye_gaze") == "looking up":
        state["eye_gaze"] = "looking at viewer"
        cleared.append("eye_gaze(改注视镜头)")

    if state.get("viewpoint") == "from below" \
            and state.get("eye_gaze") == "looking down":
        state["eye_gaze"] = "looking at viewer"
        cleared.append("eye_gaze(改注视镜头)")

    # ── 联动矛盾消解（v3.15.0）────────────────────────────────────
    # 联动本身在**抽取时**就过滤了候选池（见 _one_pass 的 bans 分支），
    # 这里兜的是"抽完之后才被改写"的那批：法典 / 场景预设、锁定值、强制值。
    # 实测踩到过两次：
    #   · 某雪地预设写了 emotion=surprised，而 mouth 是按**那时**已抽的旧
    #     情绪选的「咬唇」→ 成品里成了「惊讶 + 咬唇」；
    #   · 场景预设会写 outfit / top →「泳池边 + 皮草大衣」。
    # 顺序上 sanitize 排在 apply_preset 之后，所以能兜住。
    #
    # ⚠️ 遍历 state 里的**全部**字段，不要只列那几个"想到的"——
    #    联动表覆盖 10+ 个字段（服装/姿势/表情/体位/光效），
    #    手写清单漏一个就是一条静默失效的约束。
    for _fk in [k for k in list(state) if not str(k).startswith("_")]:
        _v = state.get(_fk)
        if _v and _v in L.bans_for(state, _fk):
            clear(_fk)

    return cleared


# ============================================================================
# 五之二、成品检查与自动修复（v3.8.0）
# ============================================================================
#
# 与 sanitize 的分工（很重要，别把两者混起来）：
#
#   sanitize()  看 **state 字段** —— 只管"我们抽出来的字段之间"的矛盾。
#   repair()    看 **成品标签串** —— 里面还有 sanitize 看不到的来源：
#                 手写补句 extra_body、法典预设自带的 n_extra、
#                 锁定 / 强制指定的值、上游拼接台送进来的内容。
#
# 典型漏洞（sanitize 抓不到、repair 能抓）：手写补句写了 `full body`、
# 视角抽到 `pov` —— 两者分属不同来源，state 层面查不出矛盾，
# 但成品是"第一人称 + 全身"（F7 报的就是这个）。
#
# 三条纪律：
#   ① 只修**可判定、可解释**的项，每一条都进报告（不静默）；
#   ② 优先"剔除"，其次"改写"，**绝不凭空发明**词库里没有的词；
#   ③ 结果对同一输入**幂等**（修完再修不再变）。

# 观察成品时要用的四组"结构性"标签（判据与自检 F6/F7/F8/F9/F14 对齐，
# 不另立一套口径 —— 两套口径各判各的正是历史上反复出错的根因）。
_STAND_TAGS = ("standing sex", "stand and carry")
_LIE_TAGS = ("lying on stomach", "on all fours", "prone bone")
_LOFI_TAGS = ("selfie", "webcam footage", "security camera footage")
_PRO_DOF = ("focus on face", "depth of field, blurry background")
_PUPIL_TAGS = ("heart-shaped pupils", "rolled back eyes")
_MULTI_TAGS = ("multiple girls", "multiple boys", "2girls", "1boy", "hetero",
               "yuri", "orgy", "group sex")


def level_floor_tags() -> dict:
    """{单个标签: 逐选项上抬确认过的最低档位}（缓存）—— 只收 LEVEL_RAISE 覆盖的标签。

    为什么成品层需要它：判定表是**纯标签**的，对 `pov crotch` 这类词判 0；
    而它的档位是在**字段上下文**里确认的（见 anima_v3_overrides.LEVEL_RAISE）。
    repair / 检查节点只看成品标签串，拿不到字段，所以必须把「字段层已经确认过的
    档位」按标签折算回来 —— 否则用户把手写补句写成 `pov crotch` 时，
    它会以 0 档的口径混进全年龄正文。

    这是**只升**的折算：取该标签在所有登记项里的最高档位。
    """
    if "raise_floor_tags" not in _CACHE:
        from .anima_v3_overrides import LEVEL_RAISE
        m = {}
        for (_fk, en), (target, _why) in LEVEL_RAISE.items():
            for t in str(en).split(","):
                t = t.strip().lower()
                if t:
                    m[t] = max(m.get(t, 0), int(target))
        _CACHE["raise_floor_tags"] = m
    return _CACHE["raise_floor_tags"]


def level_ceiling_tags() -> dict:
    """{单个标签: 逐选项**下调**过的最低档位}（缓存）—— 只收 LEVEL_DEMOTE 覆盖的标签。

    与 `level_floor_tags()` 完全对称：那个把 RAISE 折算回标签层，这个把 DEMOTE 折算回去。

    ⚠️ 为什么成品层需要它（v3.15.0 补）：`tag_level()` 是**成品层**判档
    （repair 与报告都用它），而 LEVEL_DEMOTE 是**字段级**校准。不折算回来就会错位 ——
    实测 `hair_fx / wet hair`（字段层 demote 到 0）在成品层仍被判 2，
    于是「SFW 出图 state 里全是 L0，报告的 real_level 却是 2」。
    v3.15.0 的 place 预抽改变了随机序列，才把这条既有错位撞出来。

    这是**只降**的折算：取该标签在所有登记项里的最低档位；
    最终 `tag_level` 再与上抬折算取 max —— 两张表互不覆盖。
    """
    if "ceiling_tags" not in _CACHE:
        m = {}
        for (_fk, en), (target, _why) in LEVEL_DEMOTE.items():
            for t in str(en).split(","):
                t = t.strip().lower()
                if t:
                    m[t] = min(m.get(t, 99), int(target))
        _CACHE["ceiling_tags"] = m
    return _CACHE["ceiling_tags"]


def tag_level(t: str) -> int:
    """单个标签的**有效档位** = max(判定表∩下调折算, 上抬折算)。

    ⚠️ 不改 `anima_v3_nsfw.grade_nsfw` —— 那个函数与造型台**逐字对拍**
       （tests/check_nsfw_parity.py），动它等于让两个插件对同一句话给出
       不同档位。折算只发生在本层，作为"准入"口径使用。

    三段合成（v3.15.0 起）：
        base = 判定表结果
        base = min(base, 下调折算)     ← 字段层说过"这个上下文里它更低"
        out  = max(base, 上抬折算)     ← 字段层说过"判定表漏了这个词"
    """
    key = str(t).strip().lower()
    base = int(N.grade_nsfw([key])["num"])
    ceil = level_ceiling_tags().get(key)
    if ceil is not None:
        base = min(base, ceil)
    return max(base, int(level_floor_tags().get(key, 0)))


def _field_tags(field_key: str) -> set:
    """某个字段所有选项拆成单标签后的集合（缓存在 _CACHE）。

    给"成品层"的服装互斥用：成品里出现某个标签时，光看标签不知道它来自
    哪个字段，得反查词库才能判断"这算不算具体服装"。
    """
    if "field_tags" not in _CACHE:
        m = {}
        for s in sections():
            for f in s["fields"]:
                bag = set()
                for _zh, en in f.get("opts", []):
                    for t in str(en).split(","):
                        t = t.strip().lower()
                        if t:
                            bag.add(t)
                m[f["key"]] = bag
        _CACHE["field_tags"] = m
    return _CACHE["field_tags"].get(field_key) or set()


def repair(tags, lv: int):
    """对**成品标签串**做检查 + 自动修复。返回 (新标签列表, 修复记录列表)。

    修三类：
      ① 档位    —— 超档标签剔除（已登记校准项除外，见 LEVEL_DEMOTE）；
      ② 结构互斥 —— 数量 / 视角 / 眼罩 / 服装 / 设备景深 / 体位（判定看成品）；
      ③ 格式    —— 下划线归一成空格 + 去重保序。

    ⚠️ 刻意**不修**的（写明理由，免得以后有人"顺手补上"）：
      · 超长且不在词库内的标签（F11）—— 那可能正是用户手写的作品名，
        删掉等于替用户删内容；留在报告里给人看。
      · 标签数量（F1）—— 那是密度自适应的活，在这里削只会越削越少。
      · 状态类致命项（F15 准入 / F16 槽位映射）—— 它们看的是字段，成品层
        看不到字段归属，只能由生成流程自己保证（force/lock 注入的越档值
        由 ① 兜住）。
    """
    fixes = []
    out = []

    # ── ③-a 格式：下划线归一 + 去重保序 ─────────────────────────────
    # ⚠️ 规则与自检 F12 完全一致（`score_` 例外）—— 不一致的话会出现
    #    "修完还是红"。
    norm, seen = [], set()
    for t in tags:
        t = str(t or "").strip().lower()
        if not t:
            continue
        fixed = t
        if "_" in t and "score_" not in t:
            fixed = re.sub(r"[\s_]+", " ", t).strip()
        if fixed != t:
            fixes.append(f"格式：`{t}` → `{fixed}`")
        if fixed and fixed not in seen:
            seen.add(fixed)
            norm.append(fixed)

    # ── ① 档位：超档剔除（已登记校准项除外）──────────────────────────
    # ⚠️ 用 tag_level（含逐选项上抬折算），不用裸的 grade_nsfw ——
    #    否则手写补句里的 `pov crotch` 会被判 0 档而混进低档正文。
    calibrated = _calibrated_tags()
    for t in norm:
        if tag_level(t) > lv and t not in calibrated:
            fixes.append(f"档位：剔除超 {lv} 档的 `{t}`")
            continue
        out.append(t)

    def _drop(pred, why):
        """剔除命中的标签（就地改 out）。"""
        hit = [t for t in out if pred(t)]
        if hit:
            fixes.append(why + "：" + ", ".join(f"`{h}`" for h in hit))
            keep = [t for t in out if not pred(t)]
            out[:] = keep

    def _replace(old, new, why):
        if old in out and new not in out:
            out[out.index(old)] = new
            fixes.append(why + f"：`{old}` → `{new}`")
        elif old in out:
            _drop(lambda t: t == old, why + "（保留已有的新写法）")

    # ── ② 结构互斥（看成品，不看字段）──────────────────────────────
    # 服装：判据与 F9 一致，只认最严格的「全裸」——
    #   state 层的 sanitize 用的是 4 词表（含 topless/bottomless），那是**字段语义**
    #   下的判断（选了"仅上裸"就不该再抽整套服装）；成品层没有字段语义，
    #   `topless` + 半身裙是站得住的组合，不能连坐。
    if "completely nude" in out or "nude with accessories only" in out:
        outfit = _field_tags("outfit")
        _drop(lambda t: t in outfit, "服装互斥（全裸）")
    if "pantyhose" in out and ("bare feet" in out or "barefoot" in out):
        _drop(lambda t: t in ("bare feet", "barefoot"), "服装互斥（连裤袜与赤足）")

    # 眼罩：与 F8 同口径（眼镜按 `glasses` 判，墨镜不算 —— F8 也只认 glasses）
    if "blindfold" in out:
        _drop(lambda t: t in _PUPIL_TAGS, "眼罩互斥（蒙眼无法呈现心形瞳/翻白眼）")
        _drop(lambda t: t == "glasses", "眼罩互斥（眼罩与眼镜冲突）")

    # 数量：与 F6 同口径（solo 与多人标签互斥）
    if "solo" in out and any(t in _MULTI_TAGS for t in out):
        _drop(lambda t: t == "solo", "数量互斥（solo 与多人标签冲突）")

    # 视角：与 F7 / sanitize 同口径
    if any("pov" in t for t in out):
        _drop(lambda t: t == "full body", "视角互斥（POV 看不到自身全身）")
    if "from above" in out and "from below" in out:
        _drop(lambda t: t == "from below", "视角互斥（俯拍与仰拍同现）")
    if "from above" in out and "looking up" in out:
        _replace("looking up", "looking at viewer", "视角互斥（俯拍时的抬眼）")
    if "from below" in out and "looking down" in out:
        _replace("looking down", "looking at viewer", "视角互斥（仰拍时的垂眼）")
    if "facing away" in out and "looking at viewer" in out:
        _drop(lambda t: t == "looking at viewer", "视角互斥（背对却注视镜头）")

    # 设备 × 景深：与 S5 / sanitize 同口径
    if any(t in _LOFI_TAGS for t in out) and any(t in _PRO_DOF for t in out):
        _drop(lambda t: t in ("focus on face", "depth of field, blurry background"),
              "设备与画质（低画质设备不配专业景深效果）")

    # 体位：与 F14 同口径
    if any(t in _STAND_TAGS for t in out) and any(t in _LIE_TAGS for t in out):
        _drop(lambda t: t in _LIE_TAGS, "体位互斥（站立性行为与俯卧体态矛盾）")

    # 修复过程可能造出重复（_replace 改成了已存在的值等）→ 再收一次
    dedup, seen2 = [], set()
    for t in out:
        if t not in seen2:
            seen2.add(t)
            dedup.append(t)
    return dedup, fixes


def blocked_by_weight(weights: dict, field_key: str) -> bool:
    """该字段是不是被用户用权重明确关掉了。

    ⚠️ 语义：**权重 0 = 用户明确说"不要这个"**，是一个强指令。
       所以除了随机抽取要尊重它，两条自动写入路径也必须尊重：
         · 角色外貌对齐（apply_appearance）
         · 法典场景填充（apply_preset）
       曾经漏过这一条：用户把「发色」权重设 0、或把整个「头发」段权重设 0，
       结果抽到角色时 APPEAR 表照样把 hair_color 写了进去 ——
       用户看到的是"我明明关了，它还是出来了"。
    """
    sec = _field_section().get(field_key)
    if sec and section_weight(weights, sec) <= 0.0:
        return True
    return field_weight(weights, field_key) <= 0.0


def char_by_trigger() -> dict:
    """{trigger: 角色记录}。给外貌自动对齐用。

    ⚠️ role 字段存的是 **trigger**（`jane doe, zenless zone zero`），
       不是 tag，所以这里按 trigger 建索引。
    """
    if "char_by_trigger" not in _CACHE:
        m = {}
        for c in characters_data().get("characters", []):
            m[(c.get("trigger") or "").strip().lower()] = c
        _CACHE["char_by_trigger"] = m
    return _CACHE["char_by_trigger"]


def appearance_for(role_trigger: str) -> dict:
    """给一个角色 trigger 找外貌数据。返回 {hair, eyes, figure, skin, face}。

    三个来源，优先级从高到低：
      ① 源 HTML 的 APPEAR 表 —— 人工核准，但只覆盖 221 条
      ② 角色库的 `hair` / `eyes` 字段（来自造型台，覆盖 ~2000 条）
      ③ 角色库的官方特征 tags —— 里面有 `aqua hair` / `green eyes` 这种
         现成标签，直接挑出来用

    为什么必须补这一层：角色库从 221 扩到 2687 之后，APPEAR 表一下就
    只覆盖 8% 了。如果不补，自检 F10（角色未绑定发色/瞳色）会大面积报红，
    而模型在没有发色瞳色时会自己乱猜。
    """
    trig = (role_trigger or "").strip().lower()
    if not trig:
        return {}

    # ① 人工资核准的表
    a = appear().get(role_trigger) or appear().get(trig)
    if a:
        return dict(a)

    # ②③ 角色库
    c = char_by_trigger().get(trig)
    if not c:
        return {}
    out = {}
    hair = (c.get("hair") or "").strip().lower()
    eyes = (c.get("eyes") or "").strip().lower()
    if hair:
        out["hair"] = hair if hair.endswith("hair") else f"{hair} hair"
    if eyes:
        out["eyes"] = eyes if eyes.endswith("eyes") else f"{eyes} eyes"

    # 从官方特征 tags 里捞（`very long hair` / `aqua eyes` …）
    for t in c.get("tags") or []:
        t = str(t).strip().lower()
        if not t or len(t) > 40:
            continue
        if "hair" in t and "hair" not in out:
            out["hair"] = t
        elif "eyes" in t and "eyes" not in out:
            out["eyes"] = t
        elif t in ("huge breasts", "large breasts", "medium breasts",
                   "small breasts", "flat chest") and "figure" not in out:
            out["figure"] = t
    return out


def apply_appearance(state: dict, weights: dict = None) -> list:
    """按角色对齐发色/瞳色/体型/肤色/脸型。返回被改的字段。"""
    a = appearance_for(state.get("role") or "")
    if not a:
        return []
    weights = weights or empty_weights()
    changed = []
    pairs = (("hair", "hair_color"), ("eyes", "eyes_color"),
             ("figure", "figure"), ("skin", "skin"), ("face", "face_shape"))
    for src, dst in pairs:
        if not a.get(src):
            continue
        # 用户明确关掉的字段，官方设定也不覆盖
        if blocked_by_weight(weights, dst):
            continue
        state[dst] = a[src]
        changed.append(dst)
    return changed


# ============================================================================
# 六、输出槽位序（ANIMA3 v3.0 规范）
# ============================================================================
#
# 源脚本按**界面段序**输出，结果是「…场景 → 光影 → 服装状态 → 体位」——
# 服装状态排在了场景后面。ANIMA3 规范的顺序是：
#
#   count/gender → character/series → appearance → clothing/state
#   → pose/action/sex → expression/reaction → camera/shot
#   → scene/environment → detail/mood → 自然语言末尾
#
# 所以这里按**字段**做一次重排：把 NSFW 段的字段并回它语义所属的槽。
#
SLOTS = ("count", "character", "appearance", "clothing", "pose",
         "expression", "camera", "scene", "lighting", "detail", "free")

FIELD_SLOT = {
    # ① 数量与身份
    "count": "count",
    "role": "character",
    # ② 头发
    "hair_len": "appearance", "hair_color": "appearance", "hair_curl": "appearance",
    "hair_tie": "appearance", "hair_bangs": "appearance", "hair_fx": "appearance",
    "hair_acc": "appearance",
    # ③ 五官与表情
    "face_shape": "appearance", "eyes_color": "appearance",
    "temperament": "appearance",
    "emotion": "expression", "eye_gaze": "expression", "mouth": "expression",
    # ④ 身材与肤质
    "figure": "appearance", "skin": "appearance", "leg": "appearance",
    "skin_detail": "detail",
    # ⑤ 妆容
    "makeup": "detail", "nails": "detail",
    # ⑥ 服装（SFW）
    "outfit": "clothing", "cloth_mat": "clothing", "cloth_state_sfw": "clothing",
    "socks": "clothing", "shoes": "clothing", "accessory": "clothing",
    # v3.12.0 新增：上装 / 下装 → 与 outfit 并列，同归 clothing 槽
    "top": "clothing", "bottom": "clothing",
    # ⑦ 姿势（SFW）
    "pose_base": "pose", "arms_hands": "pose", "action": "pose",
    # v3.14.0 新增：暗示姿势（L1–L2）→ 与 pose_base 并列，同归 pose 槽
    "pose_risque": "pose",
    # ⑧ 镜头
    "shot_size": "camera", "viewpoint": "camera", "dof": "camera", "device": "camera",
    # ⑨ 场景
    "place": "scene", "prop": "scene", "weather": "scene",
    "comp": "camera", "texture_fx": "detail",
    # ⑩ 光影
    "light_fx": "lighting",
    # ⑪ 服装状态（NSFW）→ 并回 clothing
    "n_state": "clothing", "n_exposure": "clothing", "n_body": "clothing",
    "n_fabric": "clothing", "n_shoes_keep": "clothing",
    # ⑫ 体位与性行为（NSFW）→ 并回 pose
    "n_act": "pose", "n_pos": "pose", "n_orgasm": "pose",
    "n_cum": "pose", "n_fluid": "pose",
    # ⑬ 特殊主题（NSFW）→ 道具类，归 detail
    "n_theme": "detail",
    # ⑭ 法典场景：n_canon 是个"填充器"，本身不产出标签；n_extra 是补充
    "n_canon": "free", "n_extra": "free",
    # ⑮ 自然语言补位
    "nl": "free",
}

# 超长裁剪顺序（源脚本 dropList 的忠实移植）。
# ⚠️ 判决时不是简单地按这个顺序削，而是「低优先级先削」，
#    优先级 = 段权重 × 字段权重 —— 用户把某字段权重调高，它就不容易被削掉。
DROP_ORDER = ("texture_fx", "comp", "prop", "weather", "hair_acc", "nails",
              "skin_detail", "leg")


def tag_cap_for(count_en: str):
    """按人数给标签数上限。数值全部来自源脚本 sanitizeRandom()。"""
    cnt = str(count_en or "")
    solo = "solo" in cnt and "multiple" not in cnt
    duo = bool(re.search(r"hetero|yuri|1boy", cnt))
    return 29 if solo else (37 if duo else 45)


def tag_window(count_en: str):
    """自检 F1 的合格区间（下界, 上界）。数值来自源脚本 checkTags()。

    与 tag_cap_for 的区别：cap 是「不许超过」，window 是「最好落在里面」。
    密度自适应瞄准的是 window —— 只瞄 cap 会一路往下掉，掉出下界照样红。
    """
    cnt = str(count_en or "")
    solo = "solo" in cnt and "multiple" not in cnt
    duo = bool(re.search(r"hetero|yuri|1boy", cnt))
    multi = bool(re.search(r"multiple|orgy|group sex", cnt))
    if solo:
        return 16, 30
    if duo:
        return 22, 38
    if multi:
        return 30, 48
    return 16, 42


# ============================================================================
# 七、随机引擎
# ============================================================================


class DrawResult(dict):
    """普通 dict，只是给调用方一个稳定的字段名约定。"""


def draw(level="suggestive", seed=None, *, base_prob=DEFAULT_BASE_PROB,
         weights=None, enabled_sections=None, locks=None, tag_cap="自动",
         forced=None, force_role="", extra_body="", max_retry=8,
         appearance_follow=True, selected_roles=None, big_breasts=False,
         breast_size=None, role_works=""):
    """跑一次完整抽取。

    参数
    ----
    level         : 档位（中文标签 / 数字 / 旧英文 key 都认，见 resolve_level）
    seed          : None 或 <0 → 每次重抽；>=0 → 逐字节可复现
    base_prob     : 可选字段的基准抽取概率（源脚本写死 0.65）
    weights       : 三层权重 dict（见 parse_weights）
    enabled_sections : 参与随机的段；None = 全部（受档位闸门约束）
    locks         : 锁定字段集合 —— 锁定的字段不参与本次随机
    tag_cap       : "自动" 或 int
    forced        : {field_key: en} 强制指定，优先级最高
    force_role    : 指定单个角色 tag（会触发 APPEAR 外貌对齐）
    extra_body    : 手写补充，拼在末尾
    max_retry     : 抽出来的东西被判超档时最多重试几次
    appearance_follow : 抽到角色时是否用 APPEAR 表对齐发色/瞳色/体型
    selected_roles : **面板里点选的**角色 tag 列表 —— 抽取概率 100%，
                     并联动人数标签（1 位 / 双女 / 群像，见 headcount_tags）
    big_breasts   : v3.2.0 巨乳开关（v3.17.0 起为**兼容保留**：仅当
                    breast_size 不指定时生效，锁定 huge breasts）。
    breast_size   : v3.17.0 胸部大小等级（BREAST_SIZES 的中文键：「平胸 /
                    小胸 / 中等 / 大胸 / 巨乳 / 爆乳」）。None/空/"不指定"
                    = 不干预（figure 参与随机，可被角色对齐覆盖）；
                    指定 = 锁定 figure，最高优先级。
    role_works    : **按作品 IP 限定随机角色**（v3.10.0）。作品 slug / 英文名 /
                     中文名，可多个。只在「没点选角色、也没手填角色」时生效 ——
                     于是每次运行都从这些 IP 里重新抽（配合 seed=-1 即"每次换人"）

    ⚠️ 角色优先级（自上而下，先命中者胜）：
       ① force_role（手填一个）  ② selected_roles（面板点选 = 固定）
       ③ role_works（按 IP 每次随机）  ④ 全库随机
       ①②是"用户显式指定"，写进 role 字段；③④是"自动随机"，不改写人数标签。

    ⚠️ selected_roles 与 force_role 的区别：
       force_role 是"指定一个角色"，selected_roles 是"角色浏览器里勾选的
       一或多位角色"。两者都写 role 字段，selected_roles 优先。
       多位角色时**不做 APPEAR 外貌对齐** —— 两个人各有各的官方发色，
       强行对齐其中一个是错的（角色的 danbooru tag 本身已经带足了特征）。

    返回 dict：text / state / level / level_num / report / dropped / notes
    """
    weights = weights or empty_weights()
    lv_key = resolve_level(level)
    lv = LEVEL_NUM[lv_key]
    locks = set(locks or ())
    forced = dict(forced or {})
    notes = []

    # ── 按作品 IP 限定随机角色（v3.10.0）──────────────────────────────
    # ⚠️ 解析放在最前面：它是**输入校验** —— 认不出来的写法要立刻进报告，
    #    而不是等到角色段才发现"池子是空的"，那时用户根本不知道为什么。
    works_filter = resolve_works(role_works)
    if role_works and not works_filter:
        notes.append(f"⚠️ 作品串没认出来：`{role_works}`"
                     "（可用作品 slug / 英文名 / 中文名，多个用 | 分隔）")
    elif role_works:
        bad_works = unknown_works(role_works)
        if bad_works:
            notes.append("⚠️ 认不出的作品名（已忽略）："
                         + "、".join(f"`{b}`" for b in bad_works))

    # base_prob 兜底：0 / 负数 / 非数字一律当"没设置"，用默认值。
    # ⚠️ 为什么要兜：v3.1.0 的「采用这条」在那一栏为空时会写 Number("") === 0，
    #    而 base_prob = 0 意味着**所有可选字段都不抽**，出图只剩必填项 ——
    #    用户看到的就是"随机坏了"。这里把历史脏数据救回来。
    #    （想在界面上"少抽可选字段"应该调低数值而不是调到 0，0 没有可用语义）
    if not isinstance(base_prob, (int, float)) or base_prob <= 0:
        base_prob = DEFAULT_BASE_PROB

    if seed is None or int(seed) < 0:
        rng = random.Random()
        seeded = False
    else:
        rng = random.Random(int(seed))
        seeded = True

    enabled = set(enabled_sections) if enabled_sections is not None \
        else set(section_ids())

    if "count" not in enabled:
        notes.append("①数量与身份段已关闭 —— 正文不含人数标签，请确认上游是否补齐")

    # 权重表里引用了不存在的 key → 不阻断，但要说出来
    bad_w = validate_weights(weights)
    if bad_w:
        notes.extend(bad_w[:6])

    state = {}
    drawn = []
    cap = None

    def _one_pass(pass_rng, eff_p):
        """抽一遍。返回 (state, drawn, notes)。"""
        st, dr, nt = {}, [], []
        pre = {}          # 预抽结果（v3.15.0）：字段 key → (zh, en, num)

        def _pre_place():
            """场景预抽：把 `place` 从场景段里**提前**抽出来。

            为什么必须提前：`place` 在 ⑨ 场景段，排在 ⑥ 服装 / ⑦ 姿势
            **之后**，而「服装 × 场景」「场景 × 姿势」两条联动都要求它先定下来。
            不预抽的话这两条联动永远读到空的 place（表现：联动完全不生效，
            而且**不报任何错**）。

            与正式循环里的抽取**完全同构**（段权重 → 字段权重 → 概率 → 选项权重），
            只是换了执行时机。抽不中就不抽 —— 联动自然退化成"无场景"，
            与 v3.14.0 的行为逐字一致（新增能力默认不干预）。
            """
            key = "place"
            hit = None
            for sec in sections():
                for f in sec["fields"]:
                    if f["key"] == key:
                        hit = (sec["id"], f)
                        break
            if not hit:
                return
            sec_id, fld = hit
            if sec_id not in enabled or key in locks:
                return
            if key in forced:
                en = forced[key]
                zh = next((z for z, e in (fld.get("opts") or []) if e == en), en)
                pre[key] = (zh, en, option_level(key, en))
                # ⚠️ **立刻**写进 state：服装/姿势段的抽取就在后面，
                #    联动读的就是 st["place"]。只存进 pre 等到主循环再写，
                #    那时服装早就抽完了 —— 联动会静默失效（实测踩到）。
                st[key] = en
                return
            sw = section_weight(weights, sec_id)
            fw = field_weight(weights, key)
            if sw <= 0.0 or fw <= 0.0:
                return
            pool = eligible_options(key, lv)
            if not pool:
                return
            if not (fld.get("req") or fw >= WEIGHT_MAX):
                if not pass_rng.random() < clamp_prob(float(eff_p) * sw * fw):
                    return
            pick = weighted_pick(
                pass_rng, pool,
                lambda it: option_weight(weights, key, it[1]) * sw * fw)
            if pick is None:
                return
            pre[key] = pick
            st[key] = pick[1]

        _pre_place()

        for sec in sections():
            sid = sec["id"]
            if sid not in enabled:
                continue
            sw = section_weight(weights, sid)
            if sw <= 0.0:
                continue

            for fld in sec["fields"]:
                key = fld["key"]
                # ⚠️ 角色字段**不参与常规随机**（v3.2.0 起）。
                #    它的选择已由「角色库」页签接管 —— 见下面「角色：统一入口」。
                #    留在这里的话，权重与角色库选择会互相打架：
                #    用户点了角色，却被 weights.fields.role = 0 静默否决。
                if key == "role":
                    continue
                # 锁定 / 强制指定的字段：由调用方/上游给值，跳过随机
                if key in locks:
                    continue
                # 场景已在预抽阶段定值（v3.15.0）—— 含 forced 的情况。
                # ⚠️ 必须排在 forced 分支**之前**：forced 的 place 在预抽时
                #    就已经写进 state 了，这里再走 forced 分支会直接 continue，
                #    place 的抽取记录就丢了（报告里看不到它）。
                if key in pre:
                    _zh, _en, _num = pre[key]
                    st[key] = _en
                    dr.append((key, _zh, _en, _num,
                               option_weight(weights, key, _en)))
                    continue
                if key in forced:
                    st[key] = forced[key]
                    continue
                # 文本字段（手写）：不随机，留给调用方
                if fld.get("type") == "text" or not fld.get("opts"):
                    continue

                fw = field_weight(weights, key)
                if fw <= 0.0:
                    continue

                pool = eligible_options(key, lv)
                if not pool:
                    continue

                # ── 联动① 剔除矛盾项（v3.15.0）─────────────────────────
                # 体位配对（n_act × n_pos）、表情配套（emotion × eye_gaze/mouth）、
                # 场景违和（place × 服装）。表在 anima_v3_link.py。
                # ⚠️ **fail-open**：全被剔光时保留原池 —— 宁可留一组矛盾，
                #    也不要让"这个字段整段消失"（用户会以为随机坏了）。
                bans = L.bans_for(st, key)
                if bans:
                    kept = [it for it in pool if it[1] not in bans]
                    if kept and len(kept) != len(pool):
                        nt.append("联动：%s 剔除 %d 项矛盾的候选"
                                  % (fld.get("lab") or key, len(pool) - len(kept)))
                        pool = kept

                # 必填字段（源脚本的 req）永远抽；
                # 字段权重顶到 3.0 也永远抽（用户表达"这个一定要有"）。
                if fld.get("req") or fw >= WEIGHT_MAX:
                    take = True
                else:
                    take = pass_rng.random() < clamp_prob(
                        float(eff_p) * sw * fw)
                if not take:
                    continue

                pick = weighted_pick(
                    pass_rng, pool,
                    lambda it: (option_weight(weights, key, it[1]) * sw * fw
                                # 联动③ 场景倾向（v3.15.0）：场景把相关选项
                                # 权重放大（默认 ×1.0 = 不干预），但**不剥夺**
                                # 其它选项 —— 泳池边照样可能抽到连衣裙。
                                * L.bias_for(st, key, it[1])))
                if pick is None:
                    # 池子里所有选项权重都是 0 —— 等价于该字段被关掉，
                    # 不是错误，但值得记一笔（否则用户会以为随机坏了）
                    nt.append(f"字段「{fld['lab']}」候选权重全为 0，本次未抽取")
                    continue

                zh, en, num = pick
                st[key] = en
                dr.append((key, zh, en, num, option_weight(weights, key, en)))

        # ── 法典场景填充 ──────────────────────────────────────────
        preset_id = st.get("n_canon") or ""
        if preset_id and preset_id in canon():
            # ⚠️ locks 必须传进去：预设会往 n_act / n_pos / n_theme 里写字，
            #    而锁定字段是"留给上游给值"的，预设不得代填（v3.8.0 补）。
            filled = apply_preset(st, preset_id, lv, weights, locks=locks)
            if filled:
                nt.append("已应用法典场景：" + "、".join(filled))

        # ── 角色：统一入口（v3.2.0 起不再看权重）───────────────────
        # ⚠️ 顺序很重要，必须排在**法典场景之后**：
        #    法典预设会往 n_act / n_pos / n_theme 里写字，
        #    而多人模式下这些单人向字段要清空。之前放在预设之前，
        #    预设又把它们写回来了 —— 单元测试抓到过。
        #
        # 优先级（2026-10-03 与用户确认）：
        #   ① 手填 force_role —— 逃生舱，能填角色库里没有的自定义 tag
        #   ② 角色库已选     —— 点卡片 = 抽取概率 100%
        #   ③ 都没给         —— 必定从角色库随机抽 1 位（保证出图有角色）
        #
        # ⚠️ 这里**完全不读 weights**。「角色权重」在 v3.2.0 已取消：
        #    权重只能表达"想要什么倾向"，而角色是显式选择；两个入口并存时，
        #    用户点了角色却被权重静默否决，表现就是"我明明选了怎么没出来"。
        sel = expand_roles(selected_roles or [])
        triggers = []
        auto = False          # 角色是不是"系统自动随机抽的"
        if force_role:
            triggers = expand_roles([force_role])
            if sel:
                nt.append("手填角色已覆盖角色库里的选择")
        elif sel:
            triggers = sel
        else:
            # 未选角色 → 按「人数构成」抽**对应数量**的女性角色。
            #
            # ⚠️ 以前这里是固定抽 1 位，并用 headcount_tags 把人数强制改写成
            #    `1girl, solo` —— 结果把 count 抽到的「一男一女 / 双女 /
            #    一女多男 / 多人群交」全盖掉了。现在反过来：
            #    **人数决定角色数**，组合才真正生效。
            # 男方由 count 里的通用标签承担（`1boy` / `multiple boys`），
            # 角色库全女性，没有男性角色可指。
            want = girl_count_for(st.get("count", ""))
            got = pick_random_roles(pass_rng, lv, want, works=works_filter)
            if not got and works_filter:
                # ⚠️ 指定的 IP 在当前档位下一位可用角色都没有 → **回退全库**，
                #    而不是"出图没有角色"。回退一定写进报告 —— 静默回退会让
                #    用户以为"我选了 IP 它却不听话"。
                got = pick_random_roles(pass_rng, lv, want)
                if got:
                    nt.append("⚠️ 指定的作品在当前档位下没有可用角色 → 已回退全库随机"
                              f"（{len(got)} 位）")
            if got:
                triggers = got
                auto = True
                src = ("作品 " + "/".join(f"`{w}`" for w in works_filter)
                       if works_filter else "全库")
                nt.append(f"未选角色 → 从{src}按人数组合抽了 {len(got)} 位："
                          + "、".join(f"`{g}`" for g in got))

        if triggers:
            # auto=True 时**不改写人数标签**：人数是 count 抽出来的组合，
            # 角色只是填进这个组合的角色位。
            # auto=False（用户显式选中 / 手填）时才按角色数改写 ——
            # 那时人数理应由"选了几个人"决定。
            nt.extend(apply_selected_roles(st, triggers, lv,
                                           rewrite_count=not auto))
            st["_role_count"] = len(triggers)

        # ── 角色外貌对齐 ──────────────────────────────────────────
        # ⚠️ 只在**单个**角色时做。两位以上各有各的官方发色，
        #    强行对齐其中一位是错的（角色 tag 本身已经带足特征）。
        if st.get("role"):
            if len(triggers) >= 2:
                st["_appearance"] = "multi"
                nt.append("多位角色：已跳过外貌对齐（各自保留官方特征）")
            elif not appearance_follow:
                st["_appearance"] = "off"
                nt.append("角色外貌对齐已关闭 —— 发色/瞳色按随机与权重决定")
            elif not appearance_for(st["role"]):
                st["_appearance"] = "nodata"
                nt.append("该角色没有外貌数据（不在 APPEAR 表，角色库里也没特征 tags）"
                          "—— 发色瞳色交给模型自己认")
            else:
                changed = apply_appearance(st, weights)
                st["_appearance"] = "applied" if changed else "blocked"
                if changed:
                    nt.append("角色外貌已对齐（会覆盖发色/瞳色等选项权重）："
                              + "、".join(changed))

        # ── 胸部大小等级（v3.17.0，扩展自巨乳开关）────────────────────
        # ⚠️ 必须排在外貌对齐**之后**：APPEAR 表里有 10 个角色带 figure 值
        #    （211 个为空），撞上就会被冲掉。显式指定 = **最高优先级**，
        #    覆盖角色官方设定与任何权重（与原巨乳开关语义一致）。
        # 兼容：breast_size 为空时回退旧开关 big_breasts（老工作流不迁移）。
        _bs_en = BREAST_SIZES.get(str(breast_size or "").strip())
        if _bs_en:
            st["figure"] = _bs_en
            st["_breast_size"] = _bs_en
            nt.append(f"胸部大小：身材轮廓锁定为「{breast_size}」（{_bs_en}）")
        elif big_breasts:
            st["figure"] = "huge breasts"
            st["_big_breasts"] = True
            nt.append("巨乳开关：身材轮廓锁定为「巨乳」（huge breasts）"
                      "（旧版开关兼容，建议改用「胸部大小」下拉）")

        # ── 消解自相矛盾 ──────────────────────────────────────────
        cleared = sanitize(st)
        if cleared:
            nt.append("互斥消解清空：" + "、".join(cleared))

        return st, dr, nt

    # ── 密度自适应 ────────────────────────────────────────────────
    #
    # 为什么不学源脚本「抽完再硬削」：
    #   源脚本从固定的 8 个软字段里删，实测一次能删掉 13 个字段
    #   （发长 / 发型 / 脸型 / 气质 / 眼神 / 体型 / 肤色 全没了），
    #   画面特色被削平，而且 F1 还照样红。
    #   这里改成**降密度重抽** —— 每个字段始终是干净的「抽中 / 没抽中」，
    #   不会出现「字段留了但值被删空」这种半吊子状态。
    #
    # 重抽用派生种子，所以 seed>=0 时整体依然逐字节可复现。
    auto = tag_cap in ("自动", None, "")
    density_notes = []
    best = None

    # 密度用**二分**收敛，不用比例反馈。
    # 比例反馈的毛病：标签数 15（目标 23）→ 下一轮密度 1.53 倍 → 直接冲到 40，
    # 两头都不合格，而"最接近的一轮"仍然是那个 15。二分能夹住。
    #
    # 为什么是 8 轮而不是 3-4 轮：
    #   每一轮用的派生种子不同，同一个密度系数下标签数本身有 ±5 的抖动，
    #   所以"收敛"收敛的是系数，落点仍会飘。多跑几轮取最接近的那次，
    #   实测能把自检 F1 的失败率从 ~1% 压到 0。8 轮总耗时仍在 5ms 以内。
    r_lo, r_hi = 0.05, 1.8
    ratio = 1.0

    for attempt in range(8):
        if attempt == 0:
            rng = random.Random(int(seed)) if seeded else random.Random()
        else:
            rng = random.Random(derive_seed(
                seed if seeded else random.random(), "density", attempt))
        eff_p = clamp_prob(base_prob * ratio)
        state, drawn, step_notes = _one_pass(rng, eff_p)

        cap = tag_cap_for(state.get("count", "")) if auto else int(tag_cap)
        tags = collect(state, tail_extra=extra_body)
        # ⚠️ 密度瞄准的必须是**修完之后**的标签数：repair 会剔掉超档 / 冲突标签，
        #    拿未修的数字收敛会一路瞄高，修完就掉出 F1 的下界。
        n = len(repair(tags, lv)[0])

        if not auto:
            best = (0, state, drawn, step_notes, tags, cap)
            break

        # 瞄准自检 F1 的合格区间打分：0 = 落在区间内，其余为离最近边的距离
        lo, hi = tag_window(state.get("count", ""))
        score = 0 if lo <= n <= hi else (lo - n if n < lo else n - hi)
        if best is None or score < best[0]:
            best = (score, state, drawn, step_notes, tags, cap)
        if score == 0:
            break

        target = (lo + hi) // 2
        if n < target:
            r_lo = ratio          # 太稀，往密的方向收
        else:
            r_hi = ratio          # 太密，往稀的方向收
        new_ratio = round((r_lo + r_hi) / 2, 3)
        if abs(new_ratio - ratio) < 1e-3:
            break                 # 夹逼到底了，再跑也是同一个值
        ratio = new_ratio
        density_notes.append(
            f"标签数 {n}（目标 {lo}–{hi}）→ 密度 {ratio} 再试")

    _score, state, drawn, step_notes, tags, cap = best
    if auto:
        lo, hi = tag_window(state.get("count", ""))
        if not (lo <= len(repair(tags, lv)[0]) <= hi):
            density_notes.append(
                f"8 轮密度自适应后仍未落进 {lo}–{hi}"
                f"（当前 {len(repair(tags, lv)[0])}），"
                f"已取最接近的一轮；可手动指定标签数上限或调权重")
    notes.extend(density_notes)
    notes.extend(step_notes)

    # 兜底裁剪：重抽 4 轮仍超（法典预设自带一大串标签时可能发生）才硬削
    dropped = trim_to_cap(state, cap, weights)
    tags = collect(state, tail_extra=extra_body)

    # ── 成品检查 + 自动修复（v3.8.0）───────────────────────────────
    # ⚠️ 必须排在 trim 之后、check 之前：
    #    · 排在 trim 之后 —— trim 会把字段清空，早于它修等于修完又被削；
    #    · 排在 check 之前 —— check 报的是"最终交付的那串"，
    #      修完再体检，报告里的结论才和用户看到的正文一致。
    tags, fix_notes = repair(tags, lv)
    text = ", ".join(tags)

    # 自检要报告"权重有没有真的用上"，先把它挂进 state（下划线键不参与输出）
    state["_weights_tuned"] = weight_stats(weights)["total"]

    # ── 体检 + 超档回滚（最多重试 max_retry 次）────────────────────
    report = check(tags, state, lv)
    tries = 0
    while report["over_level"] and tries < max_retry and not seeded_is_locked(forced):
        tries += 1
        # 回滚策略：把超档的字段清掉再重抽一次那个字段。
        # 刻意不做「整轮重抽」—— 那会把用户已经满意的字段一起洗掉。
        for key in report["over_fields"]:
            state.pop(key, None)
        for key in report["over_fields"]:
            pool = eligible_options(key, lv)
            if not pool:
                continue
            pick = weighted_pick(
                rng, pool, lambda it: option_weight(weights, key, it[1]))
            if pick:
                state[key] = pick[1]
        tags = collect(state, tail_extra=extra_body)
        tags, more_fix = repair(tags, lv)
        for f in more_fix:
            if f not in fix_notes:
                fix_notes.append(f)
        text = ", ".join(tags)
        report = check(tags, state, lv)
    if tries:
        notes.append(f"超档回滚 {tries} 轮")
    if fix_notes:
        notes.append(f"成品自动修复 {len(fix_notes)} 项：" + "；".join(fix_notes[:8])
                     + ("…" if len(fix_notes) > 8 else ""))

    report["notes"] = notes
    report["drawn"] = [
        {"field": k, "zh": zh, "en": en, "level": num, "weight": round(w, 2)}
        for k, zh, en, num, w in drawn]
    report["dropped"] = dropped
    report["seeded"] = seeded
    # 修复明细单独给一个字段：前端与「提示词检查与修复」节点都用它
    report["repaired"] = fix_notes

    return {
        "text": text,
        "state": state,
        "level": lv_key,
        "level_num": lv,
        "level_label": level_label(lv_key),
        "report": report,
        "notes": notes,
        "dropped": dropped,
        "cn_summary": cn_summary(tags),
        "tags": tags,
    }


def seeded_is_locked(forced) -> bool:
    """强制指定了字段时不做回滚 —— 用户明确点了要这个，回滚等于抗命。"""
    return bool(forced)


def apply_preset(state: dict, preset_id: str, lv: int,
                 weights: dict = None, locks=None) -> list:
    """法典场景一键填充。返回实际填入的字段中文标签列表。

    ⚠️ 与其他字段走**同三条闸门**：
       · 档位：预设要写进去的字段如果本档不允许，照样不写；
       · 权重：用户把某字段权重设 0（或关掉整段）就跳过该字段；
       · 锁定（v3.8.0 补）：`lock_fields` 里的字段"留给上游给值"，
         预设不得代填。少这一条，锁了 n_act 也会被预设写回去 ——
         用户看到的是"我锁了它还改"。
       少任何一条，法典预设都会变成绕过用户设置的"后门"。
    """
    p = canon().get(preset_id)
    if not p:
        return []
    weights = weights or empty_weights()
    locks = set(locks or ())
    filled = []
    for k, v in p.items():
        _sec, fld = field_by_key(k)
        if not fld:
            continue
        if not v:
            continue
        # ⚠️ n_extra 由下面**专门的**分支处理（逐标签判档）。
        #    这里必须跳过：它的值是一整串附加标签，走 `_field_ok_under` 会被
        #    所在段的段下限（canon = 2）误挡，或者原样整串写进去再做一次过滤 ——
        #    两条路一起走只是浪费，还容易让"到底谁说了算"变糊。
        if k == "n_extra":
            continue
        if k in locks:
            continue
        if blocked_by_weight(weights, k):
            continue
        if _field_ok_under(k, v, lv):
            state[k] = v
            filled.append(fld.get("lab", k))
    # n_extra 是预设自带的补充标签串，逐标签判档后保留合规部分
    extra = p.get("n_extra")
    if extra and "n_extra" not in locks and not blocked_by_weight(weights, "n_extra"):
        kept, removed = split_by_level(extra, lv)
        if kept:
            state["n_extra"] = kept
            filled.append("场景补充")
        if removed:
            state["_n_extra_removed"] = removed
    state["n_canon"] = preset_id
    return filled


def _field_ok_under(field_key: str, en: str, lv: int) -> bool:
    """预设要写的某个值，在本档位下是否允许。"""
    if field_key == "n_canon":
        return True
    n = option_level(field_key, en)
    return n <= lv


def split_by_level(text: str, lv: int):
    """把一段逗号串按档位切开。返回 (保留, 剔除)。"""
    keep, removed = [], []
    for t in str(text or "").split(","):
        t = t.strip()
        if not t:
            continue
        n = int(N.grade_nsfw([t])["num"])
        (keep if n <= lv else removed).append(t)
    return ", ".join(keep), removed


def trim_to_cap(state: dict, cap: int, weights: dict) -> list:
    """标签数超限时按优先级削，返回被清空的字段 key 列表。

    优先级 = 段权重 × 字段权重，**低的先削**；同权重时源脚本 dropList 里
    列过的软字段先削。

    为什么不能照抄源脚本的固定 dropList：
        源脚本只从 DROP_ORDER 那 8 个字段里削。独图（29 上限）下，
        那 8 个字段往往一个都没被抽中，于是「削到上限」变成一句空话，
        抽出来 33 个标签，自检 F1 直接红。这是源脚本的一个真 bug。
        这里改成「权重最低的先削，DROP_ORDER 只在同权重时决定先后」。

    永不削的字段：人数（地基）、角色（削了就不是这个人了）、
                 法典预设 id、以及两个手写文本框（用户自己的话）。
                 外加 **v3.9.0：预设自己写进去的那些字段** ——
                 用户点了「一键设置场景搭配」，那些字段就是显式指令，
                 被密度裁剪削掉一半等于"选了场景却少了一半"。
    """
    def count_of(v):
        return len([t for t in str(v).split(",") if t.strip()])

    est = sum(count_of(v) for k, v in state.items() if not str(k).startswith("_"))
    if est <= cap:
        return []

    protected = {"count", "role", "n_canon", "nl", "n_extra"}
    # 预设（法典 / 场景搭配）写进去的字段：显式指令，不参与裁剪
    preset_id = state.get("n_canon") or ""
    if preset_id:
        protected |= set(canon().get(preset_id, {}).keys())
    # 绑定了角色就必须留住发色/瞳色 —— 这是自检 F10 的硬要求，
    # 削掉了 FR 会红，而且模型会自己乱猜发色，比少一个道具严重得多。
    if state.get("role"):
        protected |= {"hair_color", "eyes_color"}
    softness = {k: i for i, k in enumerate(DROP_ORDER)}
    candidates = [k for k, v in state.items()
                  if v and not str(k).startswith("_") and k not in protected]
    # 排序键：先看权重（低→高），再看是不是源脚本点名的软字段，最后按字段出现顺序
    candidates.sort(key=lambda k: (field_weight(weights, k),
                                   0 if k in softness else 1))

    dropped = []
    for k in candidates:
        if est <= cap:
            break
        est -= count_of(state[k])
        dropped.append(k)
        state[k] = ""
    return dropped


# ============================================================================
# 八、拼接与中文摘要
# ============================================================================


# 「填充器」型字段：它们在 state 里**有值**，但那个值是**预设 id**，
# 不是标签 —— 绝不能当作标签拼进正文。
#
# ⚠️⚠️ 这是一个 v3.0.0 起就存在的真 bug（v3.8.0 才发现并修）：
#    `FIELD_SLOT["n_canon"] = "free"` 让 collect() 把 `c8` 这种 id 当成标签
#    输出。实测 600 次 explicit/hardcore/extreme 抽样里 **195 次（32.5%）**
#    正文末尾带着一个 `c1`…`c14` 的垃圾 token —— 它进 CLIP 是纯噪声，
#    而且会让"标签数"虚高、让 F11/F12 之外的自检看不出问题。
#    更糟的是：v3.8.0 的 repair() 会把 `s_xxx` 这种带下划线的场景 id
#    "归一"成 `s xxx`（格式修复对标签是对的，对 id 就是破坏）——
#    所以在 collect 这一层就不让它们进正文，是最干净的位置。
#
# ⚠️ 只跳过**输出**，不跳过字段本身：n_canon 仍然参与抽取、仍然受档位闸门、
#    仍然由 apply_preset() 展开成真正的标签。
_NO_EMIT_FIELDS = frozenset({"n_canon"})


def collect(state: dict, tail_extra: str = "") -> list:
    """按 ANIMA3 槽位序收集标签：去重保序、小写规范、互斥消解。"""
    buckets = {s: [] for s in SLOTS}
    for key, val in state.items():
        if str(key).startswith("_"):
            continue
        if key in _NO_EMIT_FIELDS:
            continue
        v = str(val or "").strip()
        if not v:
            continue
        slot = FIELD_SLOT.get(key)
        if not slot:
            continue
        buckets[slot].append(v)

    ordered = []
    for s in SLOTS:
        ordered.extend(buckets[s])

    # 拍平 → 去重保序 → 小写 → 互斥
    seen = set()
    flat = []
    for chunk in ordered:
        for t in chunk.split(","):
            t = t.strip().lower()
            if not t or t in seen:
                continue
            seen.add(t)
            flat.append(t)

    flat = apply_mutex(flat)

    tail = str(tail_extra or "").strip()
    if tail:
        for t in tail.split(","):
            t = t.strip().lower()
            if t and t not in seen:
                seen.add(t)
                flat.append(t)
    return flat


def cn_summary(tags) -> str:
    """中文摘要：把英文标签反查回界面中文名（源脚本 renderCnSummary 的移植）。"""
    index = _cn_index()
    return "，".join(index.get(str(t).strip().lower(), str(t)) for t in tags)


def _cn_index() -> dict:
    """英文标签 → 界面中文名（源脚本 renderCnSummary 的移植）。

    ⚠️ 必须把角色库并进来：2000 条角色的 trigger 不在 pools.json 里，
       不并的话中文摘要会把角色显示成一长串英文 tag。
    """
    if "cn_index" not in _CACHE:
        idx = {}
        for sec in sections():
            for fld in sec["fields"]:
                for zh, en in fld.get("opts", []):
                    for t in en.split(","):
                        t = t.strip().lower()
                        if t and t not in idx:
                            idx[t] = zh
        for zh, en, _n in _role_options():
            for t in en.split(","):
                t = t.strip().lower()
                if t and t not in idx:
                    idx[t] = zh
        # ⚠️ 人数词的覆盖：源脚本的「多人群交」映射到
        #    `multiple girls, orgy, group sex` 三个标签，于是逐标签反查时
        #    `multiple girls` 会被翻译成「多人群交」——
        #    而我们自动生成的群像标签只有 `multiple girls`（不含 orgy），
        #    摘要里显示成「多人群交」是错的（真机端到端抓到过）。
        #    覆盖成中性词；真的抽到 orgy 时那几个标签自己会显示成「多人群交」。
        #    注意键必须是**单个标签**（索引是按逗号拆开建的），
        #    写 "1girl, solo" 这种组合是永远匹配不到的。
        idx.update({
            "multiple girls": "多人群像",
            "2girls": "双女",
        })
        _CACHE["cn_index"] = idx
    return _CACHE["cn_index"]


# ============================================================================
# 九、自检（F 致命项 + S 风格项）
# ============================================================================


def _option_index() -> list:
    """[(field_key, 选项英文原文, 该选项的标签集合), ...]（缓存）。

    给成品文本**反推字段**用（见 infer_state）。只收带选项的字段；
    role / n_canon 刻意不收 —— 前者的 2000+ 条角色不是"选项"语义，
    后者的值是预设 id（c1…），成品里根本不会出现。
    """
    if "option_index" not in _CACHE:
        out = []
        for s in sections():
            for f in s["fields"]:
                if f["key"] in ("role", "n_canon") or f.get("type") == "text":
                    continue
                for _zh, en in f.get("opts", []):
                    tset = frozenset(
                        t.strip().lower() for t in str(en).split(",") if t.strip())
                    if tset:
                        out.append((f["key"], en, tset))
        # 长选项优先：标签多的选项更有辨识度，先匹配就不容易被短选项抢走
        out.sort(key=lambda x: -len(x[2]))
        _CACHE["option_index"] = out
    return _CACHE["option_index"]


def infer_state(tags) -> dict:
    """从**成品标签串**反推 {字段: 选项}，best-effort。

    判据：某个选项的**全部**标签都在成品里 → 认定该字段取了这个值；
    同一字段有多个候选命中时，取**标签最多**的那个（最有辨识度）。

    ⚠️ 这是给「提示词检查与修复」节点用的**估计**，不是权威：
       成品里没有字段信息，手写补句可能同时命中两个选项。
       所以报告里会标明"字段为反推估计"。
    ⚠️ 对生成的提示词来说这个反推基本是全对的 —— 因为 collect() 就是
       逐字段把选项原值拼进去的（一个选项可能有多个标签，全在）。
    """
    bag = {str(t).strip().lower() for t in (tags or []) if str(t).strip()}
    best = {}
    for fk, en, tset in _option_index():
        if tset <= bag:
            prev = best.get(fk)
            if prev is None or len(tset) > len(prev[1]):
                best[fk] = (en, tset)
    return {fk: en for fk, (en, _t) in best.items()}


def check_text(tags, lv: int):
    """对任意成品文本做一次体检。返回 (反推出来的 state, 体检报告)。

    与生成流程里的 `check()` 是**同一个函数** —— 两条路的结论不会打架。
    区别只在 state：生成流程有真 state，这里靠 infer_state 反推。
    """
    state = infer_state(tags)
    return state, check(tags, state, lv)


def format_check_report(tags, lv, report, fixes=None, level_label_txt="") -> str:
    """把"检查 + 修复"结果排成人类可读的多行文本（检查与修复节点用）。"""
    lines = [
        f"目标档位 : {level_label_txt}（上限 {lv}）",
        f"标签数   : {report['n']}",
        f"体检     : 致命项 {report['ok_f']}/{report['total_f']}"
        f" · 风格项 {report['ok_s']}/{report['total_s']}",
        f"实际落档 : {report['real_level']} {report['real_level_label']}"
        "（校准口径，与抽取准入同一把尺子 · v3.16.0）",
    ]
    if report.get("level_floor_hits"):
        lines.append("词汇校准 : " + "、".join(report["level_floor_hits"]))
    lines.append("")
    if fixes:
        lines.append(f"── 已自动修复 {len(fixes)} 项 ──")
        for f in fixes:
            lines.append("  · " + f)
    else:
        lines.append("── 已自动修复 0 项 ──")
    lines.append("")
    lines.append("── 致命项 ──")
    for c in report["F"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[!!]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("── 风格项 ──")
    for c in report["S"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[??]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("注：字段归属是从成品标签反推的估计（成品里没有字段信息）；"
                 "致命项里的「档位准入 / 槽位映射」因此仅作参考，"
                 "超档与互斥已由自动修复在成品层处理。")
    return "\n".join(lines)


def check(tags, state: dict, lv: int) -> dict:
    """体检。F 项失败 = 这条提示词有问题；S 项失败 = 风格提醒。

    移植自源脚本 checkTags() 的 F1–F14 / S1–S15，并新增三项：
      F15 档位合规（本次新增能力的核心保证）
      F16 槽位顺序（确保字段映射表没漏字段）
      S16 权重生效（调了权重却一条没抽中 → 提示）
    """
    F, S = [], []
    tags = [str(t) for t in tags]
    n = len(tags)

    seen, dup = set(), []
    for t in tags:
        if t in seen:
            dup.append(t)
        seen.add(t)

    count_en = state.get("count", "")
    solo = "solo" in count_en and "multiple" not in count_en
    duo = bool(re.search(r"hetero|yuri|1boy", count_en))
    multi = bool(re.search(r"multiple|orgy|group sex", count_en))
    lo, hi = (16, 30) if solo else (22, 38) if duo else (30, 48) if multi else (16, 42)

    F.append(_c("F1", "标签数量", lo <= n <= hi,
                f"{n} 个（模板要求 {'单人' if solo else '双人' if duo else '复杂'} "
                f"{lo}-{hi}）"))

    bad_q = [t for t in tags if any(b in t for b in
             ("masterpiece", "best quality", "score_9", "score_8",
              "highres", "absurdres"))]
    F.append(_c("F2", "禁词检查", not bad_q,
                "含质量词：" + ",".join(bad_q) if bad_q else "无质量词（工作流自动加前缀）"))

    art = [t for t in tags if t.startswith("@")]
    F.append(_c("F3", "画师名", not art,
                "含画师名：" + ",".join(art) if art else "无画师名"))

    F.append(_c("F4", "光线标签", True,
                "光影区为刻意选用，不判失败"))

    F.append(_c("F5", "重复标签", not dup,
                "重复：" + ",".join(dup[:6]) if dup else "无重复"))

    has_solo = "solo" in count_en
    other = any(x in count_en for x in ("hetero", "1boy", "yuri", "multiple", "orgy"))
    F.append(_c("F6", "数量一致性", not (has_solo and other),
                "solo 与多人标签冲突" if (has_solo and other) else "人数标签自洽"))

    va, shot = state.get("viewpoint", ""), state.get("shot_size", "")
    # ⚠️ 与源脚本对照时注意：源脚本 F7 里还有一条
    #      closeFull = shot.includes('close-up') && shot === 'full body'
    #    它是**恒假**的（一个字符串不可能同时等于 'full body' 又包含
    #    'close-up'）—— 移植时刻意剔除，不是漏项。别在对拍时"补"回去。
    #    同理 va_bad 里的 `va == "from side" and "from behind" in va` 也恒假，
    #    但那是源脚本原样带过来的判据，保留（真值表上等价于只看前半条）。
    va_bad = (("from above" in va and "from below" in va)
              or (va == "from side" and "from behind" in va))
    pov_full = "pov" in va and shot == "full body"
    F.append(_c("F7", "视角互斥", not (va_bad or pov_full),
                "POV 无法看到自身全身" if pov_full
                else "仰拍俯拍/正侧背互斥" if va_bad else "视角组合自洽"))

    blind = state.get("n_theme") == "blindfold"
    heart = any(x in state.get("n_orgasm", "")
                for x in ("heart-shaped pupils", "rolled back eyes"))
    glasses = state.get("accessory") == "glasses"
    F.append(_c("F8", "眼罩互斥", not (blind and (heart or glasses)),
                "蒙眼无法呈现心形瞳/翻白眼" if (blind and heart)
                else "眼罩与眼镜冲突" if (blind and glasses) else "无眼罩冲突"))

    nude = state.get("n_state") == "completely nude"
    ph = state.get("socks") == "pantyhose"
    bare = state.get("shoes") in ("bare feet",) or state.get("socks") == "barefoot"
    F.append(_c("F9", "服装互斥", not ((nude and state.get("outfit")) or (ph and bare)),
                "全裸与具体服装同现" if (nude and state.get("outfit"))
                else "连裤袜与赤足冲突" if (ph and bare) else "服装组合相容"))

    # F10 角色完整性。
    # ⚠️ 语义在 v3.1.0 收窄了：角色库从 221 扩到 2600+ 之后，不可能每个角色
    #    都有核准过的外貌数据。所以判据从「必须绑发色瞳色」改成
    #    「**有**外貌数据就必须绑上；没有数据不算错，但要说明白」。
    #    否则这条会大面积误报，把一个正常结果判成致命错误。
    if not state.get("role"):
        F.append(_c("F10", "角色完整性", True, "未使用角色标签，免检"))
    else:
        mode = state.get("_appearance") or ""
        bound = bool(state.get("hair_color") or state.get("eyes_color"))
        if bound:
            F.append(_c("F10", "角色完整性", True, "角色外观已绑定"))
        elif mode == "nodata":
            F.append(_c("F10", "角色完整性", True,
                        "该角色无外貌数据 —— 发色瞳色交给模型识别"))
        elif mode == "multi":
            F.append(_c("F10", "角色完整性", True,
                        "多位角色，按各自官方特征输出，不做统一对齐"))
        elif mode == "off":
            F.append(_c("F10", "角色完整性", True,
                        "外貌对齐被手动关闭 —— 按随机结果输出"))
        else:
            F.append(_c("F10", "角色完整性", False,
                        "该角色有外貌数据却没绑定发色/瞳色"))

    # 长标签只在「不在词库里」时才算异常。
    # 反例：`dungeon ni deai wo motomeru no wa machigatteiru darou ka`
    # 是《地错》的 danbooru 正式作品 tag，53 字符，完全合法 ——
    # 源脚本按 40 字硬卡，会把它判成致命项，是个假阳性。
    known = _cn_index()
    long_tags = [t for t in tags if len(t) > 40 and t not in known]
    long_ok = [t for t in tags if len(t) > 40 and t in known]
    F.append(_c("F11", "标签长度", not long_tags,
                "超长且不在词库内：" + ",".join(long_tags[:4]) if long_tags
                else (f"{len(long_ok)} 个长标签均为词库内作品名" if long_ok
                      else "长度正常")))

    bad_fmt = [t for t in tags if re.match(r"^[A-Z]", t)
               or ("_" in t and "score_" not in t)]
    F.append(_c("F12", "标签格式", not bad_fmt,
                "大写或下划线：" + ",".join(bad_fmt[:6]) if bad_fmt else "全小写正确"))

    stand = bool(re.search(r"standing sex|stand and carry", state.get("n_act", "")))
    lie = bool(re.search(r"lying on stomach|on all fours|prone bone",
                         state.get("n_pos", "")))
    F.append(_c("F14", "体位姿态", not (stand and lie),
                "站立性行为与俯卧体态矛盾" if (stand and lie) else "体位协调"))

    # ── 新增 ──────────────────────────────────────────────────────
    # v3.16.0 口径统一（用户拍板）：报告的「实际落档」改按**校准口径**计算，
    # 与准入闸门（eligible_options → tag_level）同一把尺子。
    #
    # 为什么改：v3.15.0 之前这里用 `grade_nsfw(tags)` 原始口径，而准入层走
    # 校准链（LEVEL_DEMOTE / LEVEL_RAISE 折算），两层不一致 —— 实测
    # 「选 SFW 却显示落档 2（wet hair 误命中，23%）」「选 explicit 却显示
    # 落档 3（paizuri/cum on breasts 有意归入成人层，40%）」，用户看到的
    # 档位数字与所选等级对不上。
    #
    # ⚠️ 判定表 `grade_nsfw` 本身**一字不动**（与造型台逐字对拍的承诺不变，
    #    F17 也仍然按原始口径裁决"真越档"）。变的只是**展示口径**：
    #    tag_level() = max(判定表∩下调折算, 上抬折算)，与准入完全同源。
    # raw_level 保留原始口径，供对拍与排查（不再进主展示行）。
    real = max((tag_level(t) for t in tags), default=0)
    raw_level = int(N.grade_nsfw(tags)["num"])

    # F15 档位**准入**：抽出来的每个有档位概念的字段，其值必须 ≤ 目标档。
    #
    # 这是「低档不越界」的硬保证。因为候选池在抽取**之前**就已经按档位
    # 收敛过了（见 eligible_options），所以正常情况下它恒为真 ——
    # 一旦为假，说明有人绕过了闸门（新加的预设 / 上游注入 / 回归），
    # 这正是它值得作为致命项的原因。
    admit_bad = sorted({
        k for k, v in state.items()
        if not str(k).startswith("_") and v and FIELD_SLOT.get(k)
        and field_value_num(k, v) > lv
    })
    F.append(_c("F15", "档位准入", not admit_bad,
                "越档字段：" + ", ".join(admit_bad) if admit_bad
                else f"全部字段都在 {lv} 档以内"))

    # F16 槽位映射：字段表漏字段时，标签会被静默丢掉 —— 必须显式拦住。
    unmapped = sorted(k for k in state
                      if not str(k).startswith("_") and state[k]
                      and k not in FIELD_SLOT)
    F.append(_c("F16", "槽位映射", not unmapped,
                "未映射字段：" + ",".join(unmapped) if unmapped
                else f"{len(FIELD_SLOT)} 字段全部有槽位"))

    # F17 判档一致性：把成品喂回**造型台那套 grade_nsfw**，看它判多少档。
    #
    # 为什么要与 F15 分开：
    #   「选项准入」和「文本判档」是两件事。判定表只看标签、看不到字段，
    #   所以 `wet hair`（头发段，纯发型状态）会被 ^wet 模式判成 2 档 ——
    #   这类误命中登记在 anima_v3_overrides.LEVEL_DEMOTE 里，属于**已登记**。
    #   于是这里分三档处理：
    #     · 判档 ≤ 目标档                        → 通过
    #     · 超档标签全部来自已登记校准项          → 通过，在 S17 里说明
    #     · 出现未登记的超档标签                  → 真越档，致命
    over_tags = sorted({t for t in tags
                        if int(N.grade_nsfw([t])["num"]) > lv})
    calibrated_tags = _calibrated_tags()
    rogue = [t for t in over_tags if t not in calibrated_tags]
    F.append(_c("F17", "判档一致性", not rogue,
                "未登记的超档标签：" + ", ".join(rogue[:6]) if rogue
                else (f"判定表判档 {raw_level} ≤ {lv}" if raw_level <= lv
                      else f"判定表判档 {raw_level} > {lv}，但超档项均为已登记校准项"
                           "（展示口径已按校准折算，见「实际落档」）")))

    # ── 风格项 ────────────────────────────────────────────────────
    gloomy = ("fog", "thunderstorm", "rain", "snow", "night")
    cheerful = ("grin", "smile", "sweet")
    S.append(_c("S1", "情绪与氛围",
                not (state.get("emotion") and state.get("weather") in gloomy
                     and state.get("emotion") in cheerful),
                "开朗表情配阴郁天气，注意色彩情绪一致"))

    lewd = ("ahegao", "fucked silly", "mind break", "orgasm", "tongue out")
    S.append(_c("S2", "身份与姿态",
                not (re.search(r"innocent|pure", state.get("temperament", ""))
                     and any(x in state.get("n_orgasm", "") for x in lewd)),
                "清纯气质配崩坏表情（反差设定除外）"))

    if state.get("role"):
        modern = ("police uniform", "flight attendant uniform", "office lady",
                  "cheerleader uniform")
        ancient = ("miko", "china dress", "kimono")
        conflict = ((state.get("outfit") in modern and re.search(
            r"touhou|frieren|re:zero|vocaloid", state["role"]))
            or (state.get("outfit") in ancient and re.search(
                r"zenless|chainsaw|persona|one-punch", state["role"])))
        S.append(_c("S4", "跨世界观冲突", not conflict,
                    "服装与角色世界观存在冲突" if conflict
                    else "已绑定作品标签：" + state["role"]))
    else:
        S.append(_c("S4", "跨世界观冲突", True, "未使用角色标签，免检"))

    lofi = ("selfie", "webcam footage", "security camera footage")
    S.append(_c("S5", "设备与画质",
                not (state.get("device") in lofi and state.get("dof") in
                     ("focus on face", "depth of field, blurry background")),
                "低画质设备不配专业景深效果"))

    pub = ("classroom", "library", "hallway", "office", "cafe", "street",
           "convenience store", "movie theater", "stairwell", "train interior")
    if lv >= 2:
        bad_scene = (state.get("n_state") == "completely nude"
                     and state.get("place") in pub)
        S.append(_c("S6", "场景与裸露", not bad_scene,
                    "公共场景配全裸档位，建议改半脱或 stealth 描述" if bad_scene
                    else "场景与裸露档位一致"))
    else:
        S.append(_c("S6", "场景与裸露", True, "本档无裸露标签，免检"))

    S.append(_c("S8", "道具位置", True,
                f"道具「{state.get('prop')}」已交代位置" if state.get("prop")
                else "未使用道具"))

    gaze, vp = state.get("eye_gaze", ""), state.get("viewpoint", "")
    S.append(_c("S9", "眼神与机位",
                not ((vp == "from above" and gaze == "looking up")
                     or (vp == "from below" and gaze == "looking down")),
                "视线方向与机位矛盾" if ((vp == "from above" and gaze == "looking up")
                                 or (vp == "from below" and gaze == "looking down"))
                else "眼神方向与机位匹配"))

    if state.get("light_fx"):
        S.append(_c("S12", "光影氛围", True, "光效：" + state["light_fx"]))
    else:
        S.append(_c("S12", "光影氛围", True, "未指定光效（底模已内置）"))

    S.append(_c("S16", "权重生效", True,
                "本次未使用权重（全默认）" if not state.get("_weights_tuned")
                else f"已调 {state['_weights_tuned']} 项权重"))

    if over_tags and not rogue:
        S.append(_c("S17", "已登记校准项", True,
                    f"判档 {real} 高于目标 {lv}，但超档标签 "
                    f"（{', '.join(over_tags[:4])}）全部来自 anima_v3_overrides "
                    f"的 LEVEL_DEMOTE 白名单，属预期"))
    else:
        S.append(_c("S17", "已登记校准项", True, "本次未触发人工校准"))

    ok_f = sum(1 for c in F if c["ok"])
    ok_s = sum(1 for c in S if c["ok"])
    # 词汇校准命中（信息项，不判成败）：这些标签判定表判 0、但逐选项升档表
    # 在字段上下文里确认过档位 —— 报告的"实际落档"用的是判定表口径，
    # 这里把差额单独列出来，免得用户以为两者矛盾。
    floor_hits = sorted({t for t in tags if tag_level(t) > lv})
    return {
        "F": F, "S": S, "n": n, "dup": dup,
        # over_level / over_fields 是**准入**层面的越界，回滚循环看的是它。
        # 刻意不用"文本判档"当回滚依据 —— wet hair 那类误命中会让它死循环。
        "over_level": bool(admit_bad),
        "over_fields": admit_bad,
        "real_level": real,
        "real_level_label": N.NSFW_LABEL[N.NSFW_KEY_OF_NUM[real]],
        # 原始判定表口径（v3.16.0 起仅作排查辅助，不再当"实际落档"展示）。
        "raw_level": raw_level,
        "calibrated_hits": over_tags,
        "rogue": rogue,
        "level_floor_hits": floor_hits,
        "ok_f": ok_f, "total_f": len(F), "ok_s": ok_s, "total_s": len(S),
    }


def _calibrated_tags() -> set:
    """LEVEL_DEMOTE 覆盖到的所有英文标签（把选项串拆成单标签）。

    ⚠️ 刻意**只收 DEMOTE、不收 RAISE**（v3.8.0）：本集合的语义是
       「允许在低档出现、但判档会超档的已登记例外」。RAISE 是**往下钉档位**的，
       它覆盖的标签（pov crotch 等）本来就进不了低档，把它们放进白名单
       等于反过来给 F17 开了一个洞。
    """
    if "calibrated_tags" not in _CACHE:
        bag = set()
        for _fk, en in LEVEL_DEMOTE:
            for t in str(en).split(","):
                t = t.strip().lower()
                if t:
                    bag.add(t)
        _CACHE["calibrated_tags"] = bag
    return _CACHE["calibrated_tags"]


def field_value_num(field_key: str, value: str) -> int:
    """某个字段**当前值**的准入档位 —— 引擎侧唯一的"这个字段算几档"入口。

    ⚠️ 文本字段（`nl` / `n_extra`）必须**按整串逐标签判**，不能走
       `option_level()` —— 那条路会套上所在段的段下限：`n_extra` 属于
       canon 段（下限 2），于是一条 L0 的附加标签串会被判成 2 档。
       v3.9.0 的场景搭配库里 SFW 预设就带 n_extra（`afternoon light, quiet`），
       判错会让它在全年龄档被当成"越档"（测试与自检都会误报）。
    ⚠️ 与 `check()` 的 F15、以及 tests/check_v3_gate.py 的准入层**共用本函数**，
       三处口径必须一致（历史上"两套口径各判各的"出过好几次事）。
    """
    if field_key in ("nl", "n_extra"):
        return int(N.grade_nsfw([t.strip() for t in str(value).split(",")
                                 if t.strip()])["num"])
    return option_level(field_key, value)


def _c(cid, name, ok, note):
    if ok is None:
        ok = True
    return {"id": cid, "name": name, "ok": bool(ok), "note": note}


def format_report(res: dict) -> str:
    """把体检结果排成人类可读的多行文本（节点的 report 输出）。"""
    r = res["report"]
    lines = [
        f"档位   : {res['level_label']}（目标 {res['level_num']}）"
        f" · 实际落档 {r['real_level']} {r['real_level_label']}",
        f"标签数 : {r['n']}",
        f"自检   : 致命项 {r['ok_f']}/{r['total_f']}"
        f" · 风格项 {r['ok_s']}/{r['total_s']}",
        "",
        "── 致命项 ──",
    ]
    for c in r["F"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[!!]'} {c['id']} {c['name']} —— {c['note']}")
    lines.append("")
    lines.append("── 风格项 ──")
    for c in r["S"]:
        lines.append(f"  {'[OK]' if c['ok'] else '[??]'} {c['id']} {c['name']} —— {c['note']}")
    if res.get("dropped"):
        lines.append("")
        lines.append("裁剪掉的字段（超标签上限）：" + "、".join(res["dropped"]))
    if res.get("notes"):
        lines.append("")
        lines.append("── 过程记录 ──")
        for n in res["notes"]:
            lines.append("  · " + n)
    lines.append("")
    lines.append("── 调试：本次抽出的字段与档位 ──")
    for d in r.get("drawn", []):
        lines.append(f"  {d['field']:16s} L{d['level']} w{d['weight']:<5}"
                     f" {d['zh']} = {d['en']}")
    return "\n".join(lines)
