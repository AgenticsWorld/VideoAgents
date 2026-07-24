# Changelog

All notable public changes to VideoAgents are documented here.

## [Unreleased]

## [1.0.3] - 2026-07-24

### Added

- WeChat ClawBot integration: bind an account by scanning a QR code on the new `clawbot.html` page and exchange messages with the chief producer directly from WeChat; channel implementation in `services/runtime/wechat.py`, speaker credentials rebound to the TOS file-hosting channel AK/SK.
- `blocking_bound` machine check: each scene's blocking (staging) fragment is injected verbatim into generation prompts and validated by `code/blocking_bound_check.py`, preventing character spatial drift within a scene.
- `costume_bound` deterministic injection: the continuity costume state is copied verbatim into image/video prompts, preventing wardrobe drift across shots.
- Identity lock extended to all groups: prevents three-view character reference images from duplicating the character as a second person in frame; when the cast changes across a group boundary, the seam shot must switch composition so new characters cannot appear mid-shot out of nowhere.
- H3B visual-generation sign-off gate in the default workflow (g7): each episode's group clips are reviewed before entering editing.
- System sleep prevention on macOS/Windows while auto-run is active.
- Version management page gains an enable toggle (default off); when disabled, no version-management tickets are dispatched.
- Settings menu reorganized: new "Advanced" submenu, "Agent Model" renamed to "Model Strategy"; the Web UI now displays the actual serving port.

### Changed

- p0 novel parsing switched to chapter-level map+merge: batched scanning, per-chapter fan-out parsing, and mechanical merging, so large novels no longer bottleneck on a single parse pass.
- fusion-fiction plugin simplified: branch-project cloning and version management removed (users manage versions themselves); the soul reconciliation baseline is stored as a snapshot.
- Plugins are now disabled by default after installation and must be enabled manually on the Plugins page.
- Review intensity defaults to 0 with no review during the draft phase; the per-session fuse is lowered and the stateless station pool expanded.
- Racing (parallel competitive generation) now runs only on explicit user instruction, never automatically.
- Volcano video generation: input image minimum pixel hard limit (≥3,686,400 px per image, e.g. 2560x1440 for 16:9) consolidated into prompts.
- Desktop/runtime hardening: local API calls bypass system and environment proxies; installer names include the version; macOS notarization; Intel macOS release builds dropped; Windows runtime junction packaging and Node 24 artifact workflows fixed.

## [1.0.2] - 2026-07-22

### Added

- Declarative agent plugin system: a plugin contains a `plugin.json` manifest, `SOUL.md` agents, and optional standalone `workflows/*.yaml` DAGs. Bundled plugins live under `plugins/`; uploaded plugins use the writable runtime data directory. Plugin agents and DAG nodes are registered dynamically, injected into system prompts, and tracked in `runs/dag.json` alongside the built-in workflow. The typed `/api/v1/plugins` resource supports list/install/update/delete with collision and path-traversal validation; plugin authoring guide in `plugins/README.md` and WORKFLOW.md §10.
- Official plugin `derivative-fiction`: an 8-station team that writes derivative novels grounded in a project's canon bible/characters/assets — planning, outline, prose style bible, per-chapter planning/writing/line-editing, stateless prose QA, and release packaging (EPUB/TXT/platform chapter bundles) via a standalone `novel.yaml` DAG with three sign-off gates. New canon settings flow through `bible-delta` (canon stays read-only).
- Official plugin `fusion-fiction`: a 5+1-station team that fuses two books in branch-project mode — book A supplies story and character souls, book B (a classic, reconstructible from knowledge without source text) supplies the world and character appearances. Core artifacts: dimension allocation matrix, character mapping table, and concept conversion dictionary (with a modern-vocabulary blacklist); five sign-off gates, results written directly into the cloned branch's canon `bible/` and `story/`.

### Fixed

- Server-side self-initiated conversations (settings-change notices, project kickoff dispatch, watchdog wake-ups) now follow the top-bar global engine/model instead of silently falling back to `claude`: the browser preference is synced through `/api/v1/config/global-model`, and all dispatch paths resolve the model through a single `agent_effective_model` chain.
- `vc.py show` now emits the stored blob byte-exact (the internal git wrapper stripped the trailing newline, making exported files one byte short and causing false sha256 mismatches).

### Changed

- Product and architecture regression tests remain versioned in `tests/`; checks that depend on private local data stay outside the public suite.

## [1.0.1] - 2026-07-22

### Added

- `run.sh` one-command launcher, equivalent to `make run-auto` (no make required); auto-detects `python`/`python3` and passes arguments through to the server.
- Hard validation `grpsh_id_3digits` on media generation: output paths whose grp/sh numbers are not 3-digit zero-padded (e.g. `grp01` vs `grp001`) are rejected with the corrected name, preventing filename drift that broke previews and machine checks.

### Changed

- Preserved the original HTML/CSS WebUI under `apps/web/static`, migrated its JavaScript directly to the typed `/api/v1` FastAPI surface, and retained no legacy API adapter.
- Added a Python Web gateway shared by URL and Electron clients for static pages and same-origin API proxying; Electron supports local or remote Python services and consumes GitHub Release updates.
- Persisted runs, approvals, and replayable event sequences in SQLite while retaining the established creative execution core and `data/projects/` resource model.
- Migrated project asset history and version-clone operations from system Git commands to `pygit2`.
- Added GitHub Actions packaging for macOS x64/arm64 and Windows x64 clients without bundling third-party execution environments.
- Style library: all 85 live-action prompts expanded from short tags into full style descriptions (era/genre positioning, palette, lighting, film texture, contrast, composition and mood); garbled movie-name entries normalized.

### Fixed

- Preview pages now tolerantly match legacy drifted asset filenames by prefix word + numeric ID (`grp01` matches `grp001`), so existing assets stay visible while new outputs are always written with canonical names.

## [1.0.0] - 2026-07-21

### Added

- Reference images page: upload, preview, and annotate visual/character/scene/prop/music/cover references; annotations sync to `refs/NOTES.md` and are read by visual and music agents.
- Design brief split into main concept + design style, with a built-in style library picker (94 curated styles across live-action/3D/2D categories).
- Workflow preview page: `runs/dag.json` rendered as stage swimlanes with dependency lines, fan-out aggregation, gate/sign-off markers, and remaining time/cost estimates.
- Worldview preview page, plus preview enhancements across characters/scenes/props/storyboard: inline ✏️ edit-request buttons, audio players for voices/narration/music, and a collapsible directing plan block.
- TTS channels: Volcano Doubao Seed-TTS 2.0 and ElevenLabs, with voice library search/audition and one-click default voice; narrator voice now follows the active channel's default voice.
- Music generation via ElevenLabs Eleven Music with per-cue duration control.
- Publishing skills: semi-automated draft upload to YouTube, Douyin, Xiaohongshu, TikTok, and Bilibili (final publish click stays manual).
- Resource usage panel: session/weekly usage across the three engines plus OpenRouter/Volcano balances; engine CLI detection with install guidance; Kimi Code engine support.
- Idle watchdog that wakes the orchestrator when the pipeline stalls, with per-project switches and usage-threshold gating.
- Sign-off confirm dialogs that never auto-confirm, separate from ordinary retry confirms.
- Optional subtitle burn-in for final renders.
- Character gender field with hard constraints through voiceprint/casting/keyframe consistency checks; per-character voiceprint anchoring for TTS.
- ASCII-only output filename rule enforced across all 83 agents.
- UI language auto-alignment between browser and server, with agent reports following the UI language; i18n maintained across 11 languages.

### Changed

- Volcano TTS authentication migrated to the new console's single `X-Api-Key` header (legacy App ID + Access Token removed).
- Final-render subtitles standardized on `subtitles_final.srt` (intro-offset corrected); master assembly naming locked down.

### Fixed

- Final subtitle timing offset caused by intro length; watchdog engine fallback now uses the orchestrator's configured engine.
- Made the test import path explicit for pytest 9 and GitHub-hosted runners.
- Updated GitHub Actions to their Node.js 24 releases.

## [0.1.0] - 2026-07-14

### Added

- Initial open-source release of the 83-agent production workflow.
- Local Web console, media adapters, project versioning, and validation tools.
- English and Chinese documentation, security policy, contribution guide, and CI.

### Security

- Localhost-only Web binding and `acceptEdits` agent permissions by default.
- Claude OAuth usage probing changed to explicit opt-in.
