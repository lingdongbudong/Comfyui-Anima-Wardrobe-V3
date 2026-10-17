/**
 * Anima 提示词随机生成（V3）· 可视化面板
 * ---------------------------------------------------------------------------
 * 挂在节点 AnimaV3RandomPrompt 上，点「打开随机面板」弹出。
 *
 * 面板与后端共用同一套随机引擎（POST /anima-wardrobe-v3/roll 与节点
 * 调的是同一个 R.draw），所以面板里看到的结果 = 定下种子后工作流跑出来的结果。
 * 这就是「采用这条」按钮的底气：它只是把 seed 写回节点控件。
 *
 * 界面口径（沿用本项目既有约定）：
 *   · 能在面板里配的项，节点上不重复外露（所以节点上只有 9 个控件）；
 *   · 动作类按钮（随机 / 换种子 / 清空）与入口按钮保留在节点上；
 *   · 权重用三层：段 / 字段 / 选项，0 = 关闭，1.0 = 默认；
 *   · 🎯 三态指示：🎯 未调 / 🎯 n 调了 n 项 / 🎯 全部 全部非默认。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NS = "anima-wardrobe-v3";
const NODE_TYPE = "AnimaV3RandomPrompt";

/** 「⭐ 我的收藏」这个伪作品 id。
 *  真作品 id 都是 slug（小写字母数字下划线），不会出现双下划线开头，撞不上。 */
const FAV_ID = "__fav__";

const LEVEL_SHORT = {
  sfw: "0 全年龄",
  suggestive: "1 擦边",
  explicit: "2 成人",
  hardcore: "3 过激",
  extreme: "4 极端",
  any: "不限",
};

/* ── 状态：只放进程内缓存，节点值才是真源 ───────────────────────────── */
const CACHE = { pools: null, contract: null, loading: null };

async function fetchJSON(url) {
  const r = await api.fetchApi(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return await r.json();
}

function ensureData() {
  if (!CACHE.loading) {
    CACHE.loading = Promise.all([
      fetchJSON(`/${NS}/pools`),
      fetchJSON(`/${NS}/contract`),
    ]).then(([p, c]) => {
      CACHE.pools = p;
      CACHE.contract = c;
      return CACHE;
    });
  }
  return CACHE.loading;
}

/* ── 节点控件读写 ─────────────────────────────────────────────────── */
function wget(node, name, dflt = "") {
  const w = (node.widgets || []).find((x) => x.name === name);
  return w ? w.value : dflt;
}

function wset(node, name, value) {
  const w = (node.widgets || []).find((x) => x.name === name);
  if (!w) return false;
  w.value = value;
  if (w.callback) {
    try {
      w.callback(value, app.canvas, node, [0, 0], null);
    } catch (e) {
      /* 某些控件没有 callback 参数契约，忽略即可，值已经写进去了 */
    }
  }
  return true;
}

/* ── 权重表读写（控件里存的是 JSON 文本）─────────────────────────── */
function loadWeights(node) {
  const raw = wget(node, "weights_json", "");
  const empty = { sections: {}, fields: {}, options: {} };
  if (!raw) return empty;
  try {
    const d = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!d || typeof d !== "object") return empty;
    return {
      sections: d.sections && typeof d.sections === "object" ? { ...d.sections } : {},
      fields: d.fields && typeof d.fields === "object" ? { ...d.fields } : {},
      options: d.options && typeof d.options === "object" ? { ...d.options } : {},
    };
  } catch (e) {
    // 与后端 parse_weights 同一口径：非法 JSON 当空表，不弹错
    return empty;
  }
}

function saveWeights(node, w) {
  const out = {};
  const strip = (bag) => {
    const o = {};
    for (const [k, v] of Object.entries(bag || {})) {
      const f = Number(v);
      if (Number.isFinite(f) && Math.abs(f - 1) > 1e-9) o[k] = Math.round(f * 1000) / 1000;
    }
    return o;
  };
  const s = strip(w.sections);
  const f = strip(w.fields);
  if (Object.keys(s).length) out.sections = s;
  if (Object.keys(f).length) out.fields = f;
  const o = {};
  for (const [fk, sub] of Object.entries(w.options || {})) {
    const clean = strip(sub);
    if (Object.keys(clean).length) o[fk] = clean;
  }
  if (Object.keys(o).length) out.options = o;
  wset(node, "weights_json", Object.keys(out).length ? JSON.stringify(out, null, 1) : "");
}

/** 某个字段被调过的选项数 */
function tunedCount(w, fieldKey) {
  return Object.keys(w.options?.[fieldKey] || {}).length;
}

/** 🎯 三态：0 未调 / 1 部分 / 2 全部 */
function weightBadge(w, fieldKey, total) {
  const n = tunedCount(w, fieldKey);
  if (!n) return { text: "🎯", state: 0 };
  if (total && n >= total) return { text: "🎯 全部", state: 2 };
  return { text: `🎯 ${n}`, state: 1 };
}

/* ── 免责声明 ───────────────────────────────────────────────────────
   ⚠️ 这里的文案是**用户逐字给的**，不要改写、不要精简、不要调整措辞。
      改文案属于内容变更，得先跟用户确认。
      展示位置：面板头部「⚠ 免责声明」按钮 → 大弹窗（可滚动）。        */
const DISCLAIMER_TITLE = "免责声明与使用条款";
const DISCLAIMER_INTRO =
  "本插件为开源插件（Comfyui-Anima-Wardrobe-V3），按原样提供、"
  + "用于 ComfyUI 工作流内的提示词生成。生成过程全部在你本机完成，"
  + "不联网、不上传任何内容。十八岁以下禁止使用。";
const DISCLAIMER_BYLINE = [
  "插件作者：灵冻不冻",
  "插件底层框架源于脚本：ANIMA提示词生成器.html 已授权开源 —— 作者：飞蓬。",
];
const DISCLAIMER_ITEMS = [
  "① 所有角色一律视为18 岁以上虚构人物，禁止用于真实人物、未成年人、非自愿及暴力伤害相关内容。",
  "② 角色名与作品名等知识产权归各自权利人所有，此处仅作提示词检索与描述；角色头像取自公开图库，仅用于识别对照，不作他用。",
  "③ 提示词是词库模板的随机组合，不保证每条都符合预期 —— 正式出片前请自行筛选；同一设置换个种子结果就不同。",
  "④ 调高「内容强度」会让措辞明显更露骨，请自行确认是否符合你所在平台的发布政策。",
  "⑤ 生成内容及其用途由你自行负责，本插件不对由此产生的任何后果承担责任。",
];

/** 打开免责声明弹窗。返回弹窗根节点，方便测试与外部关闭。 */
function openDisclaimer() {
  const maskEl = document.createElement("div");
  maskEl.className = "av3-mask";
  const inner = document.createElement("div");
  inner.className = "av3-mini";
  inner.style.position = "static";
  maskEl.appendChild(inner);

  const box = document.createElement("div");
  box.className = "card av3-disc";
  inner.appendChild(box);

  const hd = document.createElement("div");
  hd.className = "hd";
  hd.textContent = DISCLAIMER_TITLE;
  box.appendChild(hd);

  const bd = document.createElement("div");
  bd.className = "bd";
  const lead = document.createElement("p");
  lead.className = "lead";
  lead.textContent = DISCLAIMER_INTRO;
  bd.appendChild(lead);
  for (const line of DISCLAIMER_BYLINE) {
    const p = document.createElement("p");
    p.className = "by";
    p.textContent = line;
    bd.appendChild(p);
  }
  for (const it of DISCLAIMER_ITEMS) {
    const p = document.createElement("p");
    p.className = "it";
    p.textContent = it;
    bd.appendChild(p);
  }
  box.appendChild(bd);

  const ft = document.createElement("div");
  ft.className = "ft";
  const ok = document.createElement("button");
  ok.className = "av3-btn pri";
  ok.textContent = "我已阅读";
  ok.onclick = () => maskEl.remove();
  ft.appendChild(ok);
  box.appendChild(ft);

  // 点遮罩空白处也能关（与面板里其它弹窗一致）
  maskEl.onclick = (e) => {
    if (e.target === maskEl) maskEl.remove();
  };
  document.body.appendChild(maskEl);
  return maskEl;
}

/* ── 一次性注入样式 ───────────────────────────────────────────────── */
const CSS = `
.av3-mask{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;}
.av3-panel{width:min(1180px,94vw);height:min(840px,92vh);display:flex;flex-direction:column;
  background:var(--comfy-menu-bg,#1e1e1e);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#444);border-radius:10px;overflow:hidden;font-size:13px;}
.av3-head{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border-color,#444);flex-wrap:wrap;}
.av3-head h3{margin:0;font-size:14px;font-weight:600;color:#e8c98a;}
.av3-lv{display:flex;gap:0;border:1px solid var(--border-color,#444);border-radius:7px;overflow:hidden;}
.av3-lv button{border:0;background:transparent;color:var(--fg-color,#bbb);padding:6px 12px;cursor:pointer;font-size:12px;}
.av3-lv button.on{background:#d9b36c;color:#241a05;font-weight:600;}
.av3-btn{border:1px solid var(--border-color,#555);background:transparent;color:var(--fg-color,#ccc);
  border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12px;}
.av3-btn:hover{border-color:#d9b36c;color:#e8c98a;}
.av3-btn.pri{background:#d9b36c;border-color:#d9b36c;color:#241a05;font-weight:600;}
.av3-btn:disabled{opacity:.4;cursor:not-allowed;}
.av3-body{flex:1;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,430px);overflow:hidden;}
.av3-left{overflow:auto;padding:10px 12px;border-right:1px solid var(--border-color,#444);}
.av3-right{overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:10px;}
.av3-sec{border:1px solid var(--border-color,#3a3a3a);border-radius:8px;margin-bottom:8px;overflow:hidden;}
.av3-sec>.hd{display:flex;align-items:center;gap:8px;padding:7px 10px;background:rgba(255,255,255,.035);cursor:pointer;}
.av3-sec>.hd .t{flex:1;font-weight:600;color:#e8c98a;font-size:12.5px;}
.av3-sec>.bd{padding:8px 10px;display:none;}
.av3-sec.open>.bd{display:block;}
.av3-sec.off{opacity:.45;}
.av3-sec>.hd .th{font-size:11.5px;color:#9a9a9a;}
.av3-sec>.hd .arw{font-size:10px;color:#9a9a9a;width:9px;}
/* 场景卡的动作行与预览框是 .av3-sec 的**直接子元素**（不在 .bd 里）——
   否则折叠时会把「一键套用」一起藏掉，必须展开才能套用。 */
.av3-sceneact{display:flex;gap:6px;flex-wrap:wrap;padding:0 10px 9px;}
.av3-sceneout{margin:0 10px 9px;max-height:220px;}
.av3-row{display:flex;align-items:center;gap:7px;padding:4px 0;border-bottom:1px dashed rgba(255,255,255,.06);}
.av3-row:last-child{border-bottom:0;}
.av3-row .lab{flex:1;font-size:12.5px;}
.av3-row .lv{font-size:11px;color:#8a8a8a;min-width:52px;text-align:right;}
.av3-w{width:58px;background:var(--comfy-input-bg,#2a2a2a);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#555);border-radius:5px;padding:3px 5px;font-size:12px;text-align:center;}
.av3-t{border:1px solid var(--border-color,#555);background:transparent;color:#9a9a9a;border-radius:5px;
  padding:2px 7px;font-size:11.5px;cursor:pointer;}
.av3-t.s1{color:#e0a458;border-color:#e0a458;}
.av3-t.s2{color:#7ec98b;border-color:#7ec98b;}
.av3-t:disabled{opacity:.35;cursor:not-allowed;}
.av3-box{background:rgba(255,255,255,.04);border:1px solid var(--border-color,#3a3a3a);border-radius:8px;padding:9px 11px;}
.av3-box h4{margin:0 0 7px;font-size:12.5px;color:#e8c98a;font-weight:600;}
.av3-out{background:rgba(0,0,0,.32);border:1px solid var(--border-color,#3a3a3a);border-radius:6px;
  padding:9px;min-height:96px;max-height:230px;overflow:auto;line-height:1.66;white-space:pre-wrap;
  word-break:break-word;font-size:12.5px;}
.av3-meta{font-size:11.5px;color:#9a9a9a;line-height:1.7;}
.av3-sc{font-size:11.5px;line-height:1.65;}
.av3-ok{color:#7ec98b;} .av3-no{color:#e07070;} .av3-wa{color:#e0a458;}
.av3-fld{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px;}
.av3-fld label{flex:1;color:#aaa;}
.av3-fld input[type=text],.av3-fld select{flex:1.6;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;padding:4px 6px;font-size:12px;}
.av3-sum{font-size:11.5px;color:#9a9a9a;line-height:1.7;max-height:112px;overflow:auto;}
.av3-mini{position:fixed;inset:0;z-index:10001;display:flex;align-items:center;justify-content:center;}
.av3-mini .card{width:min(620px,92vw);max-height:80vh;display:flex;flex-direction:column;
  background:var(--comfy-menu-bg,#1e1e1e);border:1px solid var(--border-color,#555);border-radius:10px;overflow:hidden;}
.av3-mini .card .hd{padding:9px 12px;border-bottom:1px solid var(--border-color,#444);display:flex;
  align-items:center;gap:8px;font-size:13px;font-weight:600;color:#e8c98a;}
.av3-mini .card .bd{overflow:auto;padding:8px 12px;}
.av3-mini .card .ft{padding:8px 12px;border-top:1px solid var(--border-color,#444);display:flex;gap:8px;justify-content:flex-end;}
/* 免责声明：比普通小弹窗更大（用户要求"可以交互展开全屏内容"） */
.av3-mini .card.av3-disc{width:min(760px,94vw);max-height:88vh;}
.av3-disc .bd p{margin:0 0 10px;font-size:12.5px;line-height:1.78;color:var(--fg-color,#ccc);}
.av3-disc .bd p.it{color:#dcdcdc;}
.av3-disc .bd p.by{color:#e8c98a;font-size:12px;margin-bottom:5px;}
.av3-disc .bd p.lead{color:#e8c98a;}
.av3-oRow{display:flex;align-items:center;gap:8px;padding:3px 0;}
.av3-oRow .n{flex:1;font-size:12.5px;}
.av3-oRow .e{font-size:11px;color:#7d7d7d;max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.av3-note{font-size:11.5px;color:#e0a458;line-height:1.6;margin-top:6px;}
.av3-tabs{display:flex;border:1px solid var(--border-color,#444);border-radius:7px;overflow:hidden;}
.av3-tabs button{border:0;background:transparent;color:var(--fg-color,#bbb);padding:5px 13px;cursor:pointer;font-size:12.5px;}
.av3-tabs button.on{background:#d9b36c;color:#241a05;font-weight:600;}
.av3-charbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:8px;}
.av3-charbar input[type=text]{flex:1;min-width:150px;background:var(--comfy-input-bg,#2a2a2a);color:var(--fg-color,#ddd);
  border:1px solid var(--border-color,#555);border-radius:6px;padding:5px 8px;font-size:12.5px;}
.av3-chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0 9px;}
.av3-chip{display:inline-flex;align-items:center;gap:5px;background:rgba(217,179,108,.13);
  border:1px solid rgba(217,179,108,.45);border-radius:14px;padding:2px 6px 2px 10px;font-size:12px;color:#f0d9a8;}
.av3-chip b{font-weight:500;cursor:pointer;color:#e8c98a;padding:0 3px;}
.av3-chip b:hover{color:#fff;}
.av3-cbody{display:grid;grid-template-columns:190px minmax(0,1fr);gap:10px;align-items:start;}
.av3-works{max-height:520px;overflow:auto;border:1px solid var(--border-color,#3a3a3a);border-radius:8px;padding:5px;}
.av3-work{display:flex;align-items:center;gap:6px;padding:4px 7px;border-radius:6px;cursor:pointer;font-size:12.5px;}
.av3-work:hover{background:rgba(255,255,255,.05);}
.av3-work.on{background:rgba(217,179,108,.18);color:#f0d9a8;}
.av3-work .c{margin-left:auto;font-size:11px;color:#8a8a8a;}
.av3-work.tgt .n::before{content:"★";color:#d9b36c;margin-right:3px;}
/* 收藏为 0 时置灰，但仍可点（点进去看空状态提示） */
.av3-work.empty{opacity:.45;}
.av3-preset{display:flex;gap:7px;align-items:center;margin:5px 0;}
.av3-preset select,.av3-preset input[type=text]{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:4px 6px;font-size:12px;}
.av3-preset .av3-btn{padding:4px 9px;}
/* 面板底部条：目前只放「巨乳」开关。固定在最下方，不随两列滚动。 */
.av3-foot{display:flex;align-items:center;gap:10px;padding:7px 14px;flex-wrap:wrap;
  border-top:1px solid var(--border-color,#444);background:rgba(255,255,255,.02);}
.av3-foot label{display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  font-size:12.5px;user-select:none;}
.av3-foot input[type=checkbox]{width:15px;height:15px;cursor:pointer;accent-color:#d9b36c;}
.av3-foot .hint{font-size:11.5px;color:#8a8a8a;}
.av3-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:7px;
  max-height:520px;overflow:auto;padding-right:2px;}
.av3-card{border:1px solid var(--border-color,#3a3a3a);border-radius:8px;overflow:hidden;cursor:pointer;
  background:rgba(255,255,255,.03);position:relative;}
.av3-card:hover{border-color:#d9b36c;}
.av3-card.on{border:2px solid #d9b36c;background:rgba(217,179,108,.14);}
.av3-card .ph{width:100%;aspect-ratio:1/1;object-fit:cover;display:block;background:#26262e;}
.av3-card .ph.letter{display:flex;align-items:center;justify-content:center;font-size:26px;color:#5f5e5a;}
.av3-card .nmRow{display:flex;align-items:center;gap:4px;padding:4px 5px 1px;}
.av3-card .nmRow .nm{flex:1;min-width:0;padding:0;font-size:11.5px;line-height:1.3;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.av3-card .en{padding:0 5px 5px;font-size:10.5px;color:#8a8a8a;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
/* 人气徽标跟着名字走（原来是绝对定位在右上角，那个角要让给收藏星） */
.av3-card .pp{flex:0 0 auto;font-size:10px;color:#8a8a8a;}
/* 收藏星：卡片右上角。切到别的角都会跟「选中打勾」或人气撞车。 */
.av3-card .fav{position:absolute;top:3px;right:3px;width:19px;height:19px;display:flex;
  align-items:center;justify-content:center;font-size:13px;line-height:1;color:#9a9a9a;
  cursor:pointer;border-radius:50%;background:rgba(0,0,0,.55);user-select:none;}
.av3-card .fav:hover{color:#e8c98a;background:rgba(0,0,0,.75);}
.av3-card .fav.on{color:#f5c451;}
.av3-card .tick{position:absolute;top:3px;left:3px;background:#d9b36c;color:#241a05;
  font-size:11px;border-radius:9px;width:16px;height:16px;display:flex;align-items:center;
  justify-content:center;font-weight:700;}
.av3-more{grid-column:1/-1;text-align:center;padding:8px;}
.av3-empty{font-size:12px;color:#8a8a8a;padding:14px;text-align:center;grid-column:1/-1;}
/* v3.4.0 角色编辑 / 自定义角色 */
.av3-card .edit{position:absolute;right:3px;bottom:3px;width:19px;height:19px;display:flex;
  align-items:center;justify-content:center;font-size:12px;line-height:1;color:#9a9a9a;
  cursor:pointer;border-radius:50%;background:rgba(0,0,0,.55);user-select:none;}
.av3-card .edit:hover{color:#e8c98a;background:rgba(0,0,0,.75);}
.av3-card .av3-badge{position:absolute;left:3px;bottom:3px;font-size:9.5px;line-height:1;
  padding:2px 4px;border-radius:4px;background:rgba(126,201,139,.92);color:#12301c;
  user-select:none;}
.av3-mini .card.av3-editor{width:min(560px,94vw);}
.av3-editor .hd .tag{margin-left:auto;font-size:11px;color:#8a8a8a;font-weight:400;}
.av3-efld{display:flex;align-items:center;gap:8px;margin:7px 0;font-size:12.5px;}
.av3-efld label{flex:0 0 72px;color:#aaa;}
.av3-efld input[type=text]{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:4px 6px;font-size:12.5px;}
.av3-ethumb{display:flex;gap:10px;align-items:flex-start;margin:6px 0 4px;}
.av3-ethumb .pv{width:84px;height:84px;flex:0 0 84px;border-radius:8px;overflow:hidden;
  background:#26262e;display:flex;align-items:center;justify-content:center;
  font-size:24px;color:#5f5e5a;}
.av3-ethumb .pv img{width:100%;height:100%;object-fit:cover;display:block;}
.av3-ethumb .ops{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;}
.av3-ethumb .ops .row{display:flex;gap:6px;flex-wrap:wrap;}
.av3-eerr{color:#e07070;font-size:11.5px;line-height:1.5;min-height:16px;margin:2px 0 0;}
/* v3.5.0 特征标签编辑区 */
.av3-tagsec{border-top:1px dashed var(--border-color,#3a3a3a);margin-top:10px;padding-top:9px;}
.av3-taghead{display:flex;align-items:center;gap:8px;margin-bottom:5px;}
.av3-taghead .ttl{font-size:12.5px;font-weight:600;color:#e8c98a;}
.av3-taghead .av3-btn{margin-left:auto;padding:3px 8px;font-size:11.5px;}
.av3-tagrows{max-height:186px;overflow:auto;display:flex;flex-direction:column;gap:4px;}
.av3-tagrow{display:flex;gap:5px;align-items:center;}
.av3-tagrow input{flex:1;min-width:0;background:var(--comfy-input-bg,#2a2a2a);
  color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:5px;
  padding:3px 6px;font-size:12px;}
.av3-tagrow .av3-btn{padding:2px 8px;font-size:12px;line-height:1.35;flex:0 0 auto;}
.av3-tagfoot{display:flex;align-items:center;gap:8px;margin-top:6px;}
.av3-tagsec .av3-meta{font-size:11.5px;color:#9a9a9a;line-height:1.6;}
`;

let cssDone = false;
function injectCSS() {
  if (cssDone) return;
  cssDone = true;
  const s = document.createElement("style");
  s.textContent = CSS;
  document.head.appendChild(s);
}

/* ── 主面板 ───────────────────────────────────────────────────────── */
function openPanel(node) {
  injectCSS();
  ensureData().then(() => buildPanel(node)).catch((e) => {
    console.error("[AnimaV3] 面板数据加载失败", e);
    alert("Anima V3 面板加载失败：" + e.message + "\n请确认插件已随 ComfyUI 启动，并刷新页面。");
  });
}

function lsGet(node, name, dflt = "") {
  return wget(node, name, dflt);
}

function buildPanel(node) {
  const { pools, contract } = CACHE;
  const mask = document.createElement("div");
  mask.className = "av3-mask";

  const panel = document.createElement("div");
  panel.className = "av3-panel";
  mask.appendChild(panel);

  /* 面板内的临时状态。权重与设置类改动会**立即**写回节点（见 persist），
     只有 seed 需要点「采用这条」—— 它是预览产物，不能乱写。 */
  const ui = {
    level: String(lsGet(node, "nsfw_level", "sfw")),
    weights: loadWeights(node),
    lastResult: null,
    lastSeed: null,
    // 默认展开前三段 —— 全折叠的面板第一眼看着像坏了
    openSecs: new Set(pools.sections.slice(0, 3).map((s) => s.id)),
    tab: "weights",
    chars: null,            // 角色库（懒加载）
    charsLoading: false,
    favorites: null,        // Set<tag>，懒加载；null = 还没拉过
    favLoading: false,
    charWork: "",           // 作品筛选：空 = 全部；FAV_ID = 只看收藏
    charQuery: "",
    charLimit: 96,          // 角色网格分页渲染上限（2000 张一次渲染会卡）
    charRndN: 1,            // 🎲 随机抽角色：一次抽几位（v3.8.0）
    scenes: null,           // 场景搭配库（懒加载，v3.9.0）
    // v3.11.0：场景卡默认**折叠**（87 条全展开要滚很久），
    // 记每张卡的展开态；「全部展开/收起」一次改全部。
    sceneOpen: {},
    scenesLoading: false,
    presets: [],            // 命名预设立列表（懒加载）
    presetsLoading: false,
  };
  // 节点上已有的 disabled_sections 要折成段权重 0，否则面板显示"全开"、
  // 实际跑起来却关着几段，两边对不上。
  for (const sid of String(lsGet(node, "disabled_sections", "")).split(",")) {
    const t = sid.trim();
    if (t && ui.weights.sections[t] === undefined) ui.weights.sections[t] = 0;
  }

  /* ── 头部 ── */
  const head = document.createElement("div");
  head.className = "av3-head";
  const title = document.createElement("h3");
  title.textContent = "Anima 提示词随机生成（V3）";
  head.appendChild(title);

  /* 左列分两页：词库权重 / 角色库。角色库 2000+ 条，进面板时**不预加载**，
     点到那一页才拉 —— 否则每次开面板都要等 400KB JSON。 */
  const tabs = document.createElement("div");
  tabs.className = "av3-tabs";
  const tabBtns = {};
  for (const [key, label] of [["weights", "词库与权重"], ["chars", "角色库"],
                              ["scenes", "🎬 场景搭配"]]) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => {
      ui.tab = key;
      syncTabs();
    };
    tabs.appendChild(b);
    tabBtns[key] = b;
  }
  head.appendChild(tabs);

  const lvBox = document.createElement("div");
  lvBox.className = "av3-lv";
  const lvBtns = {};
  for (const lv of contract.levels) {
    const b = document.createElement("button");
    b.textContent = LEVEL_SHORT[lv.key] || lv.key;
    b.title = lv.desc || "";
    b.onclick = () => {
      ui.level = lv.key;
      syncLevel();
    };
    lvBox.appendChild(b);
    lvBtns[lv.key] = b;
  }
  head.appendChild(lvBox);

  const spacer = document.createElement("div");
  spacer.style.flex = "1";
  head.appendChild(spacer);

  // 免责声明入口放头部而不是底部：它得在任何页签下都看得见，
  // 而底部两列都在滚动容器里，会被滚走。
  const discBtn = document.createElement("button");
  discBtn.className = "av3-btn";
  discBtn.textContent = "⚠ 免责声明";
  discBtn.title = "使用条款与责任说明";
  discBtn.onclick = () => openDisclaimer();
  head.appendChild(discBtn);

  const closeBtn = document.createElement("button");
  closeBtn.className = "av3-btn";
  closeBtn.textContent = "关闭";
  closeBtn.onclick = () => {
    saveWeights(node, ui.weights);
    mask.remove();
    app.graph.setDirtyCanvas(true, true);
  };
  head.appendChild(closeBtn);
  panel.appendChild(head);

  /* ── 主体两列 ── */
  const body = document.createElement("div");
  body.className = "av3-body";
  const left = document.createElement("div");
  left.className = "av3-left";
  const right = document.createElement("div");
  right.className = "av3-right";
  body.appendChild(left);
  body.appendChild(right);
  panel.appendChild(body);

  /* ── 底部条：胸部大小等级（v3.17.0，取代巨乳开关）────────────────
     放在面板**最底部**（独立于两列，不随滚动跑掉）。
     「不指定」= figure 参与随机（可被角色官方设定覆盖）；
     指定档位 = figure 锁定为对应 tag，优先级高于角色外貌对齐与权重。
     状态写进节点控件 breast_size，随工作流 JSON 保存。
     ⚠️ 旧工作流里 big_breasts=True 的一次性兼容：升级后首次打开面板时，
        若 breast_size 还没选过且旧开关是开的 → 显示为「巨乳」，
        但**不回写**旧开关（保持原值，真正生效以 breast_size 为准）。 */
  const foot = document.createElement("div");
  foot.className = "av3-foot";
  const bsLab = document.createElement("label");
  bsLab.title = "指定后身材轮廓（figure）锁定为所选档位，覆盖角色设定与权重";
  const bsTxt = document.createElement("span");
  bsTxt.textContent = "胸部大小";
  bsLab.appendChild(bsTxt);
  foot.appendChild(bsLab);
  /* 真源映射与后端 BREAST_SIZES / BREAST_SIZE_CHOICES 保持一致（改两边要同步）。 */
  const BS_CHOICES = [
    ["不指定", ""], ["平胸", "flat chest"], ["小胸", "small breasts"],
    ["中等", "medium breasts"], ["大胸", "large breasts"],
    ["巨乳", "huge breasts"], ["爆乳", "gigantic breasts"],
  ];
  const bsSel = document.createElement("select");
  for (const [zh] of BS_CHOICES) {
    const opt = document.createElement("option");
    opt.value = zh;
    opt.textContent = zh;
    bsSel.appendChild(opt);
  }
  /* 兼容读（v3.17.0）：老工作流的 big_breasts=True 语义平移 ——
     ⚠️ ComfyUI 加载老工作流时，缺失的尾部控件会用**默认值**填充
        （breast_size="不指定"），无法区分"默认填充"与"用户真选"。
        所以判定只能靠旧开关：它还开着 = 旧语义"要巨乳"没被消费过 →
        一次性平移成 breast_size=巨乳 + 旧开关退役（写回）。
        面板没打开过就跑工作流的情况由**后端**兜底（同一条规则）。 */
  const bbOld = lsGet(node, "big_breasts", false) === true;
  const bsSaved = String(lsGet(node, "breast_size", "不指定") || "不指定");
  if (bbOld && bsSaved === "不指定") {
    wset(node, "breast_size", "巨乳");
    wset(node, "big_breasts", false);
    bsSel.value = "巨乳";
  } else {
    bsSel.value = BS_CHOICES.some(([zh]) => zh === bsSaved) ? bsSaved : "不指定";
  }
  foot.appendChild(bsSel);
  const bsHint = document.createElement("span");
  bsHint.className = "hint";
  bsHint.textContent = "不指定=随机（可被角色设定覆盖）；指定=锁定身材轮廓，覆盖外貌对齐与权重";
  foot.appendChild(bsHint);
  bsSel.onchange = () => {
    wset(node, "breast_size", bsSel.value);
    app.graph.setDirtyCanvas(true, true);
    setStatus(bsSel.value === "不指定" ? "胸部大小：不指定（figure 参与随机）"
                                      : `胸部大小已锁定：${bsSel.value}`, "");
  };
  panel.appendChild(foot);

  /* ── 左列：段落 / 字段 / 权重 ── */
  const secEls = new Map();

  /* 状态条：两个页签都要用（权重页的动作反馈、角色页的清除反馈），
     所以挂在 buildPanel 作用域而不是某一列里面。 */
  let statEl = null;
  /* 「🎬 场景搭配」页要能一键套用后立刻跑一次预演 —— 而 runRoll 定义在
     renderRight 内部（那个作用域拿不到），所以在 buildPanel 层留个钩子，
     由 renderRight 结尾把 runRoll 挂上来。 */
  let panelRoll = null;
  /** v3.11.0：非破坏性预览（只看不套用）。同样由 renderRight 结尾挂上来。 */
  let panelPreview = null;
  /**
   * 读请求序号 —— 写操作会 +1，用来**作废在途的读**。
   *
   * ⚠️ 为什么必须有：GET 的响应可能比随后的写操作**更晚**到达，
   *    而且它带的是**发请求那一刻的旧快照**。不加防护的话，
   *    "删掉预设 → 面板上又冒出来"（冒烟抓到过）：
   *      ① applyConfig → loadPresets() 发出 GET（快照：还有 1 条）
   *      ② 用户点删除 → POST 成功 → ui.presets = []
   *      ③ ①的响应姗姗来迟 → ui.presets 被旧快照覆盖 → 列表复活
   *    规则：**后发起的写操作作废在途的读**，写操作的返回值才是权威。
   */
  let presetsSeq = 0;
  let favSeq = 0;
  function setStatus(text, cls) {
    if (!statEl) return;
    statEl.className = "av3-meta " + (cls === "ok" ? "av3-ok"
      : cls === "no" ? "av3-no" : "");
    statEl.textContent = text;
  }

  /* ── 角色库：懒加载 + 作品筛选 + 搜索 + 多选 ───────────────── */
  //
  // 数据量 2000+ 条，两条纪律：
  //   ① **点到这一页才拉**（不预加载），否则每次开面板都要等 400KB JSON；
  //   ② 网格**分页渲染**（默认 96 张，点「显示更多」+96），
  //      2000 个 <img> 一次性塞进 DOM 会把浏览器卡死。
  //
  // 选中结果写进节点控件 selected_roles（用 `|` 分隔的**角色 tag**）。
  // ⚠️ 分隔符不能用逗号：完整 trigger（`hakurei reimu, touhou`）里就有逗号。

  function selectedList() {
    return String(lsGet(node, "selected_roles", "") || "")
      .split(/[|;\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function writeSelected(list) {
    wset(node, "selected_roles", list.join("|"));
    app.graph.setDirtyCanvas(true, true);
  }

  /* ── 收藏（⭐）───────────────────────────────────────────────
     收藏是**长期标记**，与「已选角色」是两个独立维度：
       收藏 = 我常想用的角色，跨会话保留；
       已选 = 这一次出图要用的角色。
     收藏不会自动变成已选，但收藏列表里可以正常点选。
     存插件目录 presets/user_favorites.json（见 anima_v3_store）。
  */

  function loadFavorites() {
    if (ui.favorites || ui.favLoading) return;
    ui.favLoading = true;
    const seq = ++favSeq;
    api.fetchApi(`/${NS}/favorites`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        // 期间发生过 toggle → 这份快照过期，丢掉（同 presetsSeq 的道理）
        if (seq !== favSeq) return;
        ui.favorites = new Set(d.favorites || []);
        ui.favLoading = false;
        if (ui.tab === "chars") refreshFavUI();
      })
      .catch((e) => {
        // 拉不到收藏不该挡住浏览 —— 降级成空集合，点 ⭐ 时再报错
        ui.favorites = new Set();
        ui.favLoading = false;
        console.error("[AnimaV3] 收藏加载失败", e);
      });
  }

  async function toggleFav(tag, on) {
    try {
      const r = await api.fetchApi(`/${NS}/favorites/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag, on }),
      });
      const d = await r.json();
      if (d.error) {
        setStatus("收藏失败：" + d.error, "no");
        return false;
      }
      // 后端返回的是**最新完整列表**，直接采用，不再多发一次 GET
      ui.favorites = new Set(d.favorites || []);
      favSeq += 1;          // 作废在途的 GET（它会覆盖成旧快照）
      setStatus(`${on ? "已收藏" : "已取消收藏"}（共 ${ui.favorites.size} 位）`, "ok");
      return true;
    } catch (e) {
      setStatus("收藏失败：" + e.message, "no");
      return false;
    }
  }

  /**
   * 就地刷新收藏相关的 UI（不重建网格 —— 重建会把滚动位置丢掉）。
   *
   * ⚠️ 这里刻意用 DOM 查询而不是 renderChars 里的 cardEls Map：
   *    收藏可能在「词库与权重」页签下被加载完，那时网格根本没建，
   *    Map 是空的。查询 DOM 任何页签下都安全。
   */
  function refreshFavUI() {
    const favs = ui.favorites || new Set();
    for (const el of left.querySelectorAll(".av3-card")) {
      const tag = el.dataset.tag;
      const st = el.querySelector(".fav");
      if (!st || !tag) continue;
      const on = favs.has(tag);
      st.textContent = on ? "★" : "☆";
      st.classList.toggle("on", on);
      st.title = on ? "取消收藏" : "加入收藏";
    }
    const row = left.querySelector(`.av3-work[data-work="${FAV_ID}"]`);
    if (row) {
      row.querySelector(".c").textContent = favs.size;
      row.classList.toggle("empty", favs.size === 0);
    }
  }

  /** 人数标签预览。规则与后端 headcount_tags 一致，这里只是即时提示。 */
  function headcountPreview(n) {
    const lvNum = levelNum(ui.level);
    if (n === 1) return "1girl, solo";
    if (n === 2) {
      return lvNum <= 1 ? "2girls, multiple girls（擦边档不用 yuri）"
                        : "2girls, yuri";
    }
    if (n >= 3) return "multiple girls（多人群像）";
    return "";
  }

  function loadChars() {
    if (ui.chars || ui.charsLoading) return;
    ui.charsLoading = true;
    api.fetchApi(`/${NS}/characters`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        ui.chars = d;
        ui.charsLoading = false;
        if (ui.tab === "chars") renderChars();
      })
      .catch((e) => {
        ui.charsLoading = false;
        console.error("[AnimaV3] 角色库加载失败", e);
        if (ui.tab === "chars") {
          left.innerHTML = `<div class="av3-empty">角色库加载失败：${e.message}<br>
            请确认 presets/anima_v3_characters.json 存在（跑 tools/build_characters.py 生成）。</div>`;
        }
      });
  }

  function matchChar(c, q) {
    if (!q) return true;
    // work_id 也纳入搜索串：用户可能从别处粘 `zenless_zone_zero` 进来
    const hay = `${c.zh} ${c.name} ${c.tag} ${c.work} ${c.work_id}`.toLowerCase();
    return hay.includes(q);
  }

  /* ── 角色编辑 / 自定义角色（v3.4.0）─────────────────────────────
     数据落在插件目录 presets/user_characters.json（内置角色库只读 ——
     它是 tools/build_characters.py 的联网重建产物，直接改会被重跑覆盖）。
     弹窗字段与后端可编辑键一一对齐：角色标签 / 中文名 / 作品 / 预览图。

     ⚠️ 角色 tag（slug）**不可改**：它是 char_index 的键、也是图片文件名，
        改它等于"删掉一个再造一个"，已保存工作流里的 selected_roles 会失效。
  */

  function findChar(tag) {
    return ((ui.chars && ui.chars.characters) || [])
      .find((c) => c.tag === tag) || null;
  }

  /** 角色写操作统一入口。返回 {ok, error, data}（data 是后端整包响应）。 */
  async function postCharJSON(path, payload) {
    try {
      const r = await api.fetchApi(`/${NS}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      let d = {};
      try { d = await r.json(); } catch (e) { d = {}; }
      return { ok: r.ok !== false && !d.error, error: d.error || "", data: d };
    } catch (e) {
      return { ok: false, error: e.message || String(e), data: {} };
    }
  }

  /** 写操作返回的是**最新完整角色表**，直接采用并重渲（见后端 _chars_resp）。 */
  function applyCharData(body) {
    if (body && body.data) ui.chars = body.data;
    if (ui.tab === "chars") renderChars();
  }

  /**
   * 拉单个角色的**完整**记录（含全量特征标签 + 内置原始标签）。
   *
   * ⚠️ 列表接口只发 tags[:8]（卡片展示够用）—— 编辑标签必须拿全量，
   *    否则用户会看到"我的标签少了"并把它当成 bug。
   * ⚠️ 用查询串而不是路径参数：角色 tag 里有 1762 条带 `(` `)` `:` 等字符。
   */
  async function getCharDetail(tag) {
    try {
      const r = await api.fetchApi(
        `/${NS}/characters/detail?tag=${encodeURIComponent(tag)}`,
        { cache: "no-store" });
      let d = {};
      try { d = await r.json(); } catch (e) { d = {}; }
      return { ok: r.ok !== false && !d.error, error: d.error || "", data: d };
    } catch (e) {
      return { ok: false, error: e.message || String(e), data: {} };
    }
  }

  function openCharEditor(rec, isNew) {
    const orig = rec || {};
    const isUser = !!orig.user;

    const maskEl = document.createElement("div");
    maskEl.className = "av3-mask";
    const inner = document.createElement("div");
    inner.className = "av3-mini";
    inner.style.position = "static";
    maskEl.appendChild(inner);
    const box = document.createElement("div");
    box.className = "card av3-editor";
    inner.appendChild(box);

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.textContent = isNew ? "添加自定义角色"
      : `编辑角色 · ${orig.zh || orig.name || orig.tag}`;
    if (!isNew) {
      const tg = document.createElement("span");
      tg.className = "tag";
      tg.textContent = orig.tag || "";
      hd.appendChild(tg);
    }
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    box.appendChild(bd);

    /* 错误行在这里就声明（DOM 里挂在最后）—— 下面好几个异步分支都要写它 */
    const err = document.createElement("div");
    err.className = "av3-eerr";

    const mkField = (label, el) => {
      const row = document.createElement("div");
      row.className = "av3-efld";
      const l = document.createElement("label");
      l.textContent = label;
      row.appendChild(l);
      row.appendChild(el);
      bd.appendChild(row);
      return el;
    };

    const zhIn = document.createElement("input");
    zhIn.type = "text";
    zhIn.maxLength = 24;
    zhIn.placeholder = "留空则显示英文名";
    zhIn.value = orig.zh || "";
    mkField("中文名", zhIn);

    const workIn = document.createElement("input");
    workIn.type = "text";
    workIn.maxLength = 60;
    workIn.placeholder = "选现有作品，或直接输一个新的";
    workIn.value = orig.work || "";
    workIn.setAttribute("list", "av3-worklist");
    mkField("作品", workIn);
    const dl = document.createElement("datalist");
    dl.id = "av3-worklist";
    for (const w of ((ui.chars && ui.chars.works) || [])) {
      const o = document.createElement("option");
      o.value = w.en;
      o.label = w.zh || w.en;
      dl.appendChild(o);
    }
    bd.appendChild(dl);

    const trigIn = document.createElement("input");
    trigIn.type = "text";
    trigIn.placeholder = "如 hatsune miku, vocaloid（英文小写）";
    trigIn.value = orig.trigger || "";
    mkField("角色标签", trigIn);

    /* 预览图：本地选图 → 存成 data URL，**点保存时才上传**
       （避免用户选了图又取消，磁盘里留一张没人引用的图）。 */
    const thumbRow = document.createElement("div");
    thumbRow.className = "av3-ethumb";
    const pv = document.createElement("div");
    pv.className = "pv";
    const paintPreview = (src) => {
      pv.innerHTML = "";
      if (src) {
        const im = document.createElement("img");
        im.src = src;
        im.onerror = () => {
          pv.innerHTML = "";
          pv.textContent = (orig.zh || orig.name || "?").slice(0, 1).toUpperCase();
        };
        pv.appendChild(im);
      } else {
        pv.textContent = (orig.zh || orig.name || "?").slice(0, 1).toUpperCase();
      }
    };
    paintPreview(orig.thumb ? `/${NS}/thumb/${orig.thumb}` : "");
    thumbRow.appendChild(pv);

    const ops = document.createElement("div");
    ops.className = "ops";
    let pendingImage = null;

    const fileIn = document.createElement("input");
    fileIn.type = "file";
    fileIn.accept = "image/png,image/jpeg,image/webp";
    fileIn.style.display = "none";

    const pickBtn = document.createElement("button");
    pickBtn.className = "av3-btn";
    pickBtn.textContent = "🖼 选择本地图片";
    pickBtn.onclick = () => fileIn.click();
    fileIn.onchange = () => {
      const f = fileIn.files && fileIn.files[0];
      if (!f) return;
      if (!/^image\/(png|jpeg|webp)$/.test(f.type || "")) {
        err.textContent = "只支持 PNG / JPG / WEBP 图片";
        return;
      }
      if (f.size > 2 * 1024 * 1024) {
        err.textContent = "图片过大（上限 2MB）";
        return;
      }
      err.textContent = "";
      const rd = new FileReader();
      rd.onload = () => {
        pendingImage = String(rd.result || "");
        paintPreview(pendingImage);
      };
      rd.readAsDataURL(f);
    };

    const clrThumb = document.createElement("button");
    clrThumb.className = "av3-btn";
    clrThumb.textContent = "↩ 清除自定义图片";
    clrThumb.title = "删掉上传的图，回到内置图 / 首字母色块";
    clrThumb.disabled = isNew;
    clrThumb.onclick = async () => {
      if (!orig.tag) return;
      const r = await postCharJSON("characters/thumb-clear", { tag: orig.tag });
      if (!r.ok) {
        err.textContent = r.error || "清除失败";
        return;
      }
      pendingImage = null;
      const found = findChar(orig.tag);
      paintPreview(found && found.thumb ? `/${NS}/thumb/${found.thumb}` : "");
      applyCharData(r.data);
      setStatus("已清除自定义图片", "ok");
    };

    const row1 = document.createElement("div");
    row1.className = "row";
    row1.appendChild(pickBtn);
    row1.appendChild(clrThumb);
    const hint = document.createElement("div");
    hint.className = "av3-meta";
    hint.textContent = "PNG / JPG / WEBP，≤2MB；保存时自动缩到 160px。"
      + "留空 = 用内置图或首字母色块。";
    ops.appendChild(row1);
    ops.appendChild(hint);
    ops.appendChild(fileIn);
    thumbRow.appendChild(ops);
    bd.appendChild(thumbRow);

    /* ── 特征标签（v3.5.0）────────────────────────────────────────
       这是**列表**字段（与 trigger/中文名/作品那种字符串不同），所以 UI 用
       「逐行输入框 + ×」：增 = ＋ 添加标签，删 = 点 ×，改 = 直接改那一行。

       ⚠️ 全量标签要**按需拉** /characters/detail —— 列表接口只带 tags[:8]
          （卡片展示用）。不拉全量的话用户会看到"我的标签少了"。
       ⚠️ 详情没到位前**禁止保存**：否则会把 tags 当空发出，
          静默清掉已有的覆盖（这个坑比"少存一条"严重得多）。
       ⚠️ 保存时若标签与内置完全一致 → 发空数组（= 不写覆盖），
          这样"恢复内置标签"能真的把覆盖清掉，而不是写一份和内置一样的副本。 */
    let tagsLoaded = isNew;
    let onTagsReady = null;
    let builtinTags = [];

    const normTags = (arr) => (arr || [])
      .map((s) => String(s == null ? "" : s).trim().toLowerCase())
      .filter(Boolean);
    const sameTags = (a, b) => {
      const x = normTags(a);
      const y = normTags(b);
      return x.length === y.length && x.every((v, i) => v === y[i]);
    };

    const tagSec = document.createElement("div");
    tagSec.className = "av3-tagsec";
    const tagHead = document.createElement("div");
    tagHead.className = "av3-taghead";
    const tagTitle = document.createElement("span");
    tagTitle.className = "ttl";
    tagTitle.textContent = "特征标签";
    tagHead.appendChild(tagTitle);
    const tagMeta = document.createElement("span");
    tagMeta.className = "av3-meta";
    tagHead.appendChild(tagMeta);
    const tagRestore = document.createElement("button");
    tagRestore.className = "av3-btn";
    tagRestore.textContent = "↩ 恢复内置标签";
    tagRestore.title = "把标签还原为内置值（保存后即清除你的覆盖）";
    tagRestore.style.display = "none";
    tagHead.appendChild(tagRestore);
    tagSec.appendChild(tagHead);

    const tagRows = document.createElement("div");
    tagRows.className = "av3-tagrows";
    tagSec.appendChild(tagRows);

    const tagFoot = document.createElement("div");
    tagFoot.className = "av3-tagfoot";
    const addTagBtn = document.createElement("button");
    addTagBtn.className = "av3-btn";
    addTagBtn.textContent = "＋ 添加标签";
    const tagCount = document.createElement("span");
    tagCount.className = "av3-meta";
    tagFoot.appendChild(addTagBtn);
    tagFoot.appendChild(tagCount);
    tagSec.appendChild(tagFoot);

    const tagHint = document.createElement("div");
    tagHint.className = "av3-meta";
    tagHint.textContent = "用在外貌对齐：体型标签（large breasts / flat chest 等）"
      + "直接生效；发色瞳色标签只在该角色没有发色瞳色记录时才生效。"
      + "单个 ≤40 字符（更长的会被引擎忽略），最多 40 条。"
      + "清空全部 = 恢复内置标签。";
    tagSec.appendChild(tagHint);

    const tagInputs = () => [...tagRows.querySelectorAll("input")];
    const tagValues = () => tagInputs()
      .map((i) => String(i.value || "").trim()).filter(Boolean);

    function paintTagCount() {
      tagCount.textContent = `已 ${tagValues().length} 条（上限 40）`;
    }

    function mkTagRow(value) {
      const row = document.createElement("div");
      row.className = "av3-tagrow";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.maxLength = 40;
      inp.value = value || "";
      inp.placeholder = "如 large breasts";
      inp.oninput = paintTagCount;
      const del = document.createElement("button");
      del.className = "av3-btn";
      del.textContent = "\u00d7";
      del.title = "删除这条特征标签";
      del.onclick = () => { row.remove(); paintTagCount(); };
      row.appendChild(inp);
      row.appendChild(del);
      tagRows.appendChild(row);
      return inp;
    }

    function setTagRows(list) {
      tagRows.innerHTML = "";
      for (const t of list) mkTagRow(t);
      paintTagCount();
    }

    addTagBtn.onclick = () => {
      const i = mkTagRow("");
      paintTagCount();
      if (i.focus) i.focus();
    };
    tagRestore.onclick = () => {
      setTagRows(builtinTags);
      tagRestore.style.display = "none";
      tagMeta.textContent = `内置 ${builtinTags.length} 条（保存后清除覆盖）`;
      err.textContent = "";
    };

    function loadTags() {
      if (isNew) {
        tagsLoaded = true;
        setTagRows([]);
        tagMeta.textContent = "新角色（无内置标签）";
        tagRestore.style.display = "none";
        if (onTagsReady) onTagsReady();
        return;
      }
      tagRows.innerHTML = '<div class="av3-meta">正在读取特征标签…</div>';
      tagMeta.textContent = "";
      getCharDetail(orig.tag).then((res) => {
        if (!res.ok) {
          tagRows.innerHTML = "";
          const retry = document.createElement("button");
          retry.className = "av3-btn";
          retry.textContent = "↻ 重试读取";
          retry.onclick = () => { err.textContent = ""; loadTags(); };
          tagRows.appendChild(retry);
          err.textContent = "特征标签读取失败：" + (res.error || "未知原因")
            + "（已禁止保存，避免误清空）";
          return;
        }
        builtinTags = res.data.builtin_tags || [];
        setTagRows(res.data.tags || []);
        tagMeta.textContent = builtinTags.length
          ? `内置 ${builtinTags.length} 条${res.data.tags_overridden ? " · 已被修改" : ""}`
          : "内置无标签";
        tagRestore.style.display = builtinTags.length ? "" : "none";
        tagsLoaded = true;
        if (onTagsReady) onTagsReady();
      });
    }
    loadTags();

    bd.appendChild(tagSec);
    bd.appendChild(err);

    const ft = document.createElement("div");
    ft.className = "ft";
    const mkBtn2 = (text, cls, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (cls ? " " + cls : "");
      b.textContent = text;
      b.onclick = fn;
      return b;
    };

    if (!isNew) {
      // 自定义 → 删除；内置 → 恢复默认。
      // 两步确认而不是 window.confirm（jsdom 没实现 confirm，测试跑不了）。
      const dangerText = isUser ? "🗑 删除角色" : "↺ 恢复默认";
      let armed = false;
      let timer = null;
      const danger = mkBtn2(dangerText, "", async () => {
        if (!armed) {
          armed = true;
          danger.textContent = "⚠ 再点一次确认";
          timer = setTimeout(() => {
            armed = false;
            danger.textContent = dangerText;
          }, 4000);
          return;
        }
        if (timer) clearTimeout(timer);
        const r = await postCharJSON(
          isUser ? "characters/delete" : "characters/reset", { tag: orig.tag });
        if (!r.ok) {
          err.textContent = r.error || "操作失败";
          return;
        }
        applyCharData(r.data);
        maskEl.remove();
        setStatus(isUser ? "已删除自定义角色" : "已恢复默认", "ok");
      });
      ft.appendChild(danger);
    }

    ft.appendChild(mkBtn2("取消", "", () => maskEl.remove()));

    const save = mkBtn2(isNew ? "添加角色" : "保存", "pri", async () => {
      /* ⚠️ 详情没到位就不许保存 —— 否则 tags 会被当空发出，
         静默清掉用户已有的标签覆盖。 */
      if (!tagsLoaded) {
        err.textContent = "特征标签还没读出来，先点上面的「↻ 重试读取」";
        return;
      }
      const trigger = String(trigIn.value || "").trim();
      if (!trigger) {
        err.textContent = "角色标签不能为空";
        return;
      }
      // 与内置完全一致 → 发空数组（= 不写覆盖），"恢复内置标签"才真的清得掉
      let tagList = tagValues();
      if (!isNew && sameTags(tagList, builtinTags)) tagList = [];
      const fields = {
        trigger,
        zh: String(zhIn.value || "").trim(),
        work: String(workIn.value || "").trim(),
        tags: tagList,
      };
      save.disabled = true;
      const res = await postCharJSON("characters/save", {
        mode: isNew ? "create" : "edit",
        tag: orig.tag || "",
        fields,
      });
      if (!res.ok) {
        err.textContent = res.error || "保存失败";
        save.disabled = false;
        return;
      }
      const tag = (res.data.character && res.data.character.tag) || orig.tag;
      let last = res.data;
      if (pendingImage && tag) {
        const r2 = await postCharJSON("characters/thumb",
                                      { tag, data_url: pendingImage });
        if (!r2.ok) {
          applyCharData(res.data);
          err.textContent = "角色已保存，但图片保存失败：" + (r2.error || "");
          save.disabled = false;
          return;
        }
        last = r2.data;
      }
      applyCharData(last);
      maskEl.remove();
      if (isNew) {
        // 新角色人气为 0，按人气排序会落到很后面 —— 直接把搜索框填成它，
        // 用户一睁眼就看见刚加的那位（不然会以为"添加没生效"）。
        const c = (last.data && last.data.characters || [])
          .find((x) => x.tag === tag);
        ui.charWork = "";
        ui.charQuery = String((c && (c.zh || c.name)) || "").toLowerCase();
        ui.charLimit = 96;
        renderChars();
        setStatus("已添加自定义角色（左侧列表已筛选到它）", "ok");
      } else {
        setStatus("已保存角色修改", "ok");
      }
    });
    ft.appendChild(save);
    box.appendChild(ft);

    /* 特征标签没读出来之前，保存是禁用的（见 loadTags 的注释） */
    save.disabled = !tagsLoaded;
    onTagsReady = () => { save.disabled = false; };

    maskEl.onclick = (e) => {
      if (e.target === maskEl) maskEl.remove();
    };
    document.body.appendChild(maskEl);
    if (trigIn.focus) trigIn.focus();
  }

  function renderChars() {
    loadFavorites();
    const d = ui.chars;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载角色库…</div>`;
      loadChars();
      return;
    }
    if (!d.available || !d.characters.length) {
      left.innerHTML = `<div class="av3-empty">角色库还是空的。<br>
        先跑 <code>python tools/build_characters.py</code> 生成
        <code>presets/anima_v3_characters.json</code>，<br>
        再跑 <code>tools/fetch_character_thumbs.py</code> 补缩略图。</div>`;
      return;
    }

    left.innerHTML = "";
    const sel = selectedList();

    /* 顶部操作条 */
    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    const qIn = document.createElement("input");
    qIn.type = "text";
    qIn.placeholder = "搜索角色（中文名 / 英文名 / tag / 作品）";
    qIn.value = ui.charQuery;
    qIn.oninput = () => {
      ui.charQuery = qIn.value.trim().toLowerCase();
      ui.charLimit = 96;
      renderGrid();
    };
    bar.appendChild(qIn);
    const addBtn = document.createElement("button");
    addBtn.className = "av3-btn pri";
    addBtn.textContent = "＋ 添加角色";
    addBtn.title = "新增一个自定义角色：自填角色标签、中文名、作品与预览图";
    addBtn.onclick = () => openCharEditor(null, true);
    bar.appendChild(addBtn);
    const clearBtn = document.createElement("button");
    clearBtn.className = "av3-btn";
    clearBtn.textContent = "清除已选角色";
    clearBtn.disabled = !sel.length;
    clearBtn.onclick = () => {
      writeSelected([]);
      renderChars();
      setStatus("已清除全部已选角色", "");
    };
    bar.appendChild(clearBtn);

    /* 🎲 按当前筛选随机抽角色（v3.8.0）
       候选池 = 当前的「作品筛选 + 关键词 + 收藏视图」结果 —— 也就是你在左边
       点某个 IP、或在搜索框里打了字之后看到的那些人。
       抽到的角色**追加**进「已选角色」（与手动点卡片同一语义：都是"加一位"），
       想换一批先点「清除已选角色」。

       ⚠️ 为什么不新增节点控件：抽到谁最终落在 `selected_roles` 里，而它本来
          就随工作流保存 —— 所以"抽到谁"是可复现的，不需要再开一个控件口径。
          控件一旦新增就要配工作流迁移脚本，为了一个面板动作不值当。 */
    const rndN = document.createElement("input");
    rndN.type = "number";
    rndN.min = "1";
    rndN.max = "6";
    rndN.step = "1";
    rndN.value = String(ui.charRndN || 1);
    rndN.title = "一次抽几位（1–6）";
    rndN.style.cssText = "width:56px;flex:0 0 auto";
    rndN.onchange = () => {
      const v = Math.max(1, Math.min(6, Math.round(Number(rndN.value) || 1)));
      rndN.value = String(v);
      ui.charRndN = v;
    };
    bar.appendChild(rndN);
    const rndBtn = document.createElement("button");
    rndBtn.className = "av3-btn";
    rndBtn.textContent = "🎲 随机抽";
    rndBtn.title = "从当前筛选结果里随机抽角色，直接加入「已选角色」";
    rndBtn.onclick = () => {
      const pool = filtered();
      if (!pool.length) {
        setStatus("当前筛选下没有角色可抽（先选个作品或清掉关键词）", "no");
        return;
      }
      const cur = selectedList();
      const have = new Set(cur);
      const rest = pool.filter((c) => !have.has(c.tag));
      if (!rest.length) {
        setStatus(`当前筛选的 ${pool.length} 位已经全选上了`, "");
        return;
      }
      const want = Math.max(1, Math.min(6, Math.round(Number(rndN.value) || 1)));
      const take = Math.min(want, rest.length);
      // Fisher–Yates：不重复地抽 take 位（同一位角色不能出现两次，
      // 否则模型会把它当成两个人 —— 与后端 pick_random_roles 同一条理由）
      const arr = rest.slice();
      for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        const t = arr[i];
        arr[i] = arr[j];
        arr[j] = t;
      }
      const got = arr.slice(0, take);
      writeSelected(cur.concat(got.map((c) => c.tag)));
      paintChips();
      paintCards();
      setStatus(`🎲 从「${ui.charWork || "全部作品"}」抽到 `
        + got.map((c) => c.zh || c.name).join("、")
        + `（共 ${cur.length + got.length} 位）`, "ok");
    };
    bar.appendChild(rndBtn);
    left.appendChild(bar);

    /* 已选角色 chips（带人数标签预览） */
    const chipBox = document.createElement("div");
    chipBox.className = "av3-chips";
    left.appendChild(chipBox);

    /* ── 随机池（role_works，v3.10.0）───────────────────────────────
       「已选角色」是"抽一次就固定"（写 selected_roles）；
       「随机池」是"每跑一次自动换"（写 role_works，不写 selected_roles）。

       两个维度必须**分开**：前者是这次出图要用的角色，后者是"从哪个作品
       范围里每次重新抽"。合成一个入口的话，用户永远分不清"我点了 🎲 之后
       到底是固定了还是每次换"。 */
    const poolBox = document.createElement("div");
    poolBox.className = "av3-chips";
    left.appendChild(poolBox);

    function poolList() {
      return String(lsGet(node, "role_works", "") || "")
        .split(/[|;\n]/).map((s) => s.trim()).filter(Boolean);
    }

    function writePool(list) {
      wset(node, "role_works", list.join("|"));
      app.graph.setDirtyCanvas(true, true);
    }

    const workLabelOf = (slug) => {
      const w = (d.works || []).find((x) => x.id === slug);
      return w ? (w.zh || w.en) : slug;
    };

    function paintPool() {
      const cur = poolList();
      const sel = selectedList();
      poolBox.innerHTML = "";
      const lead = document.createElement("span");
      lead.className = "av3-meta";
      lead.textContent = "🔁 随机池：";
      poolBox.appendChild(lead);

      if (!cur.length) {
        const hint = document.createElement("span");
        hint.className = "av3-meta";
        hint.textContent = "（未限定）—— 点左边某个作品后按「＋ 加入随机池」";
        poolBox.appendChild(hint);
      }
      cur.forEach((slug) => {
        const chip = document.createElement("span");
        chip.className = "av3-chip";
        chip.dataset.pool = slug;
        chip.textContent = workLabelOf(slug);
        chip.title = `每次运行都从这个作品的角色里重新抽（${slug}）`;
        const x = document.createElement("b");
        x.textContent = "\u00d7";
        x.title = "从这个池子里移除";
        x.onclick = () => {
          writePool(cur.filter((s) => s !== slug));
          paintPool();
          setStatus(`已把「${workLabelOf(slug)}」移出随机池`, "");
        };
        chip.appendChild(x);
        poolBox.appendChild(chip);
      });

      const addBtn = document.createElement("button");
      addBtn.className = "av3-btn";
      addBtn.textContent = "＋ 加入随机池";
      addBtn.title = "把当前选中的作品加入「每次自动换」的随机池";
      addBtn.onclick = () => {
        const w = ui.charWork;
        if (!w || w === FAV_ID) {
          setStatus("先在左侧点一个作品 IP，再加入随机池", "no");
          return;
        }
        if (cur.includes(w)) {
          setStatus(`「${workLabelOf(w)}」已经在随机池里了`, "");
          return;
        }
        writePool(cur.concat([w]));
        paintPool();
        setStatus(`已把「${workLabelOf(w)}」加入随机池 —— 每次运行都会换人`
          + (selectedList().length ? "（⚠️ 但已选角色优先，先清除已选才会生效）" : ""),
          selectedList().length ? "no" : "ok");
      };
      poolBox.appendChild(addBtn);

      if (cur.length) {
        const clr = document.createElement("button");
        clr.className = "av3-btn";
        clr.textContent = "清空随机池";
        clr.onclick = () => {
          writePool([]);
          paintPool();
          setStatus("已清空随机池（回到全库随机）", "");
        };
        poolBox.appendChild(clr);
      }

      const note = document.createElement("span");
      note.className = "av3-meta";
      if (sel.length) {
        note.textContent = "⚠️ 已选角色优先：随机池要等「清除已选角色」后才生效";
        note.classList.add("av3-no");
      } else if (cur.length) {
        note.textContent = "每次运行都从池子里重新抽一位（种子 -1 时）";
      }
      poolBox.appendChild(note);
    }

    function paintChips() {
      const cur = selectedList();
      chipBox.innerHTML = "";
      if (!cur.length) {
        const hint = document.createElement("span");
        hint.className = "av3-meta";
        hint.textContent = "未选角色 —— 角色会完全随机。点下面的卡片即选中（抽取概率 100%），"
          + "或用 🎲 按当前筛选随机抽。";
        chipBox.appendChild(hint);
        clearBtn.disabled = true;
        return;
      }
      clearBtn.disabled = false;
      const idx = new Map((d.characters || []).map((c) => [c.tag, c]));
      cur.forEach((tag) => {
        const c = idx.get(tag);
        const chip = document.createElement("span");
        chip.className = "av3-chip";
        chip.textContent = c ? (c.zh || c.name) : tag;
        const x = document.createElement("b");
        x.textContent = "\u00d7";
        x.title = "移除";
        x.onclick = () => {
          writeSelected(cur.filter((t) => t !== tag));
          paintChips();
          paintPool();
          paintCards();
        };
        chip.appendChild(x);
        chipBox.appendChild(chip);
      });
      const note = document.createElement("span");
      note.className = "av3-meta";
      note.style.marginLeft = "6px";
      note.textContent = `共 ${cur.length} 位 → 人数标签将写 \`${headcountPreview(cur.length)}\``;
      chipBox.appendChild(note);
    }

    /* 主体：左作品列表 / 右角色网格 */
    const body2 = document.createElement("div");
    body2.className = "av3-cbody";
    const workBox = document.createElement("div");
    workBox.className = "av3-works";
    const gridBox = document.createElement("div");
    gridBox.className = "av3-grid";
    body2.appendChild(workBox);
    body2.appendChild(gridBox);
    left.appendChild(body2);

    /* 作品列表（按角色数排序，★ = 保底收录的目标作品） */
    const works = d.works || [];
    const mkWork = (id, label, count, isTarget) => {
      const row = document.createElement("div");
      row.className = "av3-work" + (ui.charWork === id ? " on" : "")
        + (isTarget ? " tgt" : "");
      row.dataset.work = id;      // refreshFavUI 靠它定位「我的收藏」那一行
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = label;
      const c = document.createElement("span");
      c.className = "c";
      c.textContent = count;
      row.appendChild(n);
      row.appendChild(c);
      row.onclick = () => {
        ui.charWork = id;
        ui.charLimit = 96;
        renderChars();
      };
      return row;
    };
    workBox.appendChild(mkWork("", "全部作品", d.characters.length, false));
    // 「⭐ 我的收藏」紧跟「全部作品」—— 这两个都是"筛选视图"，
    // 与下面按作品分组的列表不是一类，放一起最好找。
    const favRow = mkWork(FAV_ID, "⭐ 我的收藏",
                          (ui.favorites || new Set()).size, false);
    if (!(ui.favorites || new Set()).size) favRow.classList.add("empty");
    workBox.appendChild(favRow);
    works.forEach((w) => {
      workBox.appendChild(mkWork(w.id, w.zh || w.en, w.count, w.target));
    });

    /* 角色网格 */
    const cardEls = new Map();

    function filtered() {
      const q = ui.charQuery;
      const favs = ui.favorites || new Set();
      // ⚠️⚠️ 按作品筛选必须用 **work_id**（作品 slug），不能用 c.work。
      //    后端给的角色里，`work` 是**作品英文名**（`zenless zone zero`），
      //    而作品行的 id 是 slug（`zenless_zone_zero`）——
      //    拿两者直接比会永远不相等，表现就是「点任何 IP 作品都是空列表」。
      //    这是 v3.1.0 的线上 bug，根因记在 nodes.characters_payload() 里。
      //
      //    FAV_ID（⭐ 我的收藏）是个**伪作品筛选**，它按收藏集合判，不走 work_id。
      const list = (d.characters || []).filter((c) => {
        if (ui.charWork === FAV_ID) {
          if (!favs.has(c.tag)) return false;
        } else if (ui.charWork && c.work_id !== ui.charWork) {
          return false;
        }
        return matchChar(c, q);
      });
      // ⚠️ 面板自己按人气降序排，不依赖后端给的顺序。
      //    后端确实已经排好了，但"默认按人气排行展示"是这个界面的**契约**，
      //    靠上游顺序实现的话，哪天接口换个排序就悄悄坏掉。
      //    filter() 已经返回新数组，这里 sort 不会污染源数据。
      list.sort((a, b) => (b.pop || 0) - (a.pop || 0));
      return list;
    }

    function paintCards() {
      const cur = new Set(selectedList());
      for (const [tag, el] of cardEls) {
        const on = cur.has(tag);
        el.classList.toggle("on", on);
        let tick = el.querySelector(".tick");
        if (on && !tick) {
          tick = document.createElement("span");
          tick.className = "tick";
          tick.textContent = "\u2713";
          el.appendChild(tick);
        } else if (!on && tick) {
          tick.remove();
        }
      }
    }

    function renderGrid() {
      gridBox.innerHTML = "";
      cardEls.clear();
      const list = filtered();
      const shown = list.slice(0, ui.charLimit);

      if (!list.length) {
        const e = document.createElement("div");
        e.className = "av3-empty";
        const favN = (ui.favorites || new Set()).size;
        if (ui.charWork === FAV_ID && !favN) {
          e.innerHTML = "还没有收藏任何角色。<br>"
            + "点角色卡片右上角的 <b>☆</b> 即可收藏 —— "
            + "收藏存在插件目录里，换浏览器、重开 ComfyUI 都还在。";
        } else if (ui.charWork === FAV_ID) {
          e.textContent = "当前关键词下没有已收藏的角色。";
        } else {
          e.textContent = "没有匹配的角色。换个关键词，或点左上「全部作品」。";
        }
        gridBox.appendChild(e);
        return;
      }

      for (const c of shown) {
        const card = document.createElement("div");
        card.className = "av3-card";
        card.dataset.tag = c.tag;      // 测试与调试时用来精确定位到某个角色
        card.title = `${c.zh || c.name}\n${c.tag}\n${c.work} · 人气 ${c.pop}`;

        // 缩略图：抓不到就退化成首字母色块（不阻断浏览）
        const img = document.createElement("img");
        img.className = "ph";
        img.loading = "lazy";
        img.alt = c.zh || c.name;
        img.src = `/${NS}/thumb/${c.thumb}`;
        img.onerror = () => {
          const ph = document.createElement("div");
          ph.className = "ph letter";
          ph.textContent = (c.zh || c.name || "?").slice(0, 1).toUpperCase();
          img.replaceWith(ph);
        };
        card.appendChild(img);

        /* 收藏星（卡片右上角）。人气徽标改到名字右边 ——
           那个角原本是它，现在要让给 ⭐，两个都堆在右上会叠住。 */
        const favOn = (ui.favorites || new Set()).has(c.tag);
        const star = document.createElement("span");
        star.className = "fav" + (favOn ? " on" : "");
        star.textContent = favOn ? "★" : "☆";
        star.title = favOn ? "取消收藏" : "加入收藏";
        star.onclick = (e) => {
          // ⚠️ 必须挡住冒泡 —— 否则点收藏会顺带把这张卡选中 / 取消选中
          e.stopPropagation();
          const want = !(ui.favorites || new Set()).has(c.tag);
          toggleFav(c.tag, want).then((ok) => {
            if (!ok) return;
            // 在「⭐ 我的收藏」视图里取消收藏 → 卡片应当消失，必须重渲网格。
            // 注意 refreshFavUI 两种情况都要调：它负责更新计数行，
            // 而 renderGrid 只管网格 —— 漏了它计数就停在旧值上。
            if (ui.charWork === FAV_ID) renderGrid();
            refreshFavUI();
          });
        };
        card.appendChild(star);

        /* 编辑入口（卡片右下角）。放右下是因为左上给了「选中打勾」、
           右上给了收藏星，右下是唯一不撞的角。 */
        const editBtn = document.createElement("span");
        editBtn.className = "edit";
        editBtn.textContent = "\u270E";
        editBtn.title = "编辑这个角色（标签 / 中文名 / 作品 / 预览图）";
        editBtn.onclick = (e) => {
          // 同收藏星：必须挡住冒泡，否则点编辑会顺带把这张卡选中
          e.stopPropagation();
          openCharEditor(c, false);
        };
        card.appendChild(editBtn);

        if (c.user) {
          const bd = document.createElement("span");
          bd.className = "av3-badge";
          bd.textContent = "自定义";
          bd.title = "这是你自己添加的角色（存在 presets/user_characters.json）";
          card.appendChild(bd);
        }

        const nmRow = document.createElement("div");
        nmRow.className = "nmRow";
        const nm = document.createElement("div");
        nm.className = "nm";
        nm.textContent = c.zh || c.name;
        const pp = document.createElement("span");
        pp.className = "pp";
        pp.textContent = c.pop >= 1000 ? `${Math.round(c.pop / 1000)}k` : String(c.pop);
        nmRow.appendChild(nm);
        nmRow.appendChild(pp);
        card.appendChild(nmRow);

        const en = document.createElement("div");
        en.className = "en";
        en.textContent = c.name;
        card.appendChild(en);

        if (new Set(selectedList()).has(c.tag)) {
          const tick = document.createElement("span");
          tick.className = "tick";
          tick.textContent = "\u2713";
          card.appendChild(tick);
        }

        card.onclick = () => {
          // 单/多选：点一次选中，再点取消。人数标签由后端按个数自动定，
          // 这里只负责把 tag 写进控件。
          const cur = selectedList();
          const i = cur.indexOf(c.tag);
          if (i >= 0) cur.splice(i, 1);
          else cur.push(c.tag);
          writeSelected(cur);
          paintChips();
          paintPool();
          paintCards();
        };

        gridBox.appendChild(card);
        cardEls.set(c.tag, card);
      }

      if (list.length > shown.length) {
        const more = document.createElement("div");
        more.className = "av3-more";
        const b = document.createElement("button");
        b.className = "av3-btn";
        b.textContent = `显示更多（还有 ${list.length - shown.length} 位）`;
        b.onclick = () => {
          ui.charLimit += 96;
          renderGrid();
        };
        more.appendChild(b);
        gridBox.appendChild(more);
      } else {
        const done = document.createElement("div");
        done.className = "av3-more av3-meta";
        done.textContent = `共 ${list.length} 位`;
        gridBox.appendChild(done);
      }
    }

    paintChips();
    paintPool();
    renderGrid();
  }

  /**
   * 档位按钮的选中态 + 重渲左列。
   *
   * ⚠️ 必须挂在 buildPanel 作用域，不能塞在 renderRight 里 ——
   *    档位按钮是在**头部**创建的，它的 onclick 闭包看不到
   *    renderRight 内部的函数。之前放在里面，点档位会直接
   *    `ReferenceError: syncLevel is not defined`，档位根本切不动。
   *    这是 UI 冒烟抓出来的（jsdom 的 virtualConsole 报的错）。
   */
  function syncLevel() {
    for (const [k, b] of Object.entries(lvBtns)) b.classList.toggle("on", k === ui.level);
    // 走 syncTabs 而不是 renderTree：否则在「角色库」页切档位会被权重树顶掉
    syncTabs();
  }

  function syncTabs() {
    for (const [k, b] of Object.entries(tabBtns)) b.classList.toggle("on", k === ui.tab);
    if (ui.tab === "chars") {
      loadChars();
      loadFavorites();
      if (ui.chars) renderChars();
      else left.innerHTML = `<div class="av3-empty">正在加载角色库…</div>`;
    } else if (ui.tab === "scenes") {
      loadScenes();
      if (ui.scenes) renderScenes();
      else left.innerHTML = `<div class="av3-empty">正在加载场景搭配库…</div>`;
    } else {
      renderTree();
    }
  }

  /* ── 场景搭配（v3.9.0）───────────────────────────────────────────
     与角色库同一条纪律：**点到这一页才拉**数据；选中状态落在**权重表**里
     （`fields.n_canon = 3.0` + `options.n_canon` 只有本预设 = 3.0），
     所以不新增任何节点控件、不需要工作流迁移脚本，且随工作流保存。

     ⚠️ 为什么不新增控件：节点侧**没有"字段取值"控件** —— 字段值只能来自
        随机 / 权重 / force_role / 预设。预设这条路的承载位就是 n_canon，
        它已经在权重表里有位置，复用它比新开一个控件干净得多。
  */
  function loadScenes() {
    if (ui.scenes || ui.scenesLoading) return;
    ui.scenesLoading = true;
    api.fetchApi(`/${NS}/scenes`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        ui.scenes = d;
        ui.scenesLoading = false;
        if (ui.tab === "scenes") renderScenes();
      })
      .catch((e) => {
        ui.scenesLoading = false;
        console.error("[AnimaV3] 场景搭配库加载失败", e);
        if (ui.tab === "scenes") {
          left.innerHTML = `<div class="av3-empty">场景搭配库加载失败：${e.message}</div>`;
        }
      });
  }

  /** 当前套用的场景 id（从权重表反推：字段必现 + 该选项 3.0）。 */
  function appliedSceneId() {
    if (Number(ui.weights.fields.n_canon ?? 1) < 3) return "";
    for (const [en, v] of Object.entries(ui.weights.options.n_canon || {})) {
      if (en.startsWith("sc_") && Number(v) >= 3) return en;
    }
    return "";
  }

  /** n_canon 的全部候选（法典 + 场景），来自 /pools —— 与引擎同源。 */
  function nCanonOptions() {
    const f = findField("n_canon");
    return (f && f.opts ? f.opts : []).map((o) => o.en);
  }

  /** 一键套用：字段必现 + 其它预设归零 + 本预设命中。 */
  function applyScene(sc) {
    const all = nCanonOptions();
    if (!all.length) {
      setStatus("找不到 n_canon 字段，无法套用场景", "no");
      return;
    }
    ui.weights.fields.n_canon = 3.0;
    const bag = {};
    for (const en of all) bag[en] = 0;
    bag[sc.id] = 3.0;
    ui.weights.options.n_canon = bag;
    // ⚠️ 段权重不能是 0 —— 否则 persist() 会把 canon 写进 disabled_sections，
    //    整个预设段被关掉，"套用了却没生效"。
    if (Number(ui.weights.sections.canon ?? 1) === 0) ui.weights.sections.canon = 1;
    persist();
    if (ui.tab === "weights") renderTree();
    renderScenes();
    setStatus(`已套用「${sc.zh}」：这些字段被固定，不再参与随机`, "ok");
  }

  function clearScene() {
    ui.weights.fields.n_canon = 1.0;
    ui.weights.options.n_canon = {};
    persist();
    if (ui.tab === "weights") renderTree();
    renderScenes();
    setStatus("已取消场景套用（预设回到随机）", "");
  }

  function renderScenes() {
    const d = ui.scenes;
    if (!d) {
      left.innerHTML = `<div class="av3-empty">正在加载场景搭配库…</div>`;
      loadScenes();
      return;
    }
    if (!d.available || !(d.scenes || []).length) {
      left.innerHTML = `<div class="av3-empty">场景搭配库是空的。<br>
        确认 <code>presets/anima_v3_scenes.json</code> 存在，
        必要时跑 <code>python tools/build_scenes.py</code> 重建。</div>`;
      return;
    }

    left.innerHTML = "";
    const applied = appliedSceneId();

    const tip = document.createElement("div");
    tip.className = "av3-meta";
    tip.innerHTML = "把 <b>姿势 / 场景 / 表情 / 动作</b> 的固定组合一次套用 ——"
      + "套用到的字段<b>不再参与随机</b>（随机只作用于没写进预设的字段），"
      + "也不会被标签数裁剪削掉。<br>"
      + "按 <b>NSFW 档位</b> 分组：档位不够的搭配根本不会出现在候选池里，"
      + "所以全年龄档点不出重口搭配。<br>"
      + "⚠️ 预设<b>不写人数与角色</b>：那两项仍由你点选（或按人数组合自动抽）。";
    tip.style.marginBottom = "8px";
    left.appendChild(tip);

    const bar = document.createElement("div");
    bar.className = "av3-charbar";
    const cur = (d.scenes || []).find((s) => s.id === applied);
    const st = document.createElement("span");
    st.className = "av3-meta";
    st.id = "av3-scene-cur";
    st.textContent = cur ? `当前套用：${cur.zh}（L${cur.level}）` : "当前未套用场景";
    bar.appendChild(st);
    if (cur) {
      const cb = document.createElement("button");
      cb.className = "av3-btn";
      cb.textContent = "取消套用";
      cb.onclick = () => clearScene();
      bar.appendChild(cb);
    }
    const sum = document.createElement("span");
    sum.className = "av3-meta";
    const bl = (d.counters && d.counters.by_level) || {};
    sum.textContent = "共 " + (d.scenes || []).length + " 条（"
      + [0, 1, 2, 3, 4].filter((n) => bl[String(n)])
        .map((n) => `L${n}:${bl[String(n)]}`).join(" · ") + "）";
    bar.appendChild(sum);
    const allOpen = (d.scenes || []).every((sc) => ui.sceneOpen[sc.id]);
    const exp = document.createElement("button");
    exp.className = "av3-btn";
    exp.id = "av3-scene-expand";
    exp.textContent = allOpen ? "▾ 全部收起" : "▸ 全部展开";
    exp.title = allOpen ? "把所有场景卡的字段列表收起来" : "把所有场景卡的字段列表展开";
    exp.onclick = () => {
      const on = !allOpen;
      ui.sceneOpen = {};
      if (on) for (const sc of (d.scenes || [])) ui.sceneOpen[sc.id] = true;
      renderScenes();
    };
    bar.appendChild(exp);
    left.appendChild(bar);

    // ⚠️ 一级分组用 **档位**（level_groups），不是 sfw/nsfw —— 档位是派生数据，
    //    且与节点上的档位按钮同名，点着「过激」就能直接找到对应搭配。
    //    空组（该档暂时没有预设）也照样渲染，让人一眼看出"这一档还没内容"。
    const lvNow = levelNum(ui.level);
    for (const g of (d.level_groups || [])) {
      const list = (d.scenes || []).filter((s) => s.level === g.num);
      const h = document.createElement("div");
      h.className = "av3-note";
      h.style.cssText = "margin:10px 0 6px";
      h.dataset.level = String(g.num);
      h.textContent = `${g.title}（${list.length}）${g.desc ? "　" + g.desc : ""}`
        + (g.num === lvNow ? "　← 当前档位" : "");
      left.appendChild(h);
      if (!list.length) {
        const e = document.createElement("div");
        e.className = "av3-meta";
        e.style.margin = "0 0 6px 4px";
        e.textContent = "（这一档暂无预设）";
        left.appendChild(e);
        continue;
      }
      for (const sc of list) left.appendChild(sceneCard(sc, sc.id === applied));
    }
  }

  /** 场景卡（v3.11.0 改版）。
   *
   *  两个改动都是"方便预览与选择"：
   *    ① **默认折叠** —— 87 条全展开要滚几千像素；现在只出一行
   *       （箭头 + 中文名 + 【题材】+ L档 + 字段数），点标题行才展开字段。
   *    ② **动作行常驻** —— 「一键套用 / 👁 预览 / 套用并预演」放在 `.bd`
   *       **外面**，折叠状态也能直接操作，不用先展开。
   */
  function sceneCard(sc, on) {
    const open = !!ui.sceneOpen[sc.id];
    const box = document.createElement("div");
    box.className = "av3-sec" + (on ? " on" : "") + (open ? " open" : "");
    box.dataset.scene = sc.id;

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.title = "点一下展开 / 收起这条搭配固定了哪些字段";
    const arw = document.createElement("span");
    arw.className = "arw";
    arw.textContent = open ? "▼" : "▶";
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = sc.zh;
    const th = document.createElement("span");
    th.className = "th";
    th.textContent = sc.theme ? `【${sc.theme}】` : "";
    const lv = document.createElement("span");
    lv.className = "av3-meta";
    lv.textContent = sc.level > 0 ? `L${sc.level}` : "全档";
    lv.title = "该预设可用到的最高档位（超过就抽不到它）";
    const nf = document.createElement("span");
    nf.className = "av3-meta";
    nf.textContent = `${(sc.fields || []).length} 字段`;
    hd.appendChild(arw); hd.appendChild(t); hd.appendChild(th);
    hd.appendChild(lv); hd.appendChild(nf);
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    if (sc.desc) {
      const de = document.createElement("div");
      de.className = "av3-meta";
      de.textContent = sc.desc;
      de.style.marginBottom = "5px";
      bd.appendChild(de);
    }
    const rows = document.createElement("div");
    rows.style.cssText = "display:flex;flex-wrap:wrap;gap:4px";
    for (const f of (sc.fields || [])) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = `${f.lab}：${f.en}`;
      chip.title = `${f.field} = ${f.en}（L${f.level}）`;
      rows.appendChild(chip);
    }
    if (sc.extra) {
      const chip = document.createElement("span");
      chip.className = "av3-chip";
      chip.textContent = `补充 ${sc.extra_count} 条`;
      chip.title = sc.extra;
      rows.appendChild(chip);
    }
    bd.appendChild(rows);
    box.appendChild(bd);

    const setOpen = (o) => {
      ui.sceneOpen[sc.id] = o;
      box.classList.toggle("open", o);
      arw.textContent = o ? "▼" : "▶";
    };
    hd.onclick = () => setOpen(!box.classList.contains("open"));

    /* ── 动作行（常驻，折叠时也能点）── */
    const act = document.createElement("div");
    act.className = "av3-sceneact";
    const b = document.createElement("button");
    b.className = "av3-btn" + (on ? "" : " pri");
    b.textContent = on ? "✅ 已套用" : "✅ 一键套用";
    b.disabled = on;
    b.onclick = () => applyScene(sc);
    act.appendChild(b);

    /* 👁 预览：**只看不套用** —— 不改写权重表，临时构造权重跑一次抽取。
       这是与「套用并预演」的关键区别：后者会真的把这条搭配写进权重。 */
    const out = document.createElement("div");
    out.className = "av3-out av3-sceneout";
    out.style.display = "none";
    const pv = document.createElement("button");
    pv.className = "av3-btn" + (on ? "" : " pri");
    pv.textContent = "👁 预览";
    pv.dataset.on = "0";
    pv.title = "只看不套用：不改动你的权重表，临时按这条搭配抽一次并显示结果";
    pv.onclick = async () => {
      if (pv.dataset.on === "1") {          // 再点一次 = 收起
        out.style.display = "none";
        pv.dataset.on = "0";
        pv.textContent = "👁 预览";
        return;
      }
      pv.disabled = true;
      pv.textContent = "抽取中…";
      try {
        const d = await panelPreview(sc);
        if (!d || d.error) {
          out.textContent = "预览失败：" + ((d && d.error) || "无响应");
        } else {
          out.innerHTML = "";
          const body = document.createElement("div");
          body.textContent = d.text || "（空）";
          const m2 = document.createElement("div");
          m2.className = "av3-meta";
          m2.style.marginTop = "6px";
          const rep = d.report || {};
          m2.textContent = `档位 ${d.level_label}｜落档 ${rep.real_level}`
            + `｜标签 ${rep.n}｜自检 ${rep.ok_f}/${rep.total_f}`;
          out.appendChild(body);
          out.appendChild(m2);
        }
        out.style.display = "block";
        pv.dataset.on = "1";
        pv.textContent = "👁 收起预览";
      } catch (e) {
        out.textContent = "预览失败：" + e.message;
        out.style.display = "block";
        pv.textContent = "👁 预览";
      } finally {
        pv.disabled = false;
      }
    };
    act.appendChild(pv);

    const pv2 = document.createElement("button");
    pv2.className = "av3-btn";
    pv2.textContent = "🎲 套用并预演";
    pv2.title = "套用这条搭配（会写进权重表）后立刻跑一次抽取";
    pv2.onclick = () => {
      applyScene(sc);
      if (panelRoll) panelRoll("all");
    };
    act.appendChild(pv2);
    box.appendChild(act);
    box.appendChild(out);
    return box;
  }

  /**
   * 这个段在当前档位下"有没有事可做"（用于给段标题挂「本档不参与」）。
   *
   * ⚠️ 判据是「段内**还有没有** ≤ 当前档的选项」，**不是**段最高档 ≤ 当前档。
   *    用 max_level 判会误伤分档段：v3.8.0 给「视角」加了第一人称词条
   *    （`pov crotch` 是 2 档、`pov, fellatio` 是 3 档），⑧镜头段的最高档
   *    被抬到 3 —— 于是全年龄档会给 ⑧镜头 打上「本档不参与」，
   *    而实际上该段 16 条全年龄词条照样在抽，纯属误导。
   *    （同样的误标也一直存在于「④ 身材与肤质」段，本次一并修掉。）
   *
   * ⚠️ 纯文本段（一个带选项的字段都没有，如 ⑫自然语言补位）恒参与 ——
   *    它没有档位概念，内容由自检兜底。
   */
  function sectionEligible(sec) {
    const lvNum = levelNum(ui.level);
    let optFields = 0;
    for (const f of (sec.fields || [])) {
      const opts = f.opts || [];
      if (!opts.length) continue;          // 文本框 / 预设位按"无选项"跳过
      optFields += 1;
      if (opts.some((o) => Number(o.level || 0) <= lvNum)) return true;
    }
    return optFields === 0;
  }

  function renderTree() {
    left.innerHTML = "";
    const tip = document.createElement("div");
    tip.className = "av3-meta";
    tip.innerHTML =
      "<b>权重</b>　段 / 字段 / 选项三层，数值 0–3：" +
      "<b>0</b> = 关闭（永不出现）　<b>1.0</b> = 默认　<b>3.0</b> = 字段必定出现　" +
      "中间值是相对倾向。<br>" +
      "想让某条选项「一定有」，把它的字段权重拉到 3.0，再把同字段其它选项设为 0。<br>" +
      "⚠️ 低于当前档位的内容不会出现 —— 权重改不了这一点。<br>" +
      "🎭 <b>角色的选择在「角色库」页签</b>（点卡片即采用），这里不提供角色权重。";
    tip.style.marginBottom = "8px";
    left.appendChild(tip);

    for (const sec of pools.sections) {
      const eligible = sectionEligible(sec);
      const el = document.createElement("div");
      el.className = "av3-sec";
      el.dataset.sec = sec.id;

      const hd = document.createElement("div");
      hd.className = "hd";
      const arrow = document.createElement("span");
      arrow.textContent = "▶";
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = sec.title + (eligible ? "" : "　（本档不参与）");
      const swIn = document.createElement("input");
      swIn.className = "av3-w";
      swIn.type = "number";
      swIn.min = "0";
      swIn.max = "3";
      swIn.step = "0.25";
      swIn.title = "段权重";
      swIn.value = fmtW(ui.weights.sections[sec.id]);
      swIn.onclick = (e) => e.stopPropagation();
      swIn.oninput = () => {
        setWeight(ui.weights.sections, sec.id, swIn.value);
        refreshSectionState();
        persist();
      };
      hd.appendChild(arrow);
      hd.appendChild(t);
      hd.appendChild(swIn);
      hd.onclick = () => {
        if (ui.openSecs.has(sec.id)) ui.openSecs.delete(sec.id);
        else ui.openSecs.add(sec.id);
        renderTree();
      };
      el.appendChild(hd);

      const bd = document.createElement("div");
      bd.className = "bd";
      for (const f of sec.fields) {
        // ⚠️⚠️ 角色字段已从权重页移除（v3.2.0）。
        //    角色的选择完全交给「角色库」页签 —— 点卡片 = 抽取概率 100%。
        //    这里再留一个权重框，用户就会以为"权重能关掉角色"，
        //    而那样做会与角色库的选择打架（点了角色却没出来）。
        //    段权重与其它字段都不受影响。
        if (f.key === "role") continue;

        const row = document.createElement("div");
        row.className = "av3-row";
        // ⚠️ 行必须带 key：上面跳过了 role，refreshSectionState 就不能再靠
        //    「第 i 行 = 第 i 个字段」的索引对齐 —— 会整体错位一格。
        row.dataset.key = f.key;

        const lab = document.createElement("span");
        lab.className = "lab";
        lab.textContent = f.lab;
        row.appendChild(lab);

        const lvInfo = document.createElement("span");
        lvInfo.className = "lv";
        if (f.type === "text" || !optCount(f)) {
          lvInfo.textContent = "手写";
        } else {
          const fs = fieldSpread(f.key);
          lvInfo.textContent = fs ? `L${fs}` : "";
          lvInfo.title = "该字段候选的档位分布";
        }
        row.appendChild(lvInfo);

        const wIn = document.createElement("input");
        wIn.className = "av3-w";
        wIn.type = "number";
        wIn.min = "0";
        wIn.max = "3";
        wIn.step = "0.25";
        wIn.title = "字段权重";
        wIn.value = fmtW(ui.weights.fields[f.key]);
        wIn.oninput = () => {
          setWeight(ui.weights.fields, f.key, wIn.value);
          refreshSectionState();
          persist();
        };
        row.appendChild(wIn);

        const badgeBtn = document.createElement("button");
        badgeBtn.className = "av3-t";
        badgeBtn.disabled = !optCount(f);
        badgeBtn.onclick = () => openDetail(node, ui, f, badgeBtn);
        row.appendChild(badgeBtn);

        bd.appendChild(row);
      }
      el.appendChild(bd);
      left.appendChild(el);
      secEls.set(sec.id, el);
    }
    refreshSectionState();
    applyOpenState();
  }

  function fieldSpread(fieldKey) {
    const f = findField(fieldKey);
    if (!f) return "";
    const lv = new Set((f.opts || []).map((o) => o.level));
    const arr = [...lv].sort((a, b) => a - b);
    return arr.join("/");
  }

  /**
   * 字段的候选数。
   * ⚠️ 只能从**词库**（/pools）的 opts 算 —— /contract 里的字段叫 option_count，
   *    两个接口的字段形状不一样，混用会让按钮永远是灰的（冒烟抓到过一次）。
   */
  function optCount(f) {
    return (f && f.opts ? f.opts.length : 0);
  }

  /** 被段权重关掉的段 id 串 */
  function disabledTxt() {
    return pools.sections
      .filter((s) => Number(ui.weights.sections[s.id] ?? 1) === 0)
      .map((s) => s.id)
      .join(",");
  }

  /**
   * 把面板状态落回节点控件。
   *
   * ⚠️ 这里必须**边改边存**，不能等「采用这条」：
   *    用户改完权重直接去排队，如果改动还在面板内存里，跑出来的还是旧设置 ——
   *    "我明明调了，怎么没生效"。这个坑冒烟抓到过一次。
   *    唯一例外是 seed：它代表"复现哪一条预览"，没预览过就不该乱写，
   *    所以只有「采用这条」会写它。
   */
  function persist() {
    saveWeights(node, ui.weights);
    wset(node, "disabled_sections", disabledTxt());
    app.graph.setDirtyCanvas(true, true);
  }

  function findField(key) {
    for (const s of pools.sections) {
      for (const f of s.fields) if (f.key === key) return f;
    }
    return null;
  }

  function setWeight(bag, key, raw) {
    const f = Number(raw);
    if (!Number.isFinite(f)) {
      delete bag[key];
      return;
    }
    const clamped = Math.max(0, Math.min(3, f));
    if (Math.abs(clamped - 1) < 1e-9) delete bag[key];
    else bag[key] = Math.round(clamped * 1000) / 1000;
  }

  function fmtW(v) {
    const f = Number(v);
    return Number.isFinite(f) ? String(Math.round(f * 1000) / 1000) : "1";
  }

  function applyOpenState() {
    for (const [sid, el] of secEls) {
      const on = ui.openSecs.has(sid);
      el.classList.toggle("open", on);
      el.querySelector(".hd").firstChild.textContent = on ? "▼" : "▶";
    }
  }

  /** 段开关状态、🎯 徽标、权重计数 —— 任何权重改动后都要刷新 */
  function refreshSectionState() {
    for (const [sid, el] of secEls) {
      const sw = Number(ui.weights.sections[sid] ?? 1);
      el.classList.toggle("off", sw === 0);
    }
    for (const sec of pools.sections) {
      const el = secEls.get(sec.id);
      if (!el) continue;
      for (const f of sec.fields) {
        if (f.key === "role") continue;       // 权重页里没有这一行
        // ⚠️ 按 data-key 查，**不能**用 rows[i] 索引 —— 跳过了 role 之后，
        //    索引与 sec.fields 的下标会错位一格，🎯 徽标全挂到别的字段上。
        const btn = el.querySelector(
          `.av3-row[data-key="${f.key}"] .av3-t`);
        if (!btn) continue;
        const b = weightBadge(ui.weights, f.key, optCount(f));
        btn.textContent = b.text;
        btn.className = "av3-t" + (b.state === 1 ? " s1" : b.state === 2 ? " s2" : "");
      }
    }
    refreshCounter();
  }

  /* ── 选项级权重细则 ── */
  function openDetail(node, ui, field, badgeBtn) {
    const card = document.createElement("div");
    card.className = "av3-mask";
    const inner = document.createElement("div");
    inner.className = "av3-mini";
    inner.style.position = "static";
    card.appendChild(inner);
    const box = document.createElement("div");
    box.className = "card";
    inner.appendChild(box);

    const hd = document.createElement("div");
    hd.className = "hd";
    hd.textContent = `权重细则 · ${field.lab}`;
    box.appendChild(hd);

    const bd = document.createElement("div");
    bd.className = "bd";
    box.appendChild(bd);

    const cur = { ...(ui.weights.options[field.key] || {}) };
    const inputs = [];

    for (const o of field.opts) {
      const row = document.createElement("div");
      row.className = "av3-oRow";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = o.zh;
      n.title = `档位 L${o.level}`;
      if (o.level > 0) {
        const tag = document.createElement("span");
        tag.style.cssText = "color:#e0a458;font-size:11px;margin-left:5px";
        tag.textContent = `L${o.level}`;
        n.appendChild(tag);
      }
      const e = document.createElement("span");
      e.className = "e";
      e.textContent = o.en || "（空）";
      const inp = document.createElement("input");
      inp.className = "av3-w";
      inp.type = "number";
      inp.min = "0";
      inp.max = "3";
      inp.step = "0.25";
      inp.value = cur[o.en] !== undefined ? String(cur[o.en]) : "1";
      const setTo = (v) => {
        inp.value = String(v);
        setWeight(cur, o.en, v);
      };
      inp.oninput = () => setWeight(cur, o.en, inp.value);
      const quick = document.createElement("span");
      quick.style.cssText = "display:flex;gap:3px";
      for (const v of [0, 1, 2, 3]) {
        const qb = document.createElement("button");
        qb.className = "av3-t";
        qb.textContent = String(v);
        qb.title = v === 0 ? "禁用该项" : `权重 ${v}`;
        qb.onclick = () => setTo(v);
        quick.appendChild(qb);
      }
      row.appendChild(n);
      row.appendChild(e);
      row.appendChild(inp);
      row.appendChild(quick);
      bd.appendChild(row);
      inputs.push({ en: o.en, inp });
    }

    const ft = document.createElement("div");
    ft.className = "ft";
    const mk = (text, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn";
      b.textContent = text;
      b.onclick = fn;
      return b;
    };
    ft.appendChild(mk("全部重置为 1.0", () => {
      for (const k of Object.keys(cur)) delete cur[k];
      for (const it of inputs) it.inp.value = "1";
    }));
    ft.appendChild(mk("全部禁用（0）", () => {
      for (const it of inputs) {
        cur[it.en] = 0;
        it.inp.value = "0";
      }
    }));
    ft.appendChild(mk("加倍（2.0）", () => {
      for (const it of inputs) {
        cur[it.en] = 2;
        it.inp.value = "2";
      }
    }));
    const apply = mk("应用", () => {
      const clean = {};
      for (const [k, v] of Object.entries(cur)) {
        const f = Number(v);
        if (Number.isFinite(f) && Math.abs(f - 1) > 1e-9) clean[k] = f;
      }
      if (Object.keys(clean).length) ui.weights.options[field.key] = clean;
      else delete ui.weights.options[field.key];
      card.remove();
      refreshSectionState();
      persist();
      if (badgeBtn) badgeBtn.blur();
    });
    apply.classList.add("pri");
    ft.appendChild(apply);
    box.appendChild(ft);

    card.onclick = (e) => {
      if (e.target === card) card.remove();
    };
    document.body.appendChild(card);
  }

  /* ── 右列：设置 + 结果 ── */
  const counter = document.createElement("div");
  counter.className = "av3-meta";

  function refreshCounter() {
    const s = Object.keys(ui.weights.sections).length;
    const f = Object.keys(ui.weights.fields).length;
    const o = Object.values(ui.weights.options).reduce((n, x) => n + Object.keys(x).length, 0);
    counter.innerHTML = `已调权重：段 <b>${s}</b> · 字段 <b>${f}</b> · 选项 <b>${o}</b>`;
  }

  function renderRight() {
    right.innerHTML = "";

    /* 结果 */
    const outBox = document.createElement("div");
    outBox.className = "av3-box";
    outBox.innerHTML = "<h4>生成结果</h4>";
    const outEl = document.createElement("div");
    outEl.className = "av3-out";
    outEl.textContent = "点下方「全部随机」抽一条。（题外：面板与工作流共用同一套引擎，"
      + "所以「采用这条」之后跑工作流，出来的就是这一条。）";
    outBox.appendChild(outEl);
    const metaEl = document.createElement("div");
    metaEl.className = "av3-meta";
    metaEl.style.marginTop = "7px";
    outBox.appendChild(metaEl);
    const sumEl = document.createElement("div");
    sumEl.className = "av3-sum";
    sumEl.style.marginTop = "6px";
    outBox.appendChild(sumEl);
    const scEl = document.createElement("div");
    scEl.className = "av3-sc";
    scEl.style.marginTop = "7px";
    outBox.appendChild(scEl);
    right.appendChild(outBox);

    /* 动作 */
    const actBox = document.createElement("div");
    actBox.className = "av3-box";
    actBox.innerHTML = "<h4>动作</h4>";
    const actRow = document.createElement("div");
    actRow.style.cssText = "display:flex;gap:7px;flex-wrap:wrap";
    const mkBtn = (text, cls, fn) => {
      const b = document.createElement("button");
      b.className = "av3-btn" + (cls ? " " + cls : "");
      b.textContent = text;
      b.onclick = fn;
      return b;
    };
    const rollBtn = mkBtn("🎲 全部随机（预演）", "pri", () => runRoll("all"));
    const seedBtn = mkBtn("🔁 换种子", "", () => runRoll("all"));
    const clearBtn = mkBtn("🧹 清空权重", "", () => {
      ui.weights = { sections: {}, fields: {}, options: {} };
      renderTree();
      persist();
      setStatus("已清空全部权重", "");
    });
    const adoptBtn = mkBtn("✅ 采用这条（写回 seed）", "", () => {
      if (ui.lastSeed === null) {
        setStatus("先点一次「全部随机」再采用", "no");
        return;
      }
      wset(node, "nsfw_level", ui.level);
      wset(node, "seed", ui.lastSeed);
      wset(node, "tag_cap", capSel.value);
      // ⚠️ 空栏不写回：Number("") === 0 会把基准概率静默清零，
      //    而 0 意味着"所有可选字段都不抽" —— 这是 v3.1.0 的显示 0 那个 bug。
      if (probIn.value.trim() !== "") {
        const pv = Number(probIn.value);
        if (Number.isFinite(pv)) wset(node, "base_prob", pv);
      }
      wset(node, "force_role", roleIn.value);
      wset(node, "extra_body", extraIn.value);
      wset(node, "appearance_follow", apeChk.checked);
      wset(node, "disabled_sections", disabledTxt());
      saveWeights(node, ui.weights);
      app.graph.setDirtyCanvas(true, true);
      setStatus(`已写回节点：档位 ${LEVEL_SHORT[ui.level]}，seed ${ui.lastSeed}`, "ok");
    });
    actRow.appendChild(rollBtn);
    actRow.appendChild(seedBtn);
    actRow.appendChild(adoptBtn);
    actRow.appendChild(clearBtn);
    actBox.appendChild(actRow);
    statEl = document.createElement("div");
    statEl.className = "av3-meta";
    statEl.style.marginTop = "6px";
    actBox.appendChild(statEl);
    right.appendChild(actBox);

    /* 设置 */
    const cfgBox = document.createElement("div");
    cfgBox.className = "av3-box";
    cfgBox.innerHTML = "<h4>设置</h4>";

    const mkRow = (label, el) => {
      const d = document.createElement("div");
      d.className = "av3-fld";
      const l = document.createElement("label");
      l.textContent = label;
      d.appendChild(l);
      d.appendChild(el);
      cfgBox.appendChild(d);
      return el;
    };

    const capSel = document.createElement("select");
    for (const c of contract.tag_cap_choices) {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c === "自动" ? "自动（按人数）" : c;
      capSel.appendChild(o);
    }
    capSel.value = String(lsGet(node, "tag_cap", "自动"));
    capSel.onchange = () => {
      wset(node, "tag_cap", capSel.value);
      app.graph.setDirtyCanvas(true, true);
    };
    mkRow("标签数上限", capSel);

    const probIn = document.createElement("input");
    probIn.type = "number";
    probIn.min = "0";
    probIn.max = "1";
    probIn.step = "0.05";
    // ⚠️ 读回时兜底。控件值可能是空的 / 非数字 / 历史脏数据 0：
    //    v3.1.0 的「采用这条」在这一栏为空时写了 Number("") === 0，
    //    而 base_prob = 0 意味着"所有可选字段都不抽"，出图只剩必填项 ——
    //    用户看到的就是"随机坏了"。这里把它当"没设置"，回到默认值。
    const probSaved = Number(lsGet(node, "base_prob", NaN));
    probIn.value = String(Number.isFinite(probSaved) && probSaved > 0
      ? probSaved : contract.weights.base_prob_default);
    probIn.oninput = () => {
      // 空串不写回：Number("") === 0 会静默把基准概率清零
      if (probIn.value.trim() === "") return;
      const v = Number(probIn.value);
      if (Number.isFinite(v)) wset(node, "base_prob", Math.max(0, Math.min(1, v)));
    };
    probIn.onblur = () => {
      // 失焦时把空栏补回默认值，避免留下一个"看着像 0"的空框
      if (probIn.value.trim() === "") {
        probIn.value = String(contract.weights.base_prob_default);
        wset(node, "base_prob", contract.weights.base_prob_default);
      }
    };
    probIn.onchange = () => app.graph.setDirtyCanvas(true, true);
    mkRow("基准抽取概率", probIn);

    const roleIn = document.createElement("input");
    roleIn.type = "text";
    roleIn.placeholder = "留空 = 随机角色；填 danbooru 角色 tag";
    roleIn.value = String(lsGet(node, "force_role", ""));
    roleIn.oninput = () => wset(node, "force_role", roleIn.value);
    mkRow("指定角色", roleIn);

    const apeChk = document.createElement("input");
    apeChk.type = "checkbox";
    apeChk.style.flex = "0 0 auto";
    apeChk.checked = lsGet(node, "appearance_follow", true) !== false;
    apeChk.title = "开启时，抽到角色会用官方发色/瞳色覆盖随机结果";
    apeChk.onchange = () => {
      wset(node, "appearance_follow", apeChk.checked);
      app.graph.setDirtyCanvas(true, true);
    };
    const apeWrap = document.createElement("span");
    apeWrap.style.cssText = "flex:1.6;display:flex;align-items:center;gap:6px;font-size:12px";
    apeWrap.appendChild(apeChk);
    const apeTxt = document.createElement("span");
    apeTxt.textContent = "按角色官方外貌对齐（会覆盖发色/瞳色的权重）";
    apeWrap.appendChild(apeTxt);
    mkRow("外貌对齐", apeWrap);

    const extraIn = document.createElement("input");
    extraIn.type = "text";
    extraIn.placeholder = "手写补句，拼在末尾";
    extraIn.value = String(lsGet(node, "extra_body", ""));
    extraIn.oninput = () => wset(node, "extra_body", extraIn.value);
    mkRow("手写补句", extraIn);

    cfgBox.appendChild(counter);
    const secTip = document.createElement("div");
    secTip.className = "av3-note";
    secTip.textContent = "关闭的段会写进节点的 disabled_sections（段开关只有面板这一个入口，节点上不重复外露）。";
    cfgBox.appendChild(secTip);
    right.appendChild(cfgBox);

    /* ── 预设：把当前这一整套配置存下来，随时一键恢复 ─────────────
       存的是面板上的这一套状态：档位 + 三层权重 + 段开关 + 已选角色
       + 标签上限 + 基准概率 + 外貌对齐 + 手写补句 + 手填角色。

       ⚠️ **不含 seed**。seed 代表"复现某一条预览"，是一次性的；
          把它存进预设会让"加载预设"变成"每次都出同一张"，
          那不是这个功能该有的语义。
    */
    const preBox = document.createElement("div");
    preBox.className = "av3-box";
    preBox.innerHTML = "<h4>预设</h4>";

    const preRow1 = document.createElement("div");
    preRow1.className = "av3-preset";
    const preSel = document.createElement("select");
    const preApply = mkBtn("📥 应用");
    const preDel = mkBtn("🗑 删除");
    preRow1.appendChild(preSel);
    preRow1.appendChild(preApply);
    preRow1.appendChild(preDel);
    preBox.appendChild(preRow1);

    const preRow2 = document.createElement("div");
    preRow2.className = "av3-preset";
    const preName = document.createElement("input");
    preName.type = "text";
    preName.placeholder = "新预设名称（同名会覆盖）";
    preName.maxLength = 40;
    const preSave = mkBtn("💾 保存当前配置", "pri");
    preRow2.appendChild(preName);
    preRow2.appendChild(preSave);
    preBox.appendChild(preRow2);

    const preTip = document.createElement("div");
    preTip.className = "av3-meta";
    preTip.textContent = "预设存在插件目录的 presets/user_presets.json —— 可备份、可随插件分享给别人。";
    preBox.appendChild(preTip);
    right.appendChild(preBox);

    // 重渲之后要能恢复"当前选中的预设"，所以记在 ui 上而不是 DOM 里
    if (ui.presetCur === undefined) ui.presetCur = "";

    /**
     * 预设摘要 —— **前端自己拼**，不用后端返回的 summary。
     *
     * 后端存/回的是档位 key（`hardcore`），而界面上要显示「3 过激」；
     * 档位标签是前端的概念（LEVEL_SHORT），后端不该知道。
     * 后端的 summary 留着给非前端消费者用，这里不走它。
     */
    function presetSummary(p) {
      const d = p.data || {};
      const bits = [LEVEL_SHORT[d.level] || d.level || "—"];
      const n = String(d.selected_roles || "").split("|")
        .filter((t) => t.trim()).length;
      if (n) bits.push(`${n} 位角色`);
      const w = d.weights || {};
      const wn = Object.keys(w.sections || {}).length
        + Object.keys(w.fields || {}).length
        + Object.values(w.options || {})
            .reduce((a, x) => a + Object.keys(x || {}).length, 0);
      if (wn) bits.push(`权重 ${wn} 项`);
      if (String(d.force_role || "").trim()) bits.push("手填角色");
      return bits.join(" · ");
    }

    function paintPresets() {
      preSel.innerHTML = "";
      if (!ui.presets.length) {
        const o = document.createElement("option");
        o.value = "";
        o.textContent = "（还没有预设）";
        preSel.appendChild(o);
        preSel.disabled = true;
        preApply.disabled = true;
        preDel.disabled = true;
        return;
      }
      preSel.disabled = false;
      preApply.disabled = false;
      preDel.disabled = false;
      for (const p of ui.presets) {
        const o = document.createElement("option");
        o.value = p.name;
        o.textContent = `${p.name} —— ${presetSummary(p)}`;
        o.title = `保存于 ${p.saved_at}`;
        preSel.appendChild(o);
      }
      if (ui.presetCur && ui.presets.some((p) => p.name === ui.presetCur)) {
        preSel.value = ui.presetCur;
      } else {
        ui.presetCur = preSel.value;
      }
    }

    function loadPresets() {
      const seq = ++presetsSeq;
      return api.fetchApi(`/${NS}/presets`, { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => {
          // 期间发生过写操作 → 这份快照已经过期，丢掉。
          // 见 presetsSeq 的注释：不然会把删掉的预设又变回来。
          if (seq !== presetsSeq) return;
          ui.presets = d.presets || [];
          paintPresets();
        })
        .catch((e) => setStatus("预设列表加载失败：" + e.message, "no"));
    }

    /** 收集面板当前这一整套配置。 */
    function collectConfig() {
      const bpRaw = probIn.value.trim();
      const bp = bpRaw === "" ? NaN : Number(bpRaw);
      return {
        level: ui.level,
        weights: JSON.parse(JSON.stringify(ui.weights)),
        selected_roles: String(lsGet(node, "selected_roles", "") || ""),
        // v3.10.0：随机池（限定作品 IP）也跟着预设走
        role_works: String(lsGet(node, "role_works", "") || ""),
        tag_cap: String(capSel.value || "自动"),
        base_prob: Number.isFinite(bp) && bp > 0
          ? bp : contract.weights.base_prob_default,
        appearance_follow: !!apeChk.checked,
        extra_body: String(extraIn.value || ""),
        force_role: String(roleIn.value || ""),
        // v3.17.0：胸部大小等级取代巨乳开关（预设里也跟着存新字段）
        breast_size: String(bsSel.value || "不指定"),
        // 兼容保留：老版本预设读取方还能认得 big_breasts（新值为 false 语义）
        big_breasts: false,
      };
    }

    /** 把一份预设写回面板与节点。 */
    function applyConfig(data) {
      if (!data || typeof data !== "object") return;
      if (data.level && contract.levels.some((l) => l.key === data.level)) {
        ui.level = String(data.level);
      }
      const w = data.weights;
      if (w && typeof w === "object") {
        // 整表替换而不是合并 —— 预设的语义就是"这一整套配置"
        ui.weights = {
          sections: { ...(w.sections || {}) },
          fields: { ...(w.fields || {}) },
          options: { ...(w.options || {}) },
        };
      }
      if (data.selected_roles !== undefined) {
        wset(node, "selected_roles", String(data.selected_roles || ""));
      }
      if (data.role_works !== undefined) {
        wset(node, "role_works", String(data.role_works || ""));
      }
      if (data.tag_cap !== undefined) {
        wset(node, "tag_cap", String(data.tag_cap || "自动"));
      }
      if (data.base_prob !== undefined) {
        const bp = Number(data.base_prob);
        wset(node, "base_prob",
             Number.isFinite(bp) && bp > 0 ? bp : contract.weights.base_prob_default);
      }
      if (data.appearance_follow !== undefined) {
        wset(node, "appearance_follow", !!data.appearance_follow);
      }
      if (data.extra_body !== undefined) {
        wset(node, "extra_body", String(data.extra_body || ""));
      }
      if (data.force_role !== undefined) {
        wset(node, "force_role", String(data.force_role || ""));
      }
      // v3.17.0：优先读新字段 breast_size；老预设只有 big_breasts →
      // 开着就映射为「巨乳」（与后端兼容逻辑一致）。两者都没有 → 不动现值。
      if (data.breast_size !== undefined) {
        const bs = String(data.breast_size || "不指定");
        wset(node, "breast_size", bs);
        bsSel.value = bs;
      } else if (data.big_breasts !== undefined) {
        const bs = data.big_breasts ? "巨乳" : "不指定";
        wset(node, "breast_size", bs);
        bsSel.value = bs;
        wset(node, "big_breasts", false); // 旧开关退役：语义已并入 breast_size
      }
      wset(node, "nsfw_level", ui.level);
      saveWeights(node, ui.weights);
      wset(node, "disabled_sections", disabledTxt());
      app.graph.setDirtyCanvas(true, true);
      // 设置区的控件值都是从节点读的，索性整块重画一次
      // （renderRight 末尾会 syncLevel → syncTabs，左列也跟着刷新）
      renderRight();
    }

    preSel.onchange = () => {
      ui.presetCur = preSel.value;
    };

    preSave.onclick = async () => {
      const name = preName.value.trim();
      if (!name) {
        setStatus("先给预设起个名字", "no");
        if (preName.focus) preName.focus();
        return;
      }
      try {
        const r = await api.fetchApi(`/${NS}/presets/save`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, data: collectConfig() }),
        });
        const d = await r.json();
        if (d.error) {
          setStatus("保存失败：" + d.error, "no");
          return;
        }
        ui.presets = d.presets || [];
        presetsSeq += 1;      // 作废所有在途的 GET（它会覆盖成旧快照）
        ui.presetCur = name;
        paintPresets();
        preName.value = "";
        setStatus(`已保存预设「${name}」`, "ok");
      } catch (e) {
        setStatus("保存失败：" + e.message, "no");
      }
    };

    preApply.onclick = () => {
      const p = ui.presets.find((x) => x.name === preSel.value);
      if (!p) {
        setStatus("先选一个预设", "no");
        return;
      }
      ui.presetCur = p.name;
      applyConfig(p.data);
      setStatus(`已应用预设「${p.name}」`, "ok");
    };

    // 删除用"再点一次确认"而不是 window.confirm ——
    // confirm 在 jsdom 里没实现（会抛异常），测试没法跑；
    // 而且原生的 confirm 在 ComfyUI 里样式也很突兀。
    let delArmed = false;
    let delTimer = null;
    preDel.onclick = async () => {
      const name = preSel.value;
      if (!name) return;
      if (!delArmed) {
        delArmed = true;
        preDel.textContent = "⚠ 再点一次";
        delTimer = setTimeout(() => {
          delArmed = false;
          preDel.textContent = "🗑 删除";
        }, 4000);
        return;
      }
      if (delTimer) clearTimeout(delTimer);
      delArmed = false;
      preDel.textContent = "🗑 删除";
      try {
        const r = await api.fetchApi(`/${NS}/presets/delete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        const d = await r.json();
        ui.presets = d.presets || [];
        presetsSeq += 1;      // 作废所有在途的 GET（它会覆盖成旧快照）
        if (ui.presetCur === name) ui.presetCur = "";
        paintPresets();
        setStatus(`已删除预设「${name}」`, "ok");
      } catch (e) {
        setStatus("删除失败：" + e.message, "no");
      }
    };

    paintPresets();
    loadPresets();

    /* 抽一次（v3.11.0 抽出）：runRoll 与「👁 预览」共用同一条请求体，
       唯一区别是 weights_json 传谁 —— 预览传的是**临时权重**，不落盘。 */
    async function rollOnce(weightsJson) {
      const r = await api.fetchApi(`/${NS}/roll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section: "all",
          seed: Math.floor(Math.random() * 2147483000),
          nsfw_level: ui.level,
          tag_cap: capSel.value,
          base_prob: Number(probIn.value),
          weights_json: weightsJson,
          disabled_sections: disabledTxt(),
          force_role: roleIn.value,
          // ⚠️ v3.8.0 修：这两个之前**没发**，于是"预演 ≠ 实跑"——
          //    选了角色点「全部随机」时预览是全库随机抽的，
          //    而真正跑工作流是按 selected_roles 走，两边对不上。
          selected_roles: String(lsGet(node, "selected_roles", "") || ""),
          lock_fields: String(lsGet(node, "lock_fields", "") || ""),
          // v3.10.0：随机池（限定作品 IP）—— 也必须发，否则预演里
          // "每次自动换"的效果看不到（预演会退回全库随机）
          role_works: String(lsGet(node, "role_works", "") || ""),
          extra_body: extraIn.value,
          appearance_follow: apeChk.checked,
          // v3.17.0：胸部大小等级（预演必须与实跑同参，否则又回到
          // "预演 ≠ 实跑" 的老坑）；后端对 "不指定" 不干预。
          breast_size: bsSel.value,
        }),
      });
      return r.json();
    }

    /* 👁 非破坏性预览（v3.11.0）：**只看不套用**。
       构造「当前权重 + n_canon 强制本场景」的临时表发给引擎，
       全程不碰 ui.weights / 不 persist —— 预览多少次都不会改变你的设置。 */
    async function previewScene(sc) {
      const all = nCanonOptions();
      const w = JSON.parse(JSON.stringify(ui.weights || {}));
      w.sections = w.sections || {};
      w.fields = w.fields || {};
      w.options = w.options || {};
      w.fields.n_canon = 3.0;
      const bag = {};
      for (const en of all) bag[en] = 0;
      bag[sc.id] = 3.0;
      w.options.n_canon = bag;
      // 段权重不能是 0（否则引擎把 canon 段当关闭，预设根本不生效）
      if (Number(w.sections.canon ?? 1) === 0) w.sections.canon = 1;
      return rollOnce(JSON.stringify(w));
    }

    async function runRoll(section) {
      setStatus("正在抽取…", "");
      const seed = Math.floor(Math.random() * 2147483000);
      try {
        // v3.11.0：请求体与「👁 预览」共用 rollOnce，这里传当前权重表
        const d = await rollOnce(JSON.stringify(ui.weights));
        if (d.error) {
          setStatus("抽取失败：" + d.error, "no");
          return;
        }
        ui.lastResult = d;
        ui.lastSeed = seed;
        outEl.textContent = d.text || "（空）";
        sumEl.textContent = d.cn_summary ? "中文摘要：" + d.cn_summary : "";
        const rep = d.report || {};
        const okAll = rep.ok_f === rep.total_f;
        scEl.innerHTML =
          `档位 <b>${d.level_label}</b>｜落档 <b>${rep.real_level}</b>｜标签 <b>${rep.n}</b>`
          + `｜自检 <span class="${okAll ? "av3-ok" : "av3-no"}">${rep.ok_f}/${rep.total_f}</span>`
          + `｜风格 <span class="${rep.ok_s === rep.total_s ? "av3-ok" : "av3-wa"}">${rep.ok_s}/${rep.total_s}</span>`;
        const extra = [];
        if ((d.dropped || []).length) extra.push("裁剪：" + d.dropped.join("、"));
        for (const n of d.notes || []) extra.push(n);
        if (extra.length) {
          const nd = document.createElement("div");
          nd.className = "av3-note";
          nd.textContent = extra.join(" ｜ ");
          scEl.appendChild(nd);
        }
        setStatus(`抽好了 · seed ${seed}（点「采用这条」写回节点即可复现）`, "ok");
      } catch (e) {
        setStatus("请求失败：" + e.message, "no");
      }
    }

    /* 档位切换要重渲左列（低档内容要标出来） */
    syncLevel();
    panelRoll = runRoll;          // 场景搭配页的「套用并预演」用
    panelPreview = previewScene;  // 场景搭配页的「👁 预览」（只看不套用）用
  }

  function levelNum(key) {
    const found = contract.levels.find((l) => l.key === key);
    return found ? found.num : 99;
  }

  renderRight();
  refreshCounter();
  syncTabs();
  document.body.appendChild(mask);
}

/* ── 节点按钮 ─────────────────────────────────────────────────────── */
function addNodeButtons(node) {
  if (node.__av3Buttons) return;
  node.__av3Buttons = true;

  const mk = (text, tip, fn) => {
    const b = node.addWidget("button", text, null, fn);
    b.serialize = false; // 按钮不进工作流 JSON
    b.tooltip = tip;
    return b;
  };

  mk("⚙ 打开随机面板", "可视化选择 + 三层权重调节", () => openPanel(node));
  mk("🔁 换种子", "seed 设为 -1 之外的新随机值，下次排队重新抽", () => {
    wset(node, "seed", Math.floor(Math.random() * 2147483000));
    app.graph.setDirtyCanvas(true, true);
  });
  mk("🎲 每次重抽", "seed = -1：每次排队都重新随机", () => {
    wset(node, "seed", -1);
    app.graph.setDirtyCanvas(true, true);
  });
  mk("🧹 清空权重", "把三层权重表清空回默认", () => {
    wset(node, "weights_json", "");
    app.graph.setDirtyCanvas(true, true);
  });
}

app.registerExtension({
  name: "Anima.Wardrobe.V3.Studio",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_TYPE) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated ? onCreated.apply(this, arguments) : undefined;
      addNodeButtons(this);
      return r;
    };
  },
});
