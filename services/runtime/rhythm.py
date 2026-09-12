"""叙事节奏目录(2026-09-07):新建项目向导 / 🧭 创作定调弹窗的「叙事节奏」选项。

两层各自独立:
- 单集节奏(episode):一集内部的节拍走向
- 跨集节奏(season):多集之间怎么衔接

选中后不是只存一个标签,而是把「名字 + 节拍链 + 要点 + 适用」整段文本写进项目 brief.md 的
「## 叙事节奏」小节,随 brief.md 注入每个 Agent 的系统提示词,让剧本/分集/分镜拿到的是可执行
的节拍而不是一个名词。目录条目的 name 是 brief.md 里的回读键,改名会让存量项目回读成「自定义」。
"""
from __future__ import annotations

CUSTOM_ID = "custom"
CUSTOM_NAME = "自定义"

# 每条:id(存储/接口用) / name(选择界面短名,也是 brief.md 回读键) / beats(节拍链) / detail(要点) / fit(适用题材)
EPISODE_RHYTHMS: list[dict] = [
    {"id": "classic", "name": "传统三幕",
     "beats": "铺垫 → 高潮 → 钩子",
     "detail": "前段交代人物与处境,中段把矛盾推到顶点,末尾留一个悬念带出下一集",
     "fit": "常规剧情片、正剧、长篇小说改编、需要观众先建立情感投入的故事"},
    {"id": "short_drama", "name": "短剧倒置",
     "beats": "冲突 → 好奇 → 补背景 → 更大冲突",
     "detail": "开场直接进入冲突,先让观众想知道「为什么」,再用最少篇幅补背景,收在更大的冲突上",
     "fit": "竖屏微短剧、网文改编、强情节爽剧、需要前 10 秒留人的平台"},
    {"id": "escalation", "name": "持续升级",
     "beats": "小危机 → 小高潮 → 更大危机 → 小高潮 → …… → 总爆发",
     "detail": "每个小高潮直接引出下一个更大的危机,不设平台期,张力单向爬升(费希特曲线)",
     "fit": "悬疑、惊悚、追逐、逃生、灾难类"},
    {"id": "kishotenketsu", "name": "起承转合",
     "beats": "起 → 承 → 转 → 合",
     "detail": "靠「转」带来的视角反差而不是对抗来推进;不需要反派,情绪落点在「合」的领悟",
     "fit": "日常、治愈、文艺、寓言、没有反派的人物小品"},
    {"id": "catharsis_loop", "name": "憋屈—爽点",
     "beats": "压抑 → 小爽 → 更憋屈 → 大爽",
     "detail": "先把观众的气憋起来,爽点隔一段给一次、越攒越大;单集内至少释放一次,集末仍留一口气",
     "fit": "逆袭、打脸、复仇、职场翻盘、爽剧"},
    {"id": "golden3s", "name": "黄金三秒",
     "beats": "3 秒钩子 → 15 秒内冲突/反转 → 中段推进 → 末 10 秒卡点",
     "detail": "按秒表切节拍:开场画面即冲突或反常,中段只推进不解释,结尾必须停在最悬的一帧上",
     "fit": "1 分钟以内的竖屏单集、信息流投放素材、切片引流"},
    {"id": "mystery", "name": "谜题揭示",
     "beats": "抛谜 → 线索 → 误导 → 揭示 → 新谜",
     "detail": "每集回答一个问题再抛出一个新问题;误导必须公平,揭示之后回看要成立",
     "fit": "推理、悬疑、烧脑反转、解谜向"},
    {"id": "slow_burn", "name": "慢热积累",
     "beats": "低强度日常 → 细节铺陈 → 单点情绪爆发 → 余韵",
     "detail": "几乎不设钩子,靠细节堆叠积蓄情绪,只在一处集中释放,结尾留白",
     "fit": "纪录片、情感、群像、家庭、年代剧"},
    {"id": "dual_line", "name": "双线交叉",
     "beats": "A 线铺垫 → B 线冲突 → A 线升级 → 两线汇合",
     "detail": "两条线交替推进、互相借力,在汇合点产生一加一大于二的冲击",
     "fit": "双主角、多时空、平行剪辑、群戏"},
    {"id": "circular", "name": "循环环形",
     "beats": "开场画面/状态 → 变化 → 回到开场画面",
     "detail": "结尾回到开头的画面或状态,但人物或意义已经不同;首尾呼应本身就是主题",
     "fit": "寓言、诗意短片、概念片、单集微电影"},
    {"id": "anticlimax", "name": "反高潮",
     "beats": "正常铺垫 → 预期的高潮被抽走 → 留白",
     "detail": "先按常规节奏建立预期,在高潮点故意落空,让观众自己补完",
     "fit": "文艺片、黑色幽默、荒诞、作者向短片"},
    {"id": "comedy", "name": "喜剧包袱",
     "beats": "建立情境与预期 → 强化预期 → 意外转折 → 笑点释放 → 回扣或新包袱",
     "detail": "先把观众带进「以为会这样」,再用意外拆掉预期;每个包袱释放后要么回扣前面,要么立刻铺下一个",
     "fit": "喜剧、情景剧、段子改编、轻松向短剧"},
]

SEASON_RHYTHMS: list[dict] = [
    {"id": "standalone", "name": "单元剧",
     "beats": "每集自成闭环,不留跨集钩子",
     "detail": "一集一个完整故事,人物与世界观贯穿,剧情不跨集",
     "fit": "单元故事集、动画短片集、科普/纪录系列"},
    {"id": "serial", "name": "连续剧",
     "beats": "每集末尾留钩子 → 主线贯穿到底",
     "detail": "每集结尾必须停在未解决处,主线连续推进,不允许闭环收尾",
     "fit": "连续剧情、网文改编、长篇追更"},
    {"id": "window_dev_close", "name": "窗口—发展—收束",
     "beats": "前段高密度留人 → 中段拉长副线 → 末段集中回收",
     "detail": "开头约一成集数是留人窗口,密度最高;中段用副线与感情线拉长;末段集中回收伏笔",
     "fit": "80–100 集微短剧、长篇连载"},
    {"id": "twin_peaks", "name": "季中双峰",
     "beats": "铺垫 → 季中大高潮 → 放缓 → 季末大高潮",
     "detail": "一季两次大高潮,中间给观众喘息并转换目标",
     "fit": "8–12 集中长剧集、季播剧"},
    {"id": "hybrid", "name": "单元+主线",
     "beats": "每集独立小案 → 主线每 3–5 集推进一次",
     "detail": "单集有独立闭环,主线间歇露头,两层节奏并行",
     "fit": "侦探、职业剧、冒险、任务制故事"},
]

KINDS = {"episode": ("单集节奏", EPISODE_RHYTHMS), "season": ("跨集节奏", SEASON_RHYTHMS)}


def catalog() -> dict:
    """接口用目录:两层各自的条目列表(不含自定义,前端自行追加)。"""
    return {"episode": EPISODE_RHYTHMS, "season": SEASON_RHYTHMS, "custom_id": CUSTOM_ID}


def _find(items: list[dict], key: str, val: str) -> dict | None:
    return next((r for r in items if r.get(key) == val), None)


def normalize(rhythm: dict | None) -> dict:
    """接口入参 → 规范结构 {episode, episode_custom, season, season_custom};未知 id 视为未指定。"""
    rhythm = rhythm or {}
    out = {}
    for kind, (_, items) in KINDS.items():
        rid = str(rhythm.get(kind) or "").strip()
        custom = str(rhythm.get(f"{kind}_custom") or "").strip()
        if rid == CUSTOM_ID:
            if not custom:
                rid = ""
        elif rid and not _find(items, "id", rid):
            rid = ""
        out[kind] = rid
        out[f"{kind}_custom"] = custom if rid == CUSTOM_ID else ""
    return out


def format_section(rhythm: dict | None) -> str:
    """规范结构 → brief.md「## 叙事节奏」小节正文(不含小节标题);两层皆未指定返回 ''。"""
    r = normalize(rhythm)
    blocks = []
    for kind, (label, items) in KINDS.items():
        rid = r[kind]
        if not rid:
            continue
        if rid == CUSTOM_ID:
            blocks.append(f"### {label}:{CUSTOM_NAME}\n{r[f'{kind}_custom']}")
            continue
        it = _find(items, "id", rid)
        blocks.append(f"### {label}:{it['name']}\n节拍链:{it['beats']}\n要点:{it['detail']}\n适用:{it['fit']}")
    return "\n\n".join(blocks)


def parse_section(text: str) -> dict:
    """brief.md「## 叙事节奏」小节正文 → 规范结构。按名字回读目录;名字不认识(含「自定义」)按自定义保留正文。"""
    out = normalize(None)
    text = (text or "").strip()
    if not text:
        return out
    # 按 "### <层>:" 标题切块;兼容全角/半角冒号
    import re
    pat = re.compile(r"^###\s*(单集节奏|跨集节奏)\s*[::]\s*(.*)$", re.M)
    marks = list(pat.finditer(text))
    for i, m in enumerate(marks):
        label, name = m.group(1), m.group(2).strip()
        body = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip()
        kind = next(k for k, (lab, _) in KINDS.items() if lab == label)
        it = None if name == CUSTOM_NAME else _find(KINDS[kind][1], "name", name)
        if it:
            out[kind] = it["id"]
            out[f"{kind}_custom"] = ""
        elif body or name:
            out[kind] = CUSTOM_ID
            # 名字不认识且不是「自定义」:把名字并回正文,避免丢信息
            out[f"{kind}_custom"] = body if name == CUSTOM_NAME else f"{name}\n{body}".strip()
    return out
