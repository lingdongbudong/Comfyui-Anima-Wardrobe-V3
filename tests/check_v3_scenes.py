# -*- coding: utf-8 -*-
"""场景搭配预设库基线（v3.9.0）。

「一键设置场景搭配」= 把 姿势/场景/表情/动作 的固定组合一次套用，套用到的
字段不参与随机。它复用**法典场景那一套机制**（`canon()` 合并 → `apply_preset`
填充 → 档位/权重/锁定三闸门），所以这组基线要钉住的不只是"数据文件合法"，
而是**这条复用链路真的按预期工作**：

  ① 数据层   —— build_scenes --check 通过（派生档位与磁盘一致）、字段/选项真实存在、
                不得写 count/role、id 不与法典撞名。
  ② 合并层   —— canon() = 法典 14 组 + 场景 N 条；法典条目逐字节不变。
  ③ 闸门层   —— 按档位可用性正确（L0 抽不到 L2+ 的场景）；
                强制指定超档场景时 F15 必须报出来。
  ④ 应用层   —— 预设字段真的被写进 state；**被 trim 裁不掉**（"真固定"）。
  ⑤ 输出层   —— 预设 id **不出现在正文里**；同 seed 逐字节可复现。
  ⑥ 载荷层   —— /pools 的 n_canon 候选数与引擎一致；/scenes 的字段名有中文标签。

跑法：python tests/check_v3_scenes.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _boot  # noqa: E402

R = _boot.load("anima_v3_rules")
BUILD = os.path.join(PLUGIN, "tools", "build_scenes.py")

FAILS = []


def ck(name, cond, note=""):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [!!] {name}  {note}")
        FAILS.append(name)


def main():
    print("── ① 数据层：构建脚本校验 + 结构 ──")
    r = subprocess.run([sys.executable, BUILD, "--check"],
                       capture_output=True, text=True, cwd=PLUGIN)
    ck("build_scenes --check 通过（派生档位与磁盘一致）", r.returncode == 0,
       (r.stdout + r.stderr).strip().replace("\n", " | ")[:200])

    scenes = R.scene_list()
    ck("场景库非空", len(scenes) > 0, str(len(scenes)))
    ids = [s["id"] for s in scenes]
    ck("id 唯一", len(set(ids)) == len(ids))
    ck("id 一律以 sc_ 开头（与法典 c1..c14 区分）",
       all(i.startswith("sc_") for i in ids))
    canon_ids = set(R.pools().get("canon", {}).keys())
    ck("id 不与法典预设撞名", not (set(ids) & canon_ids),
       str(sorted(set(ids) & canon_ids)))

    forbidden = [(s["id"], k) for s in scenes for k in ("count", "role")
                 if k in (s.get("tags") or {})]
    ck("没有预设去写 count / role（人数与角色是地基）", not forbidden,
       str(forbidden[:3]))

    bad = []
    for s in scenes:
        for k, v in (s.get("tags") or {}).items():
            _sec, fld = R.field_by_key(k)
            if not fld or v not in [en for _zh, en in fld.get("opts", [])]:
                bad.append((s["id"], k, v))
    ck("每条预设的字段与取值都真实存在（写错=静默失效）", not bad, str(bad[:3]))

    by_group = {}
    for s in scenes:
        by_group[s["group"]] = by_group.get(s["group"], 0) + 1
    ck("分组齐全（sfw / nsfw 都有）",
       {"sfw", "nsfw"} <= set(by_group), str(by_group))

    lv0 = [s["id"] for s in scenes if int(s["level"]) == 0]
    ck("存在全年龄档（L0）场景 —— SFW 一键搭配可用", len(lv0) >= 5,
       f"{len(lv0)} 条")

    print("── ② 合并层：canon = 法典 + 场景 ──")
    merged = R.canon()
    ck("合并后条数 = 法典 14 + 场景 N",
       len(merged) == 14 + len(scenes), f"{len(merged)} vs {14 + len(scenes)}")
    ck("法典条目原样保留（c8 内容不变）",
       merged.get("c8", {}).get("n_act") == "deepthroat",
       str(merged.get("c8")))
    ck("场景 id 能取到 `{字段: 值}` 形状的预设",
       isinstance(merged.get(lv0[0]), dict) and bool(merged.get(lv0[0])),
       str(merged.get(lv0[0]))[:80])
    ck("场景预设的 n_extra 挂在同一键上（与法典同形状）",
       "n_extra" in (merged.get(lv0[0]) or {}))

    print("── ③ 闸门层：按档位可用性 ──")
    for lv in (0, 2, 3, 4):
        got = {R.canon_level(en) for _zh, en, _n in
               R.eligible_options("n_canon", lv) if en.startswith("sc_")}
        ck(f"L{lv} 的场景候选档位全部 ≤ {lv}", all(g <= lv for g in got),
           str(sorted(got)))
    ck("L0 候选中不含 L2+ 场景",
       not [en for _z, en, _n in R.eligible_options("n_canon", 0)
            if en.startswith("sc_") and R.canon_level(en) > 0])
    ck("高档（L4）能拿到全部场景",
       len([en for _z, en, _n in R.eligible_options("n_canon", 4)
            if en.startswith("sc_")]) == len(scenes))

    # 文本框（n_extra）必须**按标签**判档，不能套 canon 段的段下限（=2）——
    # 否则 SFW 场景自带的附加标签串（`afternoon light, quiet`）会被判成 2 档，
    # 在闸门测试与自检里假报"越档"。
    ck("n_extra 按标签判档（不套段下限）",
       R.field_value_num("n_extra", "afternoon light, quiet") == 0,
       str(R.field_value_num("n_extra", "afternoon light, quiet")))

    # 强制指定超档场景 → F15（档位准入）必须报出来
    hi = [s["id"] for s in scenes if int(s["level"]) >= 4]
    if hi:
        rr = R.draw(level=0, seed=5, forced={"n_canon": hi[0]}, base_prob=0.0)
        ck("强制超档场景 → F15 报越档", "n_canon" in rr["report"]["over_fields"],
           str(rr["report"]["over_fields"]))
    else:
        ck("强制超档场景 → F15 报越档", False, "库里没有 L4 场景，无法验证")

    print("── ④ 应用层：预设真被写进去，且裁不掉（真固定）──")
    sid = "sc_rain_street_lookback" if "sc_rain_street_lookback" in merged \
        else lv0[0]
    want = {k: v for k, v in merged[sid].items() if k != "n_extra"}
    res = R.draw(level=0, seed=9, forced={"n_canon": sid}, base_prob=0.0)
    st = res["state"]
    missing = {k: v for k, v in want.items() if st.get(k) != v}
    ck(f"场景「{sid}」的字段全部写入 state", not missing, str(missing))
    ck("n_extra 也写进去了", bool(st.get("n_extra")))

    # trim：把上限压到 16，场景字段必须活下来（这正是"真固定"的判据）
    res2 = R.draw(level=0, seed=9, forced={"n_canon": sid},
                  base_prob=1.0, tag_cap="16")
    st2 = res2["state"]
    lost = [k for k in want if not st2.get(k)]
    ck("标签上限压到 16 后，场景字段一个都没被裁掉", not lost,
       f"被裁：{lost}；dropped={res2['dropped']}")
    ck("被裁的只可能是非预设字段", not (set(res2["dropped"] or []) & set(want)),
       str(res2["dropped"]))

    print("── ⑤ 输出层：id 不进正文 + 可复现 ──")
    ck("预设 id 不出现在正文里",
       sid not in res["text"] and not any(
           t.startswith("sc_") for t in res["tags"]), res["text"][:80])
    ck("法典 id（c8）也不出现在正文里",
       not any((t.startswith("c") and t[1:].isdigit()) for t in res["tags"]))
    a = R.draw(level=0, seed=42, forced={"n_canon": sid})["text"]
    b = R.draw(level=0, seed=42, forced={"n_canon": sid})["text"]
    ck("同 seed 逐字节可复现", a == b)
    ck("L0 场景输出的自检全绿",
       res["report"]["ok_f"] == res["report"]["total_f"],
       str([c["id"] for c in res["report"]["F"] if not c["ok"]]))

    print("── ⑥ 载荷层：面板与引擎同口径 ──")
    nodes = _boot.load("nodes")
    pools = nodes.pools_payload()
    ncanon = None
    for s in pools["sections"]:
        for f in s["fields"]:
            if f["key"] == "n_canon":
                ncanon = f
    ck("/pools 里有 n_canon 字段", ncanon is not None)
    # ⚠️ 面板列出的候选数必须与引擎实际能抽到的同源候选**完全一致**
    #    （历史教训：预演 A、实跑 B）
    panel_ids = [o["en"] for o in (ncanon or {}).get("opts", [])]
    engine_ids = [en for _zh, en, _n in R.eligible_options("n_canon", 4)]
    ck("/pools 的 n_canon 候选 == 引擎候选（L4）",
       panel_ids == engine_ids, f"{len(panel_ids)} vs {len(engine_ids)}")
    ck("面板候选带上了场景条目",
       sum(1 for i in panel_ids if i.startswith("sc_")) == len(scenes))

    sc_payload = nodes.scenes_payload()
    ck("/scenes 载荷含全部分组与场景",
       sc_payload["counters"]["scenes"] == len(scenes)
       and bool(sc_payload["groups"]), str(sc_payload["counters"]))
    # v3.10.0：一级分组是 **NSFW 档位**（L0–L4），不是 sfw/nsfw
    ck("载荷带五个档位分组", len(sc_payload["level_groups"]) == 5,
       str([g["title"] for g in sc_payload["level_groups"]]))
    ck("五个档位分组的条数之和 == 场景总数",
       sum(g["count"] for g in sc_payload["level_groups"]) == len(scenes),
       str([(g["title"], g["count"]) for g in sc_payload["level_groups"]]))
    ck("每一档都有场景（分类展示不留空组）",
       all(g["count"] > 0 for g in sc_payload["level_groups"]),
       str([(g["title"], g["count"]) for g in sc_payload["level_groups"]]))
    ck("每条场景都带题材标签（面板卡片要显示）",
       all(s.get("theme") for s in sc_payload["scenes"]),
       str([s["id"] for s in sc_payload["scenes"] if not s.get("theme")][:3]))
    ck("库规模已扩到 60 条以上", len(scenes) >= 60, str(len(scenes)))
    no_lab = [f for sc in sc_payload["scenes"] for f in sc["fields"]
              if not f.get("lab") or f.get("lab") == f.get("field")]
    ck("每个字段都带中文标签（面板直接渲染）", not no_lab, str(no_lab[:3]))

    print("── ⑦ 自慰类主题（v3.11.0）──")
    msb = [s for s in scenes if s.get("theme") == "自慰"]
    ck("存在「自慰」题材预设", len(msb) >= 20, f"{len(msb)} 条")
    lvset = {int(s["level"]) for s in msb}
    ck("自慰类覆盖 L2 / L3 / L4 三档（按 NSFW 等级分类）",
       {2, 3, 4} <= lvset, str(sorted(lvset)))
    ck("自慰类全部 ≥ L2（L1 擦边档拿得到是漏洞）",
       all(int(s["level"]) >= 2 for s in msb))
    ck("用户示例①「拉开内裤展示」在库里",
       any(s["id"] == "sc_msb_panties_aside" for s in msb))
    ck("用户示例②「假阳具」在库里",
       any("dildo" in str(s.get("tags", {}).get("n_theme", ""))
           or s.get("tags", {}).get("n_act") == "dildo riding" for s in msb))
    # ⚠️ 自慰是单人行为，但预设**不能写 count/role** —— 所以"单人"靠 extra 里的
    #    `solo` 标签表达：人数抽到单人时与 count 的 solo 去重，抽到多人时
    #    repair 的"数量互斥"会把它去掉（优雅降级，不崩不越档）。
    solo_ok = [s for s in msb if "solo" in (s.get("extra") or "")]
    ck("自慰类预设用 extra 里的 solo 表达单人", len(solo_ok) >= len(msb) - 1,
       f"{len(solo_ok)}/{len(msb)} 含 solo")
    ck("双人互慰**不**写 solo（它本来就是两个人）",
       "solo" not in (next(s for s in msb
                          if s["id"] == "sc_msb_mutual")["extra"]))
    # 闸门：自慰类在 L2 档只放 L2 的、L3 档不放 L4 的
    ids_l2 = {en for _z, en, _n in R.eligible_options("n_canon", 2)
              if en.startswith("sc_msb_")}
    ck("L2 档只拿到 L2 的自慰场景",
       ids_l2 == {s["id"] for s in msb if int(s["level"]) == 2},
       f"{len(ids_l2)} 条")
    ids_l3 = {en for _z, en, _n in R.eligible_options("n_canon", 3)
              if en.startswith("sc_msb_")}
    ck("L3 档拿不到 L4 的自慰场景（潮吹）",
       not (ids_l3 & {s["id"] for s in msb if int(s["level"]) == 4}),
       str(sorted(ids_l3 & {s["id"] for s in msb if int(s["level"]) == 4})))

    print("── ⑧ 擦边组扩充（v3.17.1：3 → 28；v3.17.2 镜头模板：→ 38）──")
    l1 = [s for s in scenes if int(s.get("level", -1)) == 1]
    ck("擦边组 ≥ 38 条", len(l1) >= 38, f"{len(l1)} 条")
    ck("擦边条目全部落在 sfw 组（面板分组正确）",
       all(s["group"] == "sfw" for s in l1),
       str(sorted({s["group"] for s in l1})))
    # 派生档位复算（与 build_scenes 同口径）：有标签的档位 ≤1 且至少一条 =1
    sys.path.insert(0, os.path.join(PLUGIN, "tools"))
    from build_scenes import scene_level
    bad_lv = [(s["id"], scene_level(s["tags"], s.get("extra", "")))
              for s in l1 if scene_level(s["tags"], s.get("extra", "")) != 1]
    ck("擦边条目派生档位全部 = 1（不沉到全年龄、不浮到成人）",
       not bad_lv, str(bad_lv[:4]))
    # 暗示姿势的使用率（v3.14.0 pose_risque 字段进场景配方）
    n_risque = sum(1 for s in l1 if "pose_risque" in s["tags"])
    ck("擦边条目大量使用暗示姿势（pose_risque）", n_risque >= 20,
       f"{n_risque}/{len(l1)} 条")
    # 四大题材方向都有覆盖（新增 25 条的 theme 分布）
    new_ids = {"sc_changing_room_peek", "sc_bedroom_sleepshirt", "sc_bath_towel",
               "sc_balcony_laundry", "sc_rain_wetshirt", "sc_sofa_catnap",
               "sc_konbini_midnight", "sc_kitchen_apron", "sc_skirt_wind",
               "sc_hotel_mirror", "sc_beach_volley", "sc_pool_ladder",
               "sc_surf_board", "sc_float_ring", "sc_shower_peek",
               "sc_watergun_fight", "sc_ol_overtime", "sc_maid_serving",
               "sc_nurse_rounds", "sc_classroom_openwin", "sc_jk_bike_ride",
               "sc_yoga_mat", "sc_gym_mirror", "sc_gymnastics_split",
               "sc_jog_rest"}
    got = {s["id"] for s in scenes} & new_ids
    ck("v3.17.1 新增 25 条全部在库", len(got) == 25,
       f"缺 {sorted(new_ids - got)}")
    # v3.17.2 镜头×姿势模板（10 条，纯镜头不带 place，地点交给随机）
    cam_ids = {"sc_cam_wide_low_side", "sc_cam_med_high_kneel",
               "sc_cam_fisheye_squat", "sc_cam_bird_lying", "sc_cam_pov_stand",
               "sc_cam_low_stretch", "sc_cam_side_hairflip", "sc_cam_dutch_wall",
               "sc_cam_ots_lookback", "sc_cam_worm_sit"}
    got_cam = {s["id"] for s in scenes} & cam_ids
    ck("v3.17.2 镜头模板 10 条全部在库", len(got_cam) == 10,
       f"缺 {sorted(cam_ids - got_cam)}")
    ck("镜头模板一律不带 place（纯镜头模板，地点随机）",
       all("place" not in s["tags"] for s in scenes if s["id"] in cam_ids),
       str([s["id"] for s in scenes if s["id"] in cam_ids and "place" in s["tags"]]))
    ck("镜头模板 theme=「镜头实验」",
       all(s.get("theme") == "镜头实验" for s in scenes if s["id"] in cam_ids))
    # 抽一条真套用：擦边预设写进 state 后 F15 档位准入不红。
    # ⚠️ 必须走 **state**（apply_preset 的真实路径）—— 用 check_text 纯文本反推
    #    会把 `bent over` 误归到 n_pos 槽位（成品层反推歧义），假越档。
    _sample = next(s for s in scenes if s["id"] == "sc_yoga_mat")
    _tags = list(dict.fromkeys(
        t.strip() for f in _sample["tags"].values() for t in str(f).split(",")))
    _rep = R.check(_tags, dict(_sample["tags"]), 1)
    ck("擦边预设成品在 L1 检查无致命越档",
       all(c["ok"] for c in _rep["F"] if c["id"] == "F15"),
       str([c["note"] for c in _rep["F"] if c["id"] == "F15" and not c["ok"]]))

    print()
    if FAILS:
        print(f"[check_v3_scenes] 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print(f"[check_v3_scenes] 通过：{len(scenes)} 个场景（"
          + " / ".join(f"{k}={v}" for k, v in sorted(by_group.items()))
          + "）· 合并 / 闸门 / 固定 / 输出 / 载荷 全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
