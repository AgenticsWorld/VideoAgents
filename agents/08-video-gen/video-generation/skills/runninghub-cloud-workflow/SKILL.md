---
name: runninghub-cloud-workflow
description: Use when the video-generation agent submits video jobs while the ComfyUI channel runs in RunningHub mode (rh_cn/rh_ai) — explains how genmedia parameterizes the cloud workflow (prompt/duration direct binding, reference upload and rewiring, full workflow override on create) and which template values stay fixed, so submissions and failure reports stay accurate.
metadata:
  skill_version: 0.1.0
  owner: video-generation
  tags:
    - runninghub
    - comfyui
    - minimax-h3
    - video-generation
  api_doc: https://www.runninghub.ai/runninghub-api-document
---

# RunningHub 云端工作流参数化调用

## 目的

RunningHub(.cn 国内站 / .ai 国际站,账号与 API Key 不互通)是第三方云托管 ComfyUI:工作流保存在用户的 RunningHub 工作区,本地不跑 ComfyUI。`genmedia.py` 提交时把**本地改写后的完整工作流 JSON 经 `/task/openapi/create` 的 `workflow` 字段整体覆盖云端模板**(`workflowId` 仅作关联;该覆盖机制已于 2026-08-10 cui2 项目真机实测生效)。

本 skill 只解释参数如何进入云端工作流与排错口径。**调用入口不变**:一律 `python3 modules/genmedia.py video ...`,严禁绕过 genmedia 手工拼 RunningHub REST 请求。

## 适用条件

系统提示词出现「RunningHub 云端工作流视频生成 Skill」注入段即满足(= Web 控制台「🎨 生成模型」视频 ComfyUI 渠道已选运行方式 RunningHub 国内/国际站,并已验证选定云端工作流)。渠道/工作流由用户配置,严禁自行切换。

## 调用(与其他渠道完全一致)

```bash
# 先 dry-run 核对生效 provider/mode(rh_cn|rh_ai)与参数组合(不上传、不计费)
python3 modules/genmedia.py video --prompt "<video_prompt 逐字>" \
  --output <路径.mp4> --ref <锚点图...> [--audio-ref <音色样本...>] \
  --duration <秒> --aspect 16:9 --resolution <档位> \
  [--generate-audio on] [--return-last-frame tail.png] [--ref-image-size max] --dry-run
# 通过后去掉 --dry-run 正式提交
```

成功输出两行:`[genmedia] RunningHub 任务已创建 <远端taskId> → <文件名>` 与 `已生成: <绝对路径>`。**远端 taskId 必须记入产物 meta 与 result.json**(排错、对账、防重复计费的唯一凭据)。

## 参数如何进入云端工作流(机制)

1. **模板来源**:所选 `rh_workflow_id` 的 JSON(API 格式)经 `/api/openapi/getJsonApiFormat` 拉取,缓存在 `data/.videoagents/rh_workflows/{mode}-{id}.json`(设置页「验证并添加」时写入,运行时缓存优先)。设置页每次「保存设置」会自动把当前选中工作流的缓存同步为云端最新版;用户在 RunningHub 网页端改了模板后,重新保存设置(或重新验证)即可刷新,未保存前提交的仍是旧结构。
2. **占位符替换**:模板中的 `{{TOKEN}}` 占位符(PROMPT/SEED/WIDTH/HEIGHT/H3_FRAMES 等,与本地 `comfy/*.json` 同一套约定)先做整树替换。工作区导出件通常**没有占位符**、全是作者演示字面值——此时替换空转,靠下面两步兜住。
3. **H3 Ref2VA 直绑**(模板含 `MiniMaxH3ReferenceToVideo` 节点时):
   - `--prompt` 顺 H3 节点 `prompt` 连线改写其上游文本 primitive(或字面值位),作者的演示提示词不会漏进生产请求;
   - `--duration` 顺 `length` 连线改写喂秒→帧换算表达式的时长 primitive(直连数值位则按 17n+5 帧数语义填),模板默认时长不会静默生效;
   - 连线形态不可识别时**提交前报错**(不发请求、不计费),按报错提示改模板或加占位符。
   - `--ref-image-size` 覆写 Ref2VA 节点同名输入:默认/不传=match(参考图压到与输出同
     像素面积,速度/成本优先);**仅当工单明确要求高身份保真(如人脸与参考图不一致的
     重出单)时**传 `max`(短边 ≤2048 不压缩直进模型,更慢更贵,勿全量默认使用)。
4. **参考素材**:`--ref`/`--audio-ref` 逐个经 `/task/openapi/upload` 上传(**单文件 ≤30MB**),返回 fileName 后动态新增 `LoadImage`/`LoadAudio` 节点接到 `ref_images.ref_image_N` / `ref_audios.ref_audio_N`(0 起,顺序=命令行顺序=prompt 内 `[Image N]`/`[Audio N]` 序号-1)。模板作者遗留的演示素材连线与节点会被**全部清除**,只提交本单素材。
5. **提交与取回**:`create` 整体覆盖提交 → `/task/openapi/status` 5s 轮询(QUEUED/RUNNING/SUCCESS/FAILED)→ SUCCESS 后 `/task/openapi/outputs` 取 `fileUrl` 公网直链下载到 `--output`;`--return-last-frame` 由本地 ffmpeg 从成片抽尾帧,不占云端节点。

## 哪些参数进不了云端模板(如实记录,不得自行拒交)

- **`--seed` 不生效**:模板 `RandomNoise` 的 noise_seed 是字面值,同参重投=同噪声。
- **`--resolution`/`--aspect` 对无占位符模板不生效**:输出尺寸由模板自身节点(如 ResolutionSelector 的 megapixels/画幅)决定。提交后必须 ffprobe 实测,实际分辨率/近似画幅比**如实写入回执**;与请求档位不符时报告差异,由用户决定是否改模板。
- steps/sampler/fps/模型文件等模板固定项同理。要参数化这些,唯一正道是改 RunningHub 工作区模板(改字面值或加 `{{TOKEN}}`)后在设置页重新验证。

## 排错口径(保留原文上报,严禁换渠道/换工作流重试)

- **建任务即失败 / taskStatus=FAILED**:报错含 `promptTips` 原文——通常是节点或模型文件在云端缺失(RunningHub 无 `/object_info` 预检,提交才暴露)。原文上报,不要瞎猜改参。
- **任务运行失败**:报错含 outputs 接口的 `failedReason`(code 805 携带)原文。
- **上传失败**:单文件超 30MB 直接本地报错(不发请求);先压缩/降采样素材。
- **超时**:默认超时后自动尝试 `cancel`,报错含 taskId;先查 RunningHub 网页端任务状态再决定重投,防止双份计费。
- API Key 未配或未选工作流:本地即报错,提示用户去「🎨 生成模型」页配置(.cn/.ai Key 按站点分开保存,不互通)。

## 计费纪律

云端按任务计费。失败先读 promptTips/failedReason 原文定位,**严禁同参盲重投**;重投前核对远端 taskId 台账,确认上一单确已失败或取消。
