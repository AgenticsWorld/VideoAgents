# Qwen Image 2.1 bf16 ComfyUI Multi-Reference Image-to-Image Workflow

[English](#english) | [中文](#中文)

## English

[`image-qwen-image-2.1-bf16-i2i-api.json`](image-qwen-image-2.1-bf16-i2i-api.json) is the reference-image
companion of [`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md). It is the same graph as
[`image-qwen-image-2.1-q8-gguf-i2i-api.json`](image-qwen-image-2.1-q8-gguf-i2i-api.md) with node `1`
replaced by the core `UNETLoader` loading `qwen_image_2.1_bf16.safetensors`, so it needs **no custom node**.
It accepts **1 to 10 reference images**, wired to `TextEncodeQwenImage21`'s `images.image_1` …
`images.image_10` with the VAE connected: each reference is seen by the Qwen3-VL text encoder and spliced into
the sequence as VAE latents. Sampling is the same as text-to-image: 25 steps, `cfg 1.0`, `euler` / `simple`.

Placeholders: `PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`.

Tested 2026-10-20 on a local ComfyUI 0.42.1 (Windows, RTX 4080 SUPER 32 GB, 32 GB RAM) with two reference
images and a 1280x720 output: the image came out right, but execution took about 750 s — the same machine
renders text-to-image at that size in about 20 s — and the ComfyUI HTTP server stopped answering for minutes at
a time while it ran, so `genmedia.py` gave up polling after 669 s and reported a failure although ComfyUI
finished the job. The bf16 diffusion model and text encoder together are about 31.7 GB; the cause was not
investigated further. On the same machine the Q8_0 companion
[`image-qwen-image-2.1-q8-gguf-i2i-api.json`](image-qwen-image-2.1-q8-gguf-i2i-api.md) (diffusion model
7.6 GB, needs ComfyUI-GGUF) ran the same request in about 38 s with a nearly identical image — prefer it on a
32 GB card. The int8 files listed in
[`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md#install) are the option without a custom
node (not tested). 3 to 10 references were not run.

> **Comfy Cloud** cannot run it as is (the diffusion model and VAE are not in Cloud's shared model catalog)
> and the model is **non-commercial only** (Qwen Research License). See
> [`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md).

### Install

Same ComfyUI version (0.37.0 or newer) and same three model files as the text-to-image workflow; see the
install section of [`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md). No extra
downloads are needed.

### Console Configuration

This workflow goes into the image channel's **`ref_workflow`** field, next to the main workflow (run mode
**local**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-bf16-t2i-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-bf16-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

`genmedia.py` switches to `ref_workflow` automatically whenever `--ref` images are supplied and uploads every
reference to ComfyUI.

### How References Are Bound

All ten `LoadImage` nodes carry the `FIRST_FRAME` placeholder only so the template parses; the actual
binding is positional. `genmedia.py` binds the N-th `--ref` to the N-th `LoadImage` (node ids 101…110) and
removes every unused `images.image_N` slot together with its `LoadImage`. More than 10 `--ref` images are
rejected before anything is submitted. In the prompt, refer to the references by order; the source workflow
writes them as `<image1>`, `<image2>`, ….

### Differences From the Official Template

The canvas (always an `EmptyLatentImage` at `GEN_WIDTH`x`GEN_HEIGHT` rather than the first reference's size),
the reference size (`resolution` `1024` rather than `0`), the dropped `QwenImage21Cache` node and the
`SaveImage` output are the same choices as in the GGUF companion; the reasons and how to fork each one back
are in
[`image-qwen-image-2.1-q8-gguf-i2i-api.md`](image-qwen-image-2.1-q8-gguf-i2i-api.md#what-differs-from-the-source-workflow).

### Runtime Parameters

`GEN_WIDTH` / `GEN_HEIGHT`, the final `ImageScale` to `WIDTH`x`HEIGHT`, and the absence of a `NEGATIVE`
placeholder (`cfg 1.0`; use `negative_mode: "append_exclusions"`) behave as in the text-to-image workflow.

---

## 中文

[`image-qwen-image-2.1-bf16-i2i-api.json`](image-qwen-image-2.1-bf16-i2i-api.json) 是
[`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md) 的参考图配套工作流。与
[`image-qwen-image-2.1-q8-gguf-i2i-api.json`](image-qwen-image-2.1-q8-gguf-i2i-api.md) 是同一张图,只把节点 `1`
换成原生 `UNETLoader` 加载 `qwen_image_2.1_bf16.safetensors`,因此**不需要任何自定义节点**。接受
**1–10 张参考图**,接在 `TextEncodeQwenImage21` 的 `images.image_1` … `images.image_10` 上并连上 VAE:每张参考图
既给 Qwen3-VL 文本编码器看,也以 VAE latent 拼进序列。采样与文生图相同:25 步、`cfg 1.0`、`euler` / `simple`。

占位符:`PROMPT` `FIRST_FRAME` `WIDTH` `HEIGHT` `GEN_WIDTH` `GEN_HEIGHT` `SEED`。

2026-10-20 实测:本地 ComfyUI 0.42.1(Windows、RTX 4080 SUPER 32 GB、内存 32 GB),两张参考图、出 1280x720,成图
正确,但执行约 750 秒——同一台机器文生图出同尺寸约 20 秒——而且执行期间 ComfyUI 的 HTTP 服务一连几分钟不响应,
`genmedia.py` 轮询到 669 秒超时放弃并报失败,实际 ComfyUI 那边跑完了。bf16 底模与文本编码器合计约 31.7 GB;
原因没有进一步排查。同一台机器上 Q8_0 版
[`image-qwen-image-2.1-q8-gguf-i2i-api.json`](image-qwen-image-2.1-q8-gguf-i2i-api.md)(底模 7.6 GB,需要
ComfyUI-GGUF)同样的请求约 38 秒、成图几乎一致——32 GB 显卡上优先用它。不装自定义节点的办法是
[`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md#安装) 里列的 int8 文件(未实测)。3–10 张
参考图没有跑过。

> **Comfy Cloud** 原样跑不了(底模与 VAE 不在云端公共模型目录里),模型**仅限非商业用途**
> (Qwen Research License)。详见 [`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md)。

### 安装

ComfyUI 版本(0.37.0 及以上)和三个模型文件都与文生图工作流相同,见
[`image-qwen-image-2.1-bf16-t2i-api.md`](image-qwen-image-2.1-bf16-t2i-api.md) 的安装章节,无需额外下载。

### 控制台配置

本工作流填入图片渠道的 **`ref_workflow`** 字段,与主工作流并列(运行方式选 **本地**):

```json
{
  "image": {
    "provider": "comfyui",
    "comfyui": {
      "workflow": "comfy/image-qwen-image-2.1-bf16-t2i-api.json",
      "ref_workflow": "comfy/image-qwen-image-2.1-bf16-i2i-api.json",
      "negative_mode": "append_exclusions",
      "checkpoint": ""
    }
  }
}
```

只要生成请求带 `--ref` 参考图,`genmedia.py` 就会自动切换到 `ref_workflow`,并把每张参考图上传到 ComfyUI。

### 参考图怎么绑

十个 `LoadImage` 都写 `FIRST_FRAME` 只是为了让模板可解析,实际按位置绑定:`genmedia.py` 把第 N 张 `--ref` 绑到
第 N 个 `LoadImage`(节点 101…110),没用到的 `images.image_N` 槽位连同对应的 `LoadImage` 一并摘掉;超过 10 张在
提交前报错。提示词里按顺序指代参考图;原工作流的写法是 `<image1>`、`<image2>`……

### 与官方模板的差别

画布(一律用 `GEN_WIDTH`x`GEN_HEIGHT` 的 `EmptyLatentImage`,不按第一张参考图尺寸)、参考图尺寸(`resolution` 用
`1024` 而不是 `0`)、去掉 `QwenImage21Cache` 节点、输出用 `SaveImage`,这几处取舍都与 GGUF 版相同;原因与各自的
改回方法见 [`image-qwen-image-2.1-q8-gguf-i2i-api.md`](image-qwen-image-2.1-q8-gguf-i2i-api.md#与原工作流的差别)。

### 运行时参数

`GEN_WIDTH` / `GEN_HEIGHT`、末端 `ImageScale` 到 `WIDTH`x`HEIGHT`、没有 `NEGATIVE` 占位符(`cfg 1.0`;用
`negative_mode: "append_exclusions"`)这几点都与文生图工作流一致。
