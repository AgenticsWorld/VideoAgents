# MiniMax H3 Image ComfyUI Multi-Reference Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-minimax-h3-image-i2i-api.json`](image-minimax-h3-image-i2i-api.json) is the reference-image companion of
[`image-minimax-h3-image-t2i-api.md`](image-minimax-h3-image-t2i-api.md), for
[MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image). It is the same graph with
**1 to 10 reference images** wired to `MiniMaxH3ImageConditioning`'s `images.image_1` … `images.image_10`, with
the VAE connected. Each reference is read by the Qwen3-VL text encoder and also anchored in the DiT as VAE
latents. Sampling is the same as text-to-image: 6 steps, `cfg 1.0`, `euler` / `simple`, as in the official
ComfyUI edit template
[`image_minimax_h3_image_edit.json`](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_edit.json).
No custom node is needed.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`.

> **Not tested on a GPU yet (2026-11-12).** The graph was checked against ComfyUI source (commit `c5751c81`).
> `genmedia.py`'s binding was run on it: 1, 3 and 10 references bind in order with no dangling links, and an
> 11th is rejected before submission. No image has been generated with it, and time and VRAM with several
> references are unmeasured. For the license, see the
> [text-to-image description](image-minimax-h3-image-t2i-api.md).

### Install

Same ComfyUI version (0.45.0 or newer) and the same three model files as the text-to-image workflow. See the
install section of [`image-minimax-h3-image-t2i-api.md`](image-minimax-h3-image-t2i-api.md#install). No extra
downloads are needed.

### Console Configuration

This workflow goes into the image channel's **`ref_workflow`** field, next to the main workflow (run mode
**local**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-minimax-h3-image-t2i-api.json",
      "ref_workflow": "comfy/image-minimax-h3-image-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

`genmedia.py` switches to `ref_workflow` whenever `--ref` images are given, and uploads every reference to
ComfyUI.

### How References Are Bound

All ten `LoadImage` nodes (ids `101`…`110`) carry the `FIRST_FRAME` placeholder only so the template parses.
The actual binding is by position: `genmedia.py` binds the N-th `--ref` to the N-th `LoadImage` and removes each
unused `images.image_N` slot together with its `LoadImage`. More than 10 `--ref` images are rejected before
anything is submitted.

The model accepts up to 16 references. To allow more, fork the JSON and add `LoadImage` nodes `111`… wired to
`images.image_11`… . Every reference adds tokens that go through all 6 sampling steps, so time and memory grow
with the number and size of the references.

References need no preparation. The node makes its own scaled copies: a 1024 px short edge for the text
encoder, and a short edge between 1024 and 2048 px for the VAE.

### Prompting

- The first `--ref` is `image_1`, the picture being edited. The node labels the references for the text encoder
  as `Picture 1`, `Picture 2`, … in upload order. Refer to them in the prompt as `<Picture 1>`, `<Picture 2>`, …,
  as the model card does. For example: `Replace the taxi in <Picture 1> with the truck from <Picture 2>,
  keeping the same street and light.`
- The official edit template turns on a prompt expander by default. It turns a short instruction into a full
  description of the target image. This workflow does not include the expander, so write the full English
  description of the finished image, not a terse instruction.
- There is no negative prompt (`cfg 1.0`). Use `negative_mode: "append_exclusions"`, as in text-to-image.

### Output Size

The canvas follows the request, not the first reference. Nodes `10`–`12` scale `WIDTH`x`HEIGHT` to 4 MP on the
32-pixel grid, and the final `ImageScale` brings the result back to `WIDTH`x`HEIGHT`, exactly as in
[text-to-image](image-minimax-h3-image-t2i-api.md#runtime-parameters). When the first reference has a different
aspect ratio from the request, an in-place edit is recomposed to the requested frame.

The official edit template instead scales the picture being edited to 4 MP and uses its size. To get that
behavior, fork the JSON and point node `11`'s `image` at `["101", 0]` instead of node `10`. The final
`ImageScale` still fits the result to `WIDTH`x`HEIGHT` with a center crop. If an edit runs out of memory, lower
`megapixels` on node `11`, as the official tutorial recommends.

### Differences From the Official Template

- The prompt expander branch (`TextGenerateV2` + `qwen3.5_9b_w4a8_gptq`, on by default there) is removed.
- The canvas follows `WIDTH`x`HEIGHT` at 4 MP instead of the first reference's size, and references are passed
  to the conditioning node unscaled instead of being resized first. The node scales its own copies, as described
  above.
- There are ten reference slots instead of one, the seed comes from `SEED`, and the output prefix is
  `videoagents/minimax-h3-image-i2i`. The `ImageCompare` preview is removed.

---

## 中文

[`image-minimax-h3-image-i2i-api.json`](image-minimax-h3-image-i2i-api.json) 是
[`image-minimax-h3-image-t2i-api.md`](image-minimax-h3-image-t2i-api.md) 的参考图配套工作流,模型为
[MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image)。节点图与文生图相同,只是把 **1–10 张参考图**
接到 `MiniMaxH3ImageConditioning` 的 `images.image_1` … `images.image_10`,并接上 VAE。每张参考图既交给 Qwen3-VL 文本编码器,
也作为 VAE latent 锚定进 DiT。采样与文生图一致:6 步、`cfg 1.0`、`euler` / `simple`,同官方 ComfyUI 编辑模板
[`image_minimax_h3_image_edit.json`](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_edit.json)。
不需要任何自定义节点。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `SEED`。

> **尚未上 GPU 实测(2026-11-12)。** 节点图已对照 ComfyUI 源码(commit `c5751c81`)核对过,`genmedia.py` 的绑定逻辑也在它上面跑过:
> 1、3、10 张参考图都按顺序绑定且没有悬空连线,第 11 张在提交前被拒收。还没用它出过图,多张参考图时的耗时和显存都没测。
> 许可见[文生图说明](image-minimax-h3-image-t2i-api.md)。

### 安装

ComfyUI 版本(0.45.0 及以上)与三个模型文件都与文生图工作流相同,见
[`image-minimax-h3-image-t2i-api.md`](image-minimax-h3-image-t2i-api.md#安装) 的安装一节。不需要额外下载。

### 控制台配置

本工作流填在图片渠道的 **`ref_workflow`** 字段,与主工作流并列(运行方式 **本地**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-minimax-h3-image-t2i-api.json",
      "ref_workflow": "comfy/image-minimax-h3-image-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

传入 `--ref` 时,`genmedia.py` 会自动改用 `ref_workflow`,并把每张参考图上传到 ComfyUI。

### 参考图如何绑定

10 个 `LoadImage` 节点(id `101`…`110`)填的 `FIRST_FRAME` 占位符只是为了让模板能解析,真正的绑定按位置进行:`genmedia.py`
把第 N 张 `--ref` 绑到第 N 个 `LoadImage`,用不到的 `images.image_N` 槽位连同它的 `LoadImage` 一起摘掉。超过 10 张 `--ref`
会在提交前被拒收。

模型最多支持 16 张参考图。需要更多时,fork 本 JSON,加 `LoadImage` 节点 `111`… 并接到 `images.image_11`…。每张参考图都会带来
一批 token,参与全部 6 步采样,所以耗时和内存随参考图的张数与尺寸增长。

参考图不需要预处理。节点会自己做缩放副本:文本编码器看短边 1024 px 的版本,VAE 用短边在 1024–2048 px 之间的版本。

### 提示词写法

- 第一张 `--ref` 是 `image_1`,即被编辑的那张图。节点按上传顺序把参考图标为 `Picture 1`、`Picture 2`… 交给文本编码器。
  提示词里按模型卡的写法用 `<Picture 1>`、`<Picture 2>`… 指代,例如:`Replace the taxi in <Picture 1> with the truck from
  <Picture 2>, keeping the same street and light.`
- 官方编辑模板默认开启提示词扩写,把一句简短指令扩成对目标画面的完整描述。本工作流不带扩写,所以要写成品画面的完整英文描述,
  不要只写一句简短指令。
- 没有负面提示词(`cfg 1.0`)。与文生图一样,用 `negative_mode: "append_exclusions"`。

### 输出尺寸

画布跟随请求尺寸,而不是第一张参考图。节点 `10`–`12` 把 `WIDTH`x`HEIGHT` 缩放到 32 像素网格上的 4 MP,末端 `ImageScale` 再缩回
`WIDTH`x`HEIGHT`,与[文生图](image-minimax-h3-image-t2i-api.md#运行时参数)完全一样。第一张参考图的宽高比与请求不同时,
原地编辑会按请求的画幅重新构图。

官方编辑模板的做法是把被编辑的图缩放到 4 MP,并用它的尺寸作画布。想要这种行为,fork 本 JSON,把节点 `11` 的 `image` 从节点 `10`
改接 `["101", 0]`;末端 `ImageScale` 仍会把结果居中裁切到 `WIDTH`x`HEIGHT`。编辑时爆显存,可以按官方教程的建议调低节点 `11` 的
`megapixels`。

### 与官方模板的差异

- 去掉提示词扩写分支(`TextGenerateV2` + `qwen3.5_9b_w4a8_gptq`,官方模板里默认开启)。
- 画布按 `WIDTH`x`HEIGHT` 取 4 MP,而不是第一张参考图的尺寸;参考图不先缩放就直接接入条件节点,由节点自己做缩放副本(见上文)。
- 参考图位从 1 个扩到 10 个,种子取 `SEED`,输出前缀为 `videoagents/minimax-h3-image-i2i`,去掉了 `ImageCompare` 对比预览。
