# Third-Party Software

VideoAgents integrates with third-party Python packages, command-line tools, and
hosted services. They are not relicensed by this repository and remain subject
to their own terms.

Core Python dependencies include FastAPI, Uvicorn, NumPy, SciPy, PyYAML, and
qrcode. Optional integrations include DeepAgents, LangChain OpenAI, boto3,
Volcengine TOS, Alibaba Cloud OSS, and Tencent Cloud COS SDKs.

External tools and services may include FFmpeg, Claude, Codex, OpenRouter,
Volcengine ModelArk, BytePlus ModelArk, ComfyUI, and compatible object
storage services. Installing or configuring an integration does not grant model,
content, trademark, or service rights. Review the applicable upstream license
and service terms before use or redistribution.

The offline whitebox viewer bundles Three.js 0.180.0 (MIT), including its core,
WebGL renderer and OrbitControls. Copyright notice and license are retained in
`apps/web/static/vendor/three/LICENSE`. OrbitControls' package import is changed
to a relative local import. Upstream: https://github.com/mrdoob/three.js/tree/r180.
The whitebox viewer also bundles Three.js' `addons/postprocessing/Pass.js` (MIT, same
license file) for the world viewer.

The scene world viewer bundles Spark (`@sparkjsdev/spark`, MIT, Copyright © 2025
World Labs Technologies, Inc.) to render Gaussian-splat worlds (`.spz`) in the browser.
License is retained in `apps/web/static/vendor/spark/LICENSE`. Upstream:
https://github.com/sparkjsdev/spark. World generation itself calls the hosted World
Labs Marble API (https://docs.worldlabs.ai/api) with the user's own API key.

The 演示视频 (code-video) project type bundles files from Lemo-Opuscar
(https://github.com/lemomo-ai/lemo-opuscar, MIT, Copyright (c) 2026 LemoLab), pinned to the
commit recorded in `modules/codevideo/lib/upstream.json` and synced by
`scripts/sync_lemo_opuscar.py`: the page helpers and audio tools under `modules/codevideo/lib/core/`
(`sampler.py` carries two marked VideoAgents patches: a configurable sample-library folder with
optional automatic download, and a SciPy fallback when `soxr` is missing), the 43 style
specifications under `agents/workflows/codevideo/styles/`, and gallery frames re-encoded as WebP in
`apps/web/static/styles/code/frames/`, plus the 98 style prompts of Lemo-Opuscar's OPUSCAR 98 film
(`agents/workflows/codevideo/styles/opuscar98/styles.json`, offered as "light" styles; the per-film shot lists and
direction notes are not copied) with one frame per film re-encoded as WebP in
`apps/web/static/styles/code/frames98/`, shown only as style previews (the character in those frames belongs to
OPUSCAR 98 and is never carried into users' films). The license is retained in
`modules/codevideo/lib/LICENSE.lemo-opuscar` and `agents/workflows/codevideo/styles/LICENSE.lemo-opuscar`.
The four display fonts in `modules/codevideo/lib/core/fonts/` are OFL 1.1 subsets (details and the
full license text in `modules/codevideo/lib/core/fonts/OFL.md`). The same project type bundles
Three.js 0.170.0 (MIT; `build/three.module.js` and `examples/jsm` without the Draco, Basis,
Rhino3dm and Ammo decoders) in `modules/codevideo/lib/vendor/three/`, license retained there,
because Lemo-Opuscar's post-processing code targets r170. Sample libraries (VSCO 2 CE, VCSL,
FreePats, Karoryfer: CC0; Salamander Grand Piano: CC BY 3.0; MuldjordKit: CC BY 4.0), Poly Haven
HDRIs and models (CC0), and the styles' demo-film sources are not bundled: they are downloaded on
demand from the Lemo-Opuscar release assets and repository at the pinned commit, verified by
SHA-256. Films made with this project type carry no LemoLab credit; the instruments a score uses
are listed in that film's `CREDITS`.

The design style library (`apps/web/static/styles/style_library.json`) includes the 916 visual styles of
Flick's Flickpedia gallery (https://flick.art/flickpedia/visual-styles, collected 2026-10-20): each style's
gallery image, resized to 640 px wide and re-encoded as WebP, is the cover of a style card (882 new
styles, plus 34 existing styles whose covers were replaced), and the style prompts were written in
Chinese from Flick's style descriptions and those images, then translated. Entries taken from Flick carry
`"source": "flick.art"` and the original Flick style name in `source_name`. These images and descriptions
remain subject to Flick's terms.

On Windows x64 the desktop client can download FFmpeg from the VideoAgents package
mirrors when none is installed. The package is the unmodified
`ffmpeg-9.0.2-essentials_build.zip` static build from gyan.dev (FFmpeg 9.0.2, GPL v3),
pinned with its SHA-256 in `apps/desktop/ffmpeg-artifact.json`. Its `LICENSE` and
`README.txt` (build configuration and the source revision,
https://github.com/FFmpeg/FFmpeg/commit/946fcce07b) are installed next to the
binaries; `ffplay.exe` is removed after extraction. FFmpeg runs as a separate program
and is not linked into VideoAgents. Upstream: https://www.gyan.dev/ffmpeg/builds/ and
https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2.

The optional HunyuanWorld world-generation channel (the worker lives in the separate
repository https://github.com/AgenticsWorld/VideoAgents-hunyuan-rpc, deployed by the
user on their own GPU host) drives Tencent HunyuanWorld-1.0
(https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0), which is subject to the
TENCENT HUNYUANWORLD-1.0 COMMUNITY LICENSE AGREEMENT: the license does not apply in
the EU, UK and South Korea, and products or services with more than 1 million MAU
must obtain a separate commercial license from Tencent. This repository neither
bundles nor redistributes the model weights; the user deploys and operates them.
SPZ export uses a clean-room encoder written against the public format
(nianticlabs/spz, MIT).
