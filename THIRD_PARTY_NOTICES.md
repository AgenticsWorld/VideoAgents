# Third-Party Software

VideoAgents integrates with third-party Python packages, command-line tools, and
hosted services. They are not relicensed by this repository and remain subject
to their own terms.

Core Python dependencies include FastAPI, Uvicorn, NumPy, SciPy, PyYAML, and
qrcode. Optional integrations include boto3, Volcengine TOS, Alibaba Cloud OSS,
and Tencent Cloud COS SDKs.

The built-in Agentics engine bundles Pi Coding Agent (`@earendil-works/pi-coding-agent`,
MIT, https://github.com/earendil-works/pi) together with its npm dependencies (MIT,
Apache-2.0, BSD, ISC and similar permissive licenses), pinned in `vendor/pi/package.json`
and `vendor/pi/package-lock.json`; each package declares its license in its
`package.json` (with a LICENSE file where the package ships one) under
`vendor/pi/node_modules`. The desktop app runs it with Electron's own Node.

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

The 动效短片 (code-video) project type bundles files from Lemo-Opuscar
(https://github.com/lemomo-ai/lemo-opuscar, MIT, Copyright (c) 2026 LemoLab), pinned to the
commit recorded in `modules/codevideo/lib/upstream.json` and synced by
`scripts/sync_lemo_opuscar.py`: the page helpers and audio tools under `modules/codevideo/lib/core/`
(`sampler.py` carries two marked VideoAgents patches: a configurable sample-library folder with
optional automatic download, and a SciPy fallback when `soxr` is missing), the 59 style
specifications under `agents/workflows/codevideo/styles/`, and gallery frames re-encoded as WebP in
`apps/web/static/styles/code/frames/`, plus the 98 style prompts of Lemo-Opuscar's OPUSCAR 98 film
(`agents/workflows/codevideo/styles/opuscar98/styles.json`, offered as "light" styles; the per-film shot lists and
direction notes are not copied) with one frame per film re-encoded as WebP in
`apps/web/static/styles/code/frames98/`, shown only as style previews (the character in those frames belongs to
OPUSCAR 98 and is never carried into users' films). The license is retained in
`modules/codevideo/lib/LICENSE.lemo-opuscar` and `agents/workflows/codevideo/styles/LICENSE.lemo-opuscar`.
The gallery frames in `frames/` and `frames98/` are stills from Lemo-Opuscar's demo films and OPUSCAR 98,
© LemoLab, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/; source
https://github.com/lemomo-ai/lemo-opuscar); they were resized to 640 px wide and re-encoded as WebP.
Third-party material visible in some frames (archive footage, photographs, maps and data, chiefly in the
Edit & Documentary and Evidence Films styles) keeps its own license, as listed in that style's demo
`CREDITS` (and `SOURCES.md`, where present) in the upstream repository at the pinned commit.
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

The design style library also includes 335 visual styles from handraw-style by yang0
(https://github.com/yang0/handraw-style, v1.9.8, commit 572dff8, collected 2026-11-01), a library of
367 hand-drawn and photographic styles. Each added style's preview image from the repository's
`images/individual/` is the cover of a style card (`style1035`–`style1369.webp`): caption labels
printed on some images (artist or style names, work titles) were cropped off or painted over, and for
FB-022, FF-002 and FF-013 one panel of the repository's `_grid` image is used instead. The style
prompts were written in Chinese from the repository's style traits and those images, then translated;
they name no artists or works. Entries taken from handraw-style carry `"source": "handraw-style"` and
the original style number and name in `source_name`. handraw-style is licensed as follows:

> MIT License (with Attribution Requirement)
>
> Copyright (c) 2026 yang0 (https://github.com/yang0/handraw-style)
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> 1. The above copyright notice and this permission notice shall be included in all
>    copies or substantial portions of the Software.
>
> 2. Attribution Requirement: You are free to use, modify, distribute, and
>    commercialize this project. If this project is helpful to you, or if your
>    project/product is based on or derived from secondary development of this work,
>    you must retain and clearly display credit to the original author (yang0) and
>    the original repository link (https://github.com/yang0/handraw-style) in your
>    documentation, repository, product description, or user interface.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

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
