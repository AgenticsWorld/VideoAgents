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
