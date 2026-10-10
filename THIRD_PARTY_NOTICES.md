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

On Windows x64 the desktop client can download FFmpeg from the VideoAgents package
mirrors when none is installed. The package is the unmodified
`ffmpeg-9.0.2-essentials_build.zip` static build from gyan.dev (FFmpeg 9.0.2, GPL v3),
pinned with its SHA-256 in `apps/desktop/ffmpeg-artifact.json`. Its `LICENSE` and
`README.txt` (build configuration and the source revision,
https://github.com/FFmpeg/FFmpeg/commit/946fcce07b) are installed next to the
binaries; `ffplay.exe` is removed after extraction. FFmpeg runs as a separate program
and is not linked into VideoAgents. Upstream: https://www.gyan.dev/ffmpeg/builds/ and
https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2.
