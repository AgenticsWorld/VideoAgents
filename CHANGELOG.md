# Changelog

All notable public changes to VideoAgents are documented here.

## [Unreleased]

## [1.0.2] - 2026-07-22

### Added

- Declarative agent plugin system: a `plugins/<name>/` directory (with `plugin.json` manifest, `SOUL.md` agents, optional standalone `workflows/*.yaml` DAGs) is a plugin. Install by copying the directory or uploading a zip in Settings → Plugins; enable/disable per plugin. Plugin agents and DAG nodes are registered dynamically, injected into system prompts, and tracked in `runs/dag.json` alongside the built-in workflow. New `/api/plugins` endpoints (list/toggle/upload/delete) with collision and path-traversal validation; plugin authoring guide in `plugins/README.md` and WORKFLOW.md §10.
- Official plugin `derivative-fiction`: an 8-station team that writes derivative novels grounded in a project's canon bible/characters/assets — planning, outline, prose style bible, per-chapter planning/writing/line-editing, stateless prose QA, and release packaging (EPUB/TXT/platform chapter bundles) via a standalone `novel.yaml` DAG with three sign-off gates. New canon settings flow through `bible-delta` (canon stays read-only).
- Official plugin `fusion-fiction`: a 5+1-station team that fuses two books in branch-project mode — book A supplies story and character souls, book B (a classic, reconstructible from knowledge without source text) supplies the world and character appearances. Core artifacts: dimension allocation matrix, character mapping table, and concept conversion dictionary (with a modern-vocabulary blacklist); five sign-off gates, results written directly into the cloned branch's canon `bible/` and `story/`.

### Fixed

- Server-side self-initiated conversations (settings-change notices, project kickoff dispatch, watchdog wake-ups) now follow the top-bar global engine/model instead of silently falling back to `claude`: the browser preference is synced to a server-side copy via `/api/globalmodel`, and all dispatch paths resolve the model through a single `agent_effective_model` chain.
- `vc.py show` now emits the stored blob byte-exact (the internal git wrapper stripped the trailing newline, making exported files one byte short and causing false sha256 mismatches).

### Changed

- `tests/` removed from the distributed repository; the test suite runs locally only (some integrity tests depend on local private data).

## [1.0.1] - 2026-07-22

### Added

- `run.sh` one-command launcher, equivalent to `make run-auto` (no make required); auto-detects `python`/`python3` and passes arguments through to the server.
- Hard validation `grpsh_id_3digits` on media generation: output paths whose grp/sh numbers are not 3-digit zero-padded (e.g. `grp01` vs `grp001`) are rejected with the corrected name, preventing filename drift that broke previews and machine checks.

### Changed

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
