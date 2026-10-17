# -*- coding: utf-8 -*-
"""场景搭配预设库：**校验 + 派生档位**，产出 presets/anima_v3_scenes.json。

为什么要一个脚本而不是手写 JSON：
  1. 每个预设的 `level` 必须由**内容**算出来（取所有标签里最高档），
     手写必然漂移 —— 而它决定"这个搭配在哪些档位可用"，写错就等于闸门漏；
  2. `tags` 里的字段名 / 选项原文必须**真实存在**，写错一个词就是静默失效
     （value 不匹配任何选项 → apply_preset 里的 field_by_key 找不到 →
      那个字段悄悄不生效，而且不报错）；
  3. 与「词库零漂移」同一套纪律：派生数据由脚本算，JSON 只存**人写的内容**。

用法：
    python tools/build_scenes.py            # 校验 + 回写（幂等）
    python tools/build_scenes.py --check    # 只校验，不写（测试用）
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

# ⚠️ 不能直接 `from anima_v3_rules import ...` —— 那个模块里有包内相对导入
#    （`from . import anima_v3_nsfw`），裸脚本运行会 ImportError:
#    "attempted relative import with no known parent package"。
#    本仓库的统一做法是复用 tests/_boot.py 造一个"只有 __path__ 的空包"
#    再 import_module（见 tools/dump_ui_fixtures.py）。
sys.path.insert(0, os.path.join(PLUGIN, "tests"))

import _boot  # noqa: E402

_R = _boot.load("anima_v3_rules")
field_by_key = _R.field_by_key
option_level = _R.option_level
tag_level = _R.tag_level

SCENES = os.path.join(PLUGIN, "presets", "anima_v3_scenes.json")


def scene_level(tags: dict, extra: str) -> int:
    """预设档位 = max(每个字段值的准入档位, extra 里每个标签的档位)。

    ⚠️ 用 `option_level`（含段下限与逐选项上抬）而不是裸的 grade_nsfw：
       那正是 `apply_preset` 写入时用的口径（`_field_ok_under`），
       两边必须同一个数，否则会出现「预设被放进了候选池、写的时候却被挡」
       这种半吊子状态。
    """
    lv = 0
    for k, v in (tags or {}).items():
        lv = max(lv, int(option_level(k, v)))
    for t in str(extra or "").split(","):
        t = t.strip().lower()
        if t:
            lv = max(lv, int(tag_level(t)))
    return lv


def validate(data: dict) -> list:
    """返回错误列表（空 = 全部合法）。"""
    errs = []
    ids = set()
    groups = {g["id"] for g in data.get("groups", [])}
    for i, sc in enumerate(data.get("scenes", [])):
        sid = str(sc.get("id") or "")
        where = f"scenes[{i}] {sid or '(缺 id)'}"
        if not sid:
            errs.append(f"{where}: 缺 id")
        elif not sid.startswith("sc_"):
            errs.append(f"{where}: id 必须以 sc_ 开头（与法典 c1..c14 区分）")
        elif sid in ids:
            errs.append(f"{where}: id 重复")
        else:
            ids.add(sid)
        if not str(sc.get("zh") or "").strip():
            errs.append(f"{where}: 缺中文名 zh")
        if sc.get("group") not in groups:
            errs.append(f"{where}: group {sc.get('group')!r} 不在 groups 里")
        tags = sc.get("tags")
        if not isinstance(tags, dict) or not tags:
            errs.append(f"{where}: tags 必须是非空对象")
            continue
        # ⚠️ 人数与角色是"地基"，预设不得代填（见 _meta.rules）
        for forbidden in ("count", "role"):
            if forbidden in tags:
                errs.append(f"{where}: 不得设置 {forbidden}（人数/角色由用户或自动抽取负责）")
        for k, v in tags.items():
            _sec, fld = field_by_key(k)
            if not fld:
                errs.append(f"{where}: 字段 {k!r} 不存在")
                continue
            opts = [en for _zh, en in fld.get("opts", [])]
            if v not in opts:
                errs.append(f"{where}: 字段 {k} 的取值 {v!r} 不是合法选项")
    return errs


def main():
    check_only = "--check" in sys.argv
    with open(SCENES, "r", encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)

    errs = validate(data)
    if errs:
        print(f"[build_scenes] 校验失败 {len(errs)} 条：")
        for e in errs:
            print("  · " + e)
        return 1

    # 派生 level（并核对：预设 id 不能与法典预设撞名）
    # ⚠️ 必须比 `pools['canon']`（**法典原文**），不能比 `R.canon()` ——
    #    后者 v3.9.0 起已经把场景库合并进去了，拿它比对等于"自己跟自己撞名"。
    canon_ids = set(_R.pools().get("canon", {}).keys())
    changed = 0
    for sc in data["scenes"]:
        if sc["id"] in canon_ids:
            print(f"[build_scenes] 预设 id 与法典撞名：{sc['id']}")
            return 1
        lv = scene_level(sc["tags"], sc.get("extra", ""))
        if int(sc.get("level", -1)) != lv:
            sc["level"] = lv
            changed += 1

    scenes = data["scenes"]
    data["_meta"]["counters"] = {
        "scenes": len(scenes),
        "groups": len(data.get("groups", [])),
        "by_group": {g["id"]: sum(1 for s in scenes if s["group"] == g["id"])
                     for g in data.get("groups", [])},
        "by_level": {str(n): sum(1 for s in scenes if s["level"] == n)
                     for n in range(5)},
    }
    data["_meta"]["levels_note"] = (
        "level 由 tools/build_scenes.py 按内容派生，勿手改；"
        "= max(各字段值的准入档位, extra 各标签档位)")

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if check_only:
        if payload != raw:
            print("[build_scenes] --check 失败：磁盘 JSON 与派生结果不一致"
                  "（跑一次 python tools/build_scenes.py 重写）")
            return 1
        print(f"[build_scenes] --check 通过：{len(scenes)} 个预设，档位分布 "
              + " ".join(f"L{k}:{v}" for k, v in data["_meta"]["counters"]["by_level"].items()))
        return 0

    if payload != raw:
        with open(SCENES, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)

    print(f"[build_scenes] 已写出 {SCENES}"
          + (f"（派生档位更新 {changed} 条）" if changed else "（内容无变化）"))
    print(f"  预设 {len(scenes)} 个 · 分组 "
          + " / ".join(f"{k}={v}" for k, v in data["_meta"]["counters"]["by_group"].items()))
    print("  档位分布：" + "  ".join(
        f"L{k}={v}" for k, v in sorted(data["_meta"]["counters"]["by_level"].items())))
    print()
    print(f"  {'id':32s} {'档':>2s}  {'组':4s} 中文名")
    for sc in scenes:
        print(f"  {sc['id']:32s} L{sc['level']:<2d} {sc['group']:4s} {sc['zh']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
