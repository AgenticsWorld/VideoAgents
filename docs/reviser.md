# 修改师(00-orchestration/reviser)

预览页「✏️ 修改 / 反馈 / 编辑」按钮的默认收件人。目标:用户指着一个对象提修改意见时,一个 Agent 当场把相关改动全做完,不再经总制片写工单、派专业工位、轮询等待、评分收尾。

## 为什么

原链路:弹窗 → 总制片(有状态串行,排在正在跑的流水线后面)→ 定位 → 写工单 → dispatch 专业工位(每个都是新进程 + 读 SOUL/WORKFLOW)→ 5 s 轮询 → evaluation → 收尾。跨两三个工位的修改 = 三到六次进程冷启动串行。

新链路:弹窗 → 修改师(无状态、可并发、占普通工人槽)→ 一个进程改完关单 → 变更记录交总制片只做标脏。

## 链路

1. **页面**(`apps/web/static/edit-popup.js`):`EditPopup.open({project, compose, agent, dispatcher, target, onSent})`。
   - 无 `agent` → 修改师;`dispatcher:true` → 总制片(工作流页改 dag.json);`agent` 指定 → 直发(草图重绘、全景锚点、白模面板、剧本各板块保持原样)。
   - `target = {kind, id, ep, files[], agents[], label}`,各页按钮用 `data-kind` / `data-id`(缺省取页面默认 kind、从定位文本抓 `epNN`)。
   - 目标为修改师时显示「顺带重跑受影响的下游任务」勾选(默认不勾),并查一次 `/api/v1/runs`,同项目在跑任务的消息含本对象 id/集号/文件名即提示冲突(不拦发送)。
2. **服务端**(`services/runtime/core.py`):
   - `sanitize_revision_target` 白名单字段限长;`_revision_header` 在用户原话前拼 `[修改单]` 头(kind/id/ep/rerun_downstream、files、代行工位、同对象最近 3 条修改记录路径)。
   - `build_role_prompt(..., target)`:修改师的「只做 SOUL 职责内的事」改为「可代行」,末尾附 `revision_agents_for(target)` 得到的一到两个工位 SOUL 正文(`REVISION_KIND_AGENTS` 映射,页面 `agents[]` 优先;单个 SOUL 截断 60k 字符)。
   - 关单钩子 `_finish_revision`:解析回执末尾 `## 变更记录`(`changed_files / dirty_nodes / signature_expired / checks / notes`,一行一键)写 `runs/revisions/<run_id>.json`;`signature_expired` 非空发本机通知;`rerun_downstream=true` 且成功 → 立即 `api_chat` 投递总制片(`source=reviser`)并标记已消化。
   - 自动运行看门狗:每条唤醒消息附 `pending_revisions_note(proj)`,消息确已发出后 `mark_revisions_consumed`。
   - `GET /api/v1/projects/{project}/revisions` 列出记录(最新在前)。
3. **规约**:`agents/00-orchestration/reviser/SOUL.md`;WORKFLOW.md §5 reviser 行、§7 返工规则第 6 条;总制片 SOUL 第 16 条(只标脏不重做);`workflow.yaml` services `on_user_revision`。

## kind → 代行工位

| kind | 页面 | 代行工位 |
|---|---|---|
| character / voice / costume | 人物 | appearance + character-concept / voiceprint + voice-generation / costume + costume-concept |
| scene | 场景 | scene + environment-concept |
| prop / creature | 道具 / 生物 | prop / creature + creature-concept |
| worldview | 世界观 | world(直接写 bible/ 并追加 changelog) |
| script | 剧本(标题行整体反馈) | screenplay + dialogue-rewrite |
| storyboard | 故事板(非草图) | storyboard |
| shot / group / group_media / shot_plate | 分镜 | shot-planning + prompt / prompt + video-generation / video-generation + image-generation / shot-plates |
| caption / narration / narration_audio / bgm | 分镜 | caption / narration / narrator / music |

未映射的 kind:修改师按 WORKFLOW §2 自行定位归属工位并 Read 其 SOUL。

`shot_plate`(分镜预览页每张背景图的「✏️ 修改」,2026-09-26):修改师跑 `code/revise_shot_plate.py --shot <镜> --role <start|end> --change "<英文修改要求>" --note "<用户原话>"`,以本镜当前这张图为参考按意见新出一张入库(`<原 key>_rev<N>`)并只替换本镜该条目、自动 sync 本组 prompt;原图不动,引用同一原图的其它镜不受影响(`docs/shot_plates.md`「按修改意见重出一张」)。修改单头 `files:` 带当前图路径,定位文本带镜号/起点或终点/库 key 与该命令。

## 修改记录文件

`data/projects/<slug>/runs/revisions/<run_id>.json`:

```json
{"id":"…","project":"…","created":0,"ended":0,"status":"done","target":{…},"message":"用户原话",
 "files":["运行期间写过的文件"],"record":{"changed_files":[],"dirty_nodes":[],"signature_expired":"","checks":[],"notes":""},
 "rerun_downstream":false,"consumed":null|{"by":"watchdog|reviser:<run_id>","at":0}}
```

## 红线(修改师同样受约束)

§7E 修正阶段形象红线(重出必带在库概念图 + 风格锚)、§7B 分辨率一律草稿档、§8A TTS 不进成片对白、冻结版必须新开版本、不改 dag.json、不派单、不换引擎。

## 已知边界

- 冲突提示是启发式(只看 `/api/v1/runs` 最近 100 条消息前 120 字符),不拦发送。
- 修改师无状态:连续两条修改互不知情,靠头里列出的上次修改记录衔接。
- `rerun_downstream=否` 且项目未开自动运行时,记录会留到下次任一唤醒消息才交总制片。
