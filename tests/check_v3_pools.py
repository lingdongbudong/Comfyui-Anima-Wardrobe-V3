# -*- coding: utf-8 -*-
"""词库零漂移 + 档位表一致性。

两件事：
  ① 拿磁盘上的 presets/anima_v3_pools.json 与源 HTML 重新导出一遍比对。
     源 HTML 改了词库而 JSON 没重导 → 立刻红（这正是"词库唯一真源是
     那份 HTML"这条纪律的机器化）。
  ② 档位表与词库对得上：选项数一致、每个选项都有档位、
     section_floor 里声明的段下限确实生效了。

跑法：python tests/check_v3_pools.py
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _boot  # noqa: E402

POOLS = os.path.join(PLUGIN, "presets", "anima_v3_pools.json")
LEVELS = os.path.join(PLUGIN, "presets", "anima_v3_levels.json")
NODE = shutil.which("node") or os.environ.get("AV3_NODE", "node")

# 基线计数：改词库就会变，是**故意**让它变红好提醒同步
# v3.8.0：视角字段新增 10 条第一人称 / FOV 词条 → 744 → 754
# v3.12.0：服装扩充 113 条 + 新增「上装与外套 top」「下装 bottom」两个字段
#          → 字段 54 → 56，选项 754 → 867
# v3.14.0：姿势扩充 + 新增「暗示姿势 pose_risque」字段（L1–L2 专属）
#          + 修 21 项无效/别名 tag → 字段 56 → 57，选项 867 → 913
# v3.15.0：场景段大幅扩充（+76）；表情段重做 —— 删掉 danbooru 里
#          没有对应概念的「脸型 face_shape」「气质 temperament」两个字段，
#          并按实测有效写法重写 emotion / eye_gaze / mouth
#          → 字段 57 → 55，选项 913 → 983
# v3.15.1：基础姿势 pose_base 末尾追加「站立一字马 standing split」（danbooru
#          有效 tag，post_count 13779，判定 0 档 SFW）→ 选项 983 → 984
# v3.17.0：胸部大小等级 —— figure 末尾追加「平胸 flat chest / 中等胸部
#          medium breasts / 爆乳 gigantic breasts」（giant_breasts 是 0 帖
#          无效 tag）→ 选项 984 → 987
EXPECT = {"sections": 15, "fields": 55, "options": 987, "roles": 222,
          "appear": 221, "canon": 14}


def main():
    fails = []

    if not os.path.exists(POOLS) or not os.path.exists(LEVELS):
        print("[check_v3_pools] 缺少 presets/*.json，请先跑：\n"
              "  node tools/export_v3_pools.mjs\n"
              "  python tools/classify_v3_levels.py")
        return 2

    # ── ① 与源 HTML 对拍 ──────────────────────────────────────────
    exporter = os.path.join(PLUGIN, "tools", "export_v3_pools.mjs")
    try:
        r = subprocess.run([NODE, exporter, "--check"], capture_output=True,
                           text=True, timeout=120)
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr).strip().replace("\n", " | ")
    except Exception as exc:
        ok = False
        detail = f"执行失败：{exc}"
    if ok:
        print(f"  [OK] 零漂移：磁盘 JSON 与源 HTML 一致（{detail}）")
    else:
        print(f"  [!!] 零漂移失败：{detail}")
        fails.append("零漂移")

    # ── ② 档位表与词库一致 ────────────────────────────────────────
    pools = json.load(open(POOLS, encoding="utf-8"))
    levels = json.load(open(LEVELS, encoding="utf-8"))
    counters = pools["_meta"]["counters"]

    for k, want in EXPECT.items():
        got = counters.get(k)
        if got != want:
            print(f"  [!!] 计数 {k} = {got}，基线是 {want} —— 词库变了？")
            fails.append(f"计数 {k}")
    if not any("计数" in f for f in fails):
        print(f"  [OK] 计数与基线一致：{counters}")

    # 每个有选项的字段都要在档位表里
    missing = []
    for s in pools["sections"]:
        for f in s["fields"]:
            if f.get("opts") and f["key"] not in levels["option_levels"]:
                missing.append(f["key"])
    if missing:
        print(f"  [!!] 这些字段没有档位：{missing}")
        fails.append("字段缺档位")
    else:
        print("  [OK] 所有带选项的字段都有档位")

    # 档位表里出现的字段必须仍然存在于词库（词库删字段后档位表没重算）
    orphan = [k for k in levels["option_levels"]
              if k not in {f["key"] for s in pools["sections"] for f in s["fields"]}]
    if orphan:
        print(f"  [!!] 档位表里有词库已不存在的字段：{orphan}")
        fails.append("档位表残留字段")
    else:
        print("  [OK] 档位表无残留字段")

    # 段下限必须真生效：NSFW 段里不应有低于下限的选项
    R = _boot.load("anima_v3_rules")
    floors = levels.get("section_floor", {})
    bad_floor = []
    for sid, fl in floors.items():
        if not fl:
            continue
        sec = R.section_by_id(sid)
        for f in sec["fields"]:
            for zh, en in f.get("opts", []):
                if R.option_level(f["key"], en) < fl:
                    bad_floor.append(f"{sid}/{f['key']}/{en}")
    if bad_floor:
        print(f"  [!!] 段下限没生效：{bad_floor[:5]}")
        fails.append("段下限")
    else:
        print(f"  [OK] 段下限生效：{ {k: v for k, v in floors.items() if v} }")

    # 注释量：源脚本 744 条里有多少落进 NSFW 档（用于报告，不作断言）
    from collections import Counter
    c = Counter()
    for s in pools["sections"]:
        for f in s["fields"]:
            for _zh, en in f.get("opts", []):
                c[R.option_level(f["key"], en)] += 1
    print(f"  档位分布（宽/擦/成/过/极）："
          f"{c[0]} / {c[1]} / {c[2]} / {c[3]} / {c[4]}")

    print()
    if fails:
        print(f"[check_v3_pools] 失败 {len(fails)} 项：{fails}")
        return 1
    print("[check_v3_pools] 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
