# FastH3 VSA T2VA ComfyUI Workflow

This API workflow is for `FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree`.
It requires the experimental `ComfyUI-FastH3-VSA` node pack, the matching
ComfyUI gate-loader patch, and the Comfy Kitchen VSA native extension.

The graph is text-to-audio-video only. It intentionally has no image, first-frame,
last-frame, or reference-image input. VideoAgents live mode therefore expresses
continuity in the prompt and does not submit its reference images to this graph.

Required files:

```text
models/diffusion_models/minimax_h3_fastvideo_vsa_datafree_1300step_4step_int8_convrot.safetensors
models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
```

The workflow uses four sampler steps, `res_multistep`, simple scheduling, VSA
keep percent `10.0`, and video/audio sigma shifts `12.0` and `3.0`. The output is
an H.264 MP4 with stereo AAC audio at 24 fps.
