# -*- coding: utf-8 -*-
"""V3 的**字段间联动层**（v3.15.0）。

三张表，各自单向、可解释、逐条能讲清理由：

    ① ACT_POS_BAN  体位配对 —— n_act 抽中某行为后，n_pos 里哪些细节与之矛盾
    ② EMOTION_BAN  表情配套 —— emotion 抽中后，eye_gaze / mouth 里哪些与之矛盾
    ③ PLACE_THEME  场景倾向 —— place 抽中后，服装与姿势的加权（+ 少量硬剔除）

**为什么单独一个文件**：这三张表都是纯人工维护的**语义知识**，会随词库增删
持续调整；混在 rules.py 里会让"引擎逻辑"和"内容知识"互相干扰 ——
改一个判定要翻 2500 行才能找到表在哪。rules.py 只调这三个函数，
不关心表长什么样。

**为什么用"禁止"而不是"只允许"**：禁止表（黑名单）在词库增删时是**安全**的 ——
新增一个选项，默认就是"不受限"，忘了更新表也只是少一条约束；
而允许表（白名单）会让新选项**默认被排除**，表现是"我加了词条却抽不到"。
（这个教训来自 v3.12.0 的角色库：白名单漏登记 = 静默丢数据。）

⚠️ 表里的键与值一律用**英文原文**（与 pools 里的 `en` 逐字一致，含逗号与空格），
不要写中文、不要加下划线 —— pools 里存的是自然写法，比对上会静默失配。
`tests/check_v3_link.py` 逐个断言"表里的每个取值都真实存在于词库"。
"""
from __future__ import annotations

# ===========================================================================
# ① 体位配对：n_act → 与之矛盾的 n_pos 取值
# ===========================================================================
#
# 背景：n_act（性行为）与 n_pos（体态细节）是**两个独立随机**的字段。
# 不设这张表就会抽出「骑乘位 + 男上位」这种直接打架的组合 ——
# 女上位和男上位是互斥体位的两极，同时写进提示词会让模型两边都想照顾，
# 出图要么糊、要么按其中一边画（那另一个词就是白写的）。
#
# 判据（刻意保守，**只收"物理上不可能同时成立"的**）：
#   · 主位冲突：女上（cowgirl / reverse cowgirl / upright straddle / facesitting）
#     与「男上位 boy on top」互斥；反之正常位/压腿位与「女上 girl on top」互斥。
#   · 体态冲突：俯卧类（prone bone）不可能同时「双腿高举」或「双腿绕颈」；
#     站立类（standing sex / stand and carry）不可能同时「四肢着地」。
#   · 能力冲突：自慰/手交类需要用手，与「手腕束缚 / 手臂束缚」互斥。
#
# ⚠️ **存疑的一律不收**（宁可少一条，也不要误剔）：
#   · `doggystyle` 与 `boy on top` —— 后入时男方确实处于上位/主动位，
#     两者共现很常见，**不判矛盾**。
#   · `bent over` 与后入类 —— 弯腰撅臀正是后入的姿态，**兼容**。
#   · `spread legs` / `head down` 几乎与所有体位兼容，不出现在任何禁集里。
ACT_POS_BAN: dict[str, set[str]] = {
    # ── 女下男上系：不可能同时是「女上」或俯身 ──
    "missionary": {
        "girl on top", "straddling", "upright straddle",
        "squatting cowgirl position", "all fours, sex", "bent over",
        "against wall, sex",
    },
    "mating press": {
        "girl on top", "straddling", "upright straddle",
        "squatting cowgirl position", "all fours, sex", "bent over",
        "against wall, sex",
    },
    # 俯卧：连「双腿高举 / 双腿绕颈」也不可能（那是仰躺的动作）
    "prone bone": {
        "girl on top", "straddling", "upright straddle",
        "squatting cowgirl position", "legs up", "legs behind head",
        "bent over", "against wall, sex",
    },
    # ── 后入系 ──
    "doggystyle": {
        "girl on top", "upright straddle", "squatting cowgirl position",
        "legs up", "legs behind head", "against wall, sex",
    },
    "sex from behind": {
        "girl on top", "upright straddle", "squatting cowgirl position",
        "legs behind head",
    },
    # ── 女上系：男上位与俯身都不成立 ──
    "cowgirl position": {
        "boy on top", "bent over", "all fours, sex", "against wall, sex",
        "legs behind head",
    },
    "reverse cowgirl position": {
        "boy on top", "bent over", "all fours, sex", "against wall, sex",
        "legs behind head",
    },
    "facesitting": {
        "boy on top", "bent over", "all fours, sex", "against wall, sex",
    },
    # 全背位：女方背对坐在男方身上、被抱住双腿 —— 不是男上位、也不俯身
    "full nelson": {
        "boy on top", "bent over", "all fours, sex", "against wall, sex",
    },
    # ── 站立系 ──
    "standing sex": {
        "all fours, sex", "legs behind head", "legs up",
    },
    "stand and carry": {
        "bent over", "all fours, sex", "legs behind head",
    },
    "leg lock": {
        "all fours, sex",
    },
    # ── 需要用手的行为：与束缚互斥 ──
    "masturbation": {
        "girl on top", "boy on top", "straddling",
        "bound wrists", "bound arms",
    },
    "handjob": {"bound wrists", "bound arms"},
    "fingering": {"bound wrists", "bound arms"},
    # 骑假阳具是**单人**行为：与所有"需要有伴侣"的体位互斥
    "dildo riding": {"girl on top", "boy on top", "straddling"},
}

# ===========================================================================
# ② 表情配套：emotion → 与之矛盾的 eye_gaze / mouth 取值
# ===========================================================================
#
# 背景：三件套原本各抽各的，会出「大笑 + 抿嘴」（露齿大笑必然张嘴）、
# 「哭泣 + 露齿笑」、「认真 + 伸舌」这类打架组合。
#
# ⚠️ 键写 emotion 的**单个标签**。`embarrassed, blush` 这种多标签值会被
#    拆成两个标签匹配，所以键写 `embarrassed` 就能命中它。
#
# 判据：只收「表情与口型/眼神在语义上互斥」的，**不做风格偏好**
#（比如"得意配什么眼神"是口味问题，不是矛盾，不进表）。
EMOTION_BAN: dict[str, dict[str, set[str]]] = {
    "grin": {
        # 露齿大笑 / 咧嘴：嘴必然张开
        "mouth": {"closed mouth", "parted lips", "biting own lip", "licking lips"},
    },
    "pout": {                       # 嘟嘴：与张口、露齿、伸舌都冲突
        "mouth": {"open mouth", "grin, teeth", "tongue out"},
    },
    "expressionless": {             # 面无表情：不能同时笑/张嘴/俏皮眨眼
        "mouth": {"grin, teeth", "open mouth", "tongue out"},
        "eye_gaze": {"one eye closed"},
    },
    "sad": {
        "mouth": {"grin, teeth", "tongue out"},
        "eye_gaze": {"one eye closed"},
    },
    "crying": {
        "mouth": {"grin, teeth", "tongue out"},
        "eye_gaze": {"one eye closed", "staring"},
    },
    "tears": {                      # 含泪
        "mouth": {"grin, teeth"},
        "eye_gaze": {"one eye closed"},
    },
    "angry": {
        "mouth": {"grin, teeth", "licking lips"},
        "eye_gaze": {"one eye closed"},
    },
    "evil smile": {                 # 坏笑：不是抿嘴、也不是咬牙
        "mouth": {"closed mouth", "clenched teeth"},
    },
    "scared": {
        "mouth": {"grin, teeth"},
        "eye_gaze": {"one eye closed"},
    },
    "confused": {
        "mouth": {"grin, teeth", "clenched teeth"},
    },
    "disgust": {
        "mouth": {"grin, teeth", "licking lips"},
        "eye_gaze": {"one eye closed"},
    },
    "excited": {                    # 兴奋：不会抿嘴或咬牙
        "mouth": {"closed mouth", "clenched teeth"},
    },
    "serious": {                    # 认真：不露齿笑、不吐舌舔唇
        "mouth": {"grin, teeth", "tongue out", "licking lips"},
        "eye_gaze": {"one eye closed"},
    },
    "surprised": {
        "mouth": {"grin, teeth", "biting own lip"},
    },
    "sleepy": {                     # 困倦：不会瞪视或俏皮眨眼
        "eye_gaze": {"staring", "one eye closed"},
        "mouth": {"grin, teeth"},
    },
    "embarrassed": {                # 害羞脸红：不会直勾勾盯着
        "eye_gaze": {"staring"},
    },
}

# ===========================================================================
# ③ 场景倾向：place → 主题 → 服装/姿势的加权与硬剔除
# ===========================================================================
#
# 用户口径（2026-10-16 拍板）：**倾向加权 + 硬剔除**。
#   · 加权（bias）：把该主题下"合理"的选项权重放大（默认 ×8 上下），
#     但**不剥夺**其它选项 —— 泳池边仍可能抽到连衣裙（意外惊喜要留着）。
#   · 硬剔除（ban）：只剔**明确违和**的（厚冬装 × 泳池、泳装 × 教室）。
#     宁少勿多 —— 剔太多会让场景变成"只能出一种图"。
#
# ⚠️ 主题按 place 分组，**每个 place 必须能查到主题**，否则联动对它静默失效
#    （tests/check_v3_link.py 会逐个断言覆盖完整）。
PLACE_THEME: dict[str, str] = {
    # 水上 / 涉水 / 更衣（泳装、湿身、赤足的自然场景）
    "beach": "water", "onsen": "water", "poolside": "water",
    "in bathtub": "water", "showering": "water", "bathroom": "water",
    "changing room": "water", "underwater": "water", "lake": "water",
    "river": "water", "waterfall": "water", "aquarium": "water",
    # 卧室 / 床
    "bedroom": "bed", "on bed": "bed", "hotel room": "bed",
    # 校园
    "classroom": "school", "library": "school", "hallway": "school",
    "stairwell": "school", "stadium": "school",
    # 室内日常
    "office": "daily", "cafe": "daily", "convenience store": "daily",
    "movie theater": "daily", "fitting room": "daily", "kitchen": "daily",
    "apartment": "daily",
    # 夜生活 / 演出
    "bar (place)": "night", "concert": "night", "stage": "night",
    "amusement park": "night", "ferris wheel": "night",
    # 自然 / 户外 / 古迹
    "forest": "nature", "flower field": "nature", "park": "nature",
    "ruins": "nature", "bridge": "nature", "desert": "nature",
    "cave": "nature", "garden": "nature", "mountain": "nature",
    "field": "nature", "castle": "nature", "graveyard": "nature",
    "hill": "nature",
    # 城市 / 交通 / 街景
    "rooftop": "city", "alley": "city", "street": "city", "shrine": "city",
    "elevator": "city", "train interior": "city", "car interior": "city",
    "train station": "city", "road": "city", "bicycle": "city",
    "crosswalk": "city", "on motorcycle": "city", "balcony": "city",
    "church": "city", "bus stop": "city", "lighthouse": "city",
}

# 主题 → 字段 → {选项英文: 权重倍数}
# ⚠️ 倍数只影响**倾向**，不改档位、不改必现/永不（那两档由权重面板负责）。
THEME_WEIGHT: dict[str, dict[str, dict[str, float]]] = {
    "water": {
        "outfit": {
            "one-piece swimsuit": 8, "bikini": 8, "white bikini": 8,
            "competitive swimsuit": 8, "school swimsuit": 7,
            "competition school swimsuit": 7, "old school swimsuit": 6,
            "new school swimsuit": 6, "sports bikini": 7, "micro bikini": 5,
            "halterneck": 6, "bikini top only": 6, "bath towel": 5,
        },
        "bottom": {"swim briefs": 6},
        "socks": {"barefoot": 6},
        "shoes": {"bare feet": 6, "sandals": 4, "geta": 3},
        "accessory": {"straw hat": 4, "sunglasses": 4},
        "pose_base": {"sitting": 3, "squatting": 3, "standing": 2},
        "arms_hands": {"hand in own hair": 4},
    },
    "bed": {
        "outfit": {
            "sleeping kimono": 6, "camisole dress": 5, "sweater dress": 4,
            "bath towel": 4, "suspender shorts": 4,
        },
        "top": {"camisole": 5, "tank top": 4, "t-shirt": 4},
        "bottom": {"shorts": 4, "g-string": 4, "thong": 4},
        "socks": {"barefoot": 5, "loose socks": 4},
        # ⚠️ 躺姿权重给得比别的主题高（×10）：卧室场景里"躺着"是最典型的画面，
        #    而姿势字段本身还有 ~35% 的概率根本不出现（base_prob / 密度自适应），
        #    权重不够高的话"卧室"和"随便一个场景"在成品上几乎看不出差别
        #    （实测 ×6 时躺姿只占 15%，体感等于没联动）。
        "pose_base": {"on side": 10, "on back": 10, "on stomach": 6, "sitting": 4},
        "pose_risque": {"hugging own legs": 5, "arched back": 4},
    },
    "school": {
        "outfit": {
            "school uniform": 8, "serafuku": 8, "gym uniform": 8,
            "cheerleader uniform": 6, "sailor dress": 6, "pinafore dress": 5,
        },
        "top": {"sailor shirt": 7, "polo shirt": 5, "blouse": 4},
        "bottom": {"pleated skirt": 7, "skirt": 4, "bloomers": 6},
        "socks": {
            "black thighhighs": 5, "white thighhighs": 5, "kneehighs": 5,
            "loose socks": 5,
        },
        "shoes": {"uwabaki": 7, "loafers": 5, "sneakers": 4},
        "pose_base": {"sitting": 5, "head rest": 4},
        "arms_hands": {"own hands clasped": 5, "hand in own hair": 3},
    },
    "daily": {
        "outfit": {
            "office lady": 6, "sweater and skirt": 5, "hoodie and shorts": 5,
            "apron only": 4, "sundress": 4,
        },
        "top": {"shirt": 5, "blouse": 5, "cardigan": 5, "sweater": 4},
        "bottom": {"jeans": 5, "pencil skirt": 5, "skirt": 4},
        "shoes": {"loafers": 5, "sneakers": 5, "mary janes": 4},
        "accessory": {"wristwatch": 4, "glasses": 4, "handbag": 5},
        "pose_base": {"sitting": 5, "standing": 4},
        "arms_hands": {"own hands clasped": 4},
    },
    "night": {
        "outfit": {
            "evening gown": 6, "dress": 5, "idol costume": 5,
            "bunny girl": 4, "gothic lolita": 4, "jirai kei": 4,
            "ryousangata": 4, "gyaru": 4,
        },
        "shoes": {"high heels": 6, "high heel boots": 4},
        "accessory": {"earrings": 4, "necklace": 4, "choker": 4},
        "light_fx": {"neon lights": 6, "dark background": 4},
        "pose_base": {"standing": 3},
    },
    "nature": {
        "outfit": {
            "sundress": 6, "dress": 5, "sweater and skirt": 4,
            "hoodie and shorts": 4, "yukata": 4, "kimono": 3,
        },
        "bottom": {"long skirt": 5, "jeans": 4},
        "shoes": {"sneakers": 4, "boots": 3, "sandals": 3},
        "accessory": {"straw hat": 5, "scarf": 3, "beret": 3},
        "pose_base": {"standing": 4},
        "action": {"walking": 5, "spinning": 4, "looking back": 4},
        "light_fx": {"god rays": 5},
    },
    "city": {
        "top": {
            "leather jacket": 4, "denim jacket": 4, "bomber jacket": 4,
            "jacket": 4, "hoodie": 3,
        },
        "bottom": {"jeans": 5, "miniskirt": 4, "denim skirt": 4},
        "shoes": {"boots": 5, "sneakers": 4, "high heels": 4},
        "accessory": {"handbag": 5, "sunglasses": 5, "beret": 3},
        "pose_base": {"standing": 4},
        "action": {"walking": 4},
    },
}

# 主题 → 字段 → 硬剔除的选项（只收"明确违和"）
THEME_BAN: dict[str, dict[str, set[str]]] = {
    "water": {
        # 厚冬装与传统礼装：在水边/浴场是明确的违和（不是口味问题）。
        # ⚠️ 大衣/高领毛衣这类在 `top` 字段（`outfit` 只有"整套装"），
        #    所以两处都要登记 —— 字段不同，同样的东西住在不同字段里。
        "outfit": {
            "sweater and skirt", "sweater dress",
            "wedding dress", "evening gown", "hanfu", "hakama",
        },
        "top": {"fur coat", "fur-trimmed coat", "trench coat", "coat", "turtleneck"},
        "bottom": {"leggings", "track pants"},
    },
    "bed": {
        "outfit": {"wedding dress", "evening gown", "suit", "tuxedo"},
    },
    "school": {
        # 校园/图书馆里穿极限泳装是明确违和
        "outfit": {"micro bikini", "bikini top only"},
    },
    "daily": {
        "outfit": {"micro bikini"},
    },
}


# ===========================================================================
# 对外 API（rules.py 只用这三个）
# ===========================================================================

def _tags(value) -> set:
    """把字段值拆成单标签集合（多标签选项按逗号拆）。"""
    return {t.strip().lower() for t in str(value or "").split(",") if t.strip()}


def _theme_of(state: dict) -> str:
    return PLACE_THEME.get(str(state.get("place") or "").strip().lower(), "")


def bans_for(state: dict, field_key: str) -> set:
    """当前已抽状态下，该字段**禁止**的选项值（英文原文）集合。

    ⚠️ 调用时机很重要：依赖的字段必须**已经抽完**。
       · n_pos 依赖 n_act（同段，n_act 在前）✓
       · eye_gaze / mouth 依赖 emotion（同段，emotion 在前）✓
       · 服装 / 姿势依赖 place —— 而 place 原在 scene 段、排在服装之后，
         所以 draw 里对 place 做了**预抽**（见 rules._one_pass 的 pre-pass）。
    """
    out: set = set()
    if field_key == "n_pos":
        for t in _tags(state.get("n_act")):
            out |= ACT_POS_BAN.get(t, set())
    elif field_key in ("eye_gaze", "mouth"):
        for t in _tags(state.get("emotion")):
            out |= (EMOTION_BAN.get(t) or {}).get(field_key, set())
    theme = _theme_of(state)
    if theme:
        out |= (THEME_BAN.get(theme) or {}).get(field_key, set())
    return out


def bias_for(state: dict, field_key: str, en: str) -> float:
    """该选项在当前场景下的权重倍数（1.0 = 不干预）。"""
    theme = _theme_of(state)
    if not theme:
        return 1.0
    return float((THEME_WEIGHT.get(theme) or {}).get(field_key, {}).get(en, 1.0))
