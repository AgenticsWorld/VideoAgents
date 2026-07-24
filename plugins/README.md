# plugins/ — Agent 插件(声明式团队扩展)

内置 83 个 Agent 覆盖「小说→视频」主流程;主流程之外的业务(衍生小说、漫画改编、有声书……)
做成插件放本目录,**复制目录即安装**,不改内核。机制与调度纪律的权威定义见 `agents/WORKFLOW.md` §10。

首个官方插件:`derivative-fiction/`(基于世界圣经/角色/资产的衍生小说创作团队)。

## 包格式

```
plugins/<plugin-name>/
├── plugin.json            # manifest(JSON;字段见下)
├── README.md              # 插件说明(建议:一句话定位 + 流程图 + 产物清单)
├── agents/
│   └── <NN-category>/<name>/SOUL.md   # 沿用 agents/_TEMPLATE.md 骨架撰写
└── workflows/<name>.yaml  # 可选:插件独立流程 DAG(与 agents/workflow.yaml 同构)
```

## plugin.json 字段

```json
{
  "name": "derivative-fiction",          // 必填;须与目录名一致,[A-Za-z0-9][\w-]{0,59}
  "version": "1.0.0",
  "description": "一句话说明(会注入 orchestrator 系统提示词)",
  "categories": {                        // 新类别注册:目录名 → 控制台显示名
    "13-derivative-fiction": "衍生创作"  // 编号用 13+ 段,避让内置 00-12
  },
  "agents": [                            // 成员清单;id 恒为「类别/名字」两段
    {"id": "13-derivative-fiction/prose-writer"},
    {"id": "11-qa/prose-qa"}             // 也可放进内置类别;11-qa/ 前缀自动无状态并发
  ],                                     // 可选字段:stateless(每单自足可并发)、dispatcher(拥有派单权)
  "workflows": ["workflows/novel.yaml"], // 流程 DAG 文件(相对插件根)
  "outputs_ns": "derivative",            // 产物命名空间:data/projects/<slug>/derivative/
  "requires": {                          // 前置声明(orchestrator 派单前核对)
    "artifacts": ["bible/world.json", "bible/characters/index.json"],
    "plugins": []
  }
}
```

## 生效方式

- 把插件目录复制进本目录(或 Web 控制台 ⚙️ 设置 →「插件」页上传 zip),约 30 秒内被发现;
  **安装后默认停用**,须在「插件」页手动启用才生效。启停/删除在「插件」页操作,
  启用名单存 `webui/state.json`(`plugins_enabled`)。
- manifest 校验不过(agent id 撞名、缺 SOUL.md、JSON 损坏)的插件**整体不注册**,错误原因在「插件」页可见。
- 启用后:成员出现在控制台左侧列表并可被总制片派单;其系统提示词自动带插件身份
  (所属插件/流程文件/产物命名空间);总制片的系统提示词自动带「已启用插件」清单。

## 编写守则

1. **SOUL.md 按 `agents/_TEMPLATE.md` 骨架写**:职责、边界(至少 2 条「不做什么」并指명归属)、
   输入输出路径、示例工单、DoD、上下游——orchestrator/context 靠这些机械定位信息。
2. **workflow DAG 与主流程同构**:phases/tasks/depends_on/for_each/validation(auto+rubric+qa)/gate,
   节点 id 用插件自有前缀(如 `nv0-`),不与主流程 `p0-`–`p11-`、`g0`–`g10` 冲突。
3. **产物只写自己的命名空间**(`outputs_ns`);新设定写 `<outputs_ns>/bible-delta/`,
   严禁触碰正史 `bible/`(升格走 memory-bible 仲裁 + 用户签字)。
4. **复用优先**:评分用既有 rubric 家族(writing_v1/creative_v1/analysis_v1…),
   QA 优先挂内置 `11-qa/*`;确需新维度才建插件自己的 QA Agent。
5. **纯声明式**:插件内不放可执行代码;运行期一次性脚本照常落项目 `code/`。

## 安全红线

SOUL.md 会逐字注入模型系统提示词——**安装第三方插件前必须人工审阅全部内容**,
警惕越权指令(改正史/越命名空间写入/伪造回执)。有疑虑就不装。
