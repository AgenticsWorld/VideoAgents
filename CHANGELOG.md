# Changelog

All notable public changes to VideoAgents are documented here.

## [Unreleased]

### Added

- Pi execution engine: run Agents through the local `pi` CLI with JSON event streaming, session resume, per-run model selection, and dynamic language-model menus populated from the models available to the current Pi login.

### Changed

- Standardized local development and CI/desktop packaging on Node.js 24.19.0 through `.nvmrc`; GitHub Actions now reads the same version file instead of independently pinning Node 22.

### Fixed

- Pi streaming replies no longer render one token per vertical line while a run is active; consecutive text deltas are coalesced into one live text block while preserving tool-event ordering.
- Long engine/model identifiers in the run panel now truncate with a tooltip instead of pushing durations and per-run stop buttons under the scrollbar.
- `make run` now exits cleanly on the first Ctrl+C even while the browser's SSE stream is open; shutdown also reaps the managed API, draw sidecar, active Agent process groups, messaging relays, and keep-awake helper, with a bounded timeout fallback.

## [1.0.12] - 2026-08-05

### Added

- audio-to-video plugin: builds a picture track for narration audio (podcasts, audiobooks, storytelling) with the timeline locked to the audio — ingest/transcript alignment, era research, timeline planning and visual scripting stations plus an A/V-sync QA gate. Ships as a standalone installable plugin package (moved out of the built-in set) with a host-toolchain preflight, and suspends the main DAG's audiovisual branch on start via the `main_dag_on_start.skip` mechanism from v1.0.10.
- `scripts/package_plugin.py` (`make plugin-package`): declarative plugin packaging driven by the plugin's own manifest.

### Changed

- WebUI: the active image/video generation channel is now chosen on the 🎨 generation-models page — the active tab is the active channel, applied on save — matching how music/TTS/file hosting already work; the two top-bar dropdowns were removed. Provider hint texts were trimmed, with recharge/console links added for OpenRouter and Volcano Ark. All 11 language dictionaries updated.
- Resource-usage panel: when Claude usage checking is disabled, the claudecode row is hidden entirely instead of showing an empty "not enabled" bar; enabling it in settings refreshes the panel immediately.
- genmedia: Volcano Ark image generation now requests URL responses and downloads the image, avoiding oversized base64 response bodies.
- Agent workflow spec: the character-concept station now delivers a single character sheet per character (2560x1440 five-panel layout — full-body front/side/back plus two close-ups) as the one anchor file consumed downstream, generated one image at a time with defect-driven targeted re-rolls instead of multi-candidate batches; user review feedback is the only driver for revisions.

### Fixed

- Console input drafts are no longer lost when switching pages or refreshing, and a failed send returns the typed content to the box.
- Desktop: upgrading the Python environment no longer hangs.
- CI: the WebUI legacy-API-path check no longer misfires on external `https://` links inside hint texts (an ElevenLabs link had broken the main-branch build); the link itself now points to the correct ElevenLabs API-keys page.

## [1.0.11] - 2026-08-04

### Added

- deepagents engine session memory: the runner gained `--checkpoint-db` / `--thread-id`, persisting conversation history (including tool results) across processes via LangGraph SqliteSaver — aligning deepagents with the claude/codex session-resume semantics. Stateful agents now keep context across dispatches; a fuse resets sessions whose history exceeds 32KB (OpenAI-compatible endpoints resend the full history each turn with no vendor prompt cache). `make install-deepagents` now also installs `langgraph-checkpoint-sqlite`.
- Desktop forced updates: the runtime index can declare a `minimumVersion`; app builds older than it are required to update before continuing (the requirement is cached so it also applies offline).
- Desktop app upgrades now carry the Python runtime along: the build embeds the requirements-lock hash, and when the installed runtime's hash no longer matches, the app checks the runtime index once and auto-downloads the newer runtime package — old users no longer keep a stale cached runtime that's missing newly added dependencies. Any check/download failure falls back to the existing runtime without blocking startup.
- Storyboard preview: group cards now also display reference images written into the group prompt by the pipeline or agents (concept art, chained tail frames…), marked 📎 and counted in the navigation badge — previously only keyframe anchor packs and user-added refs were shown. User-added refs keep their 🖼 marker and remain the only removable kind; refs pointing into the group's own anchor pack and byte-identical copies of anchor-pack images are skipped to avoid duplicates.

### Changed

- New-project wizard defaults trimmed to a minimal production config: no publish platform preselected (at least one must still be chosen), output language follows the current UI language, quality reviewers default to 0 (skip review), and intro/outro/next-episode-preview are unchecked.
- Aspect-ratio machine checks relaxed to directional: 16:9 / 9:16 describe the general format, and engine-native near-ratio output (e.g. 864x496 draft renders) passes instead of failing a strict width×9 = height×16 equality — no more per-group user waivers for engine rounding. The ≥3,686,400-pixel floor for video reference images is unchanged.
- Run panel caps the agent list at the 100 most recent entries.
- Agent workflow spec: production-required fields may no longer ship as UNKNOWN placeholders. Design-layer stations (worldbuilding, characters, scenes, art) that find no basis in the source text or upstream Bible must invent a self-consistent value, mark it `inferred: true` with a design rationale, and continue; a new `no_unknown_placeholder` machine check bounces UNKNOWN/TBD placeholders. Fact-extraction stations still mark UNKNOWN (it stays the downstream fill-me signal), and filling per this rule no longer costs faithfulness points in scoring.
- Agent workflow spec: context-package scheduling tightened again — inline work orders are exempted from the `context.md` run-record requirement in the machine-readable yaml itself, the full-package whitelist narrowed (creative work alone no longer justifies a full package when input paths can be listed explicitly), and duplicate packaging is banned (unexpired packages are reused; fan-out batches share a base package plus per-instance increments).

### Fixed

- Windows: preview pages no longer scramble assets in subfolders — all relative paths in asset/audio/ref URLs are POSIX-normalized (backslash paths broke subfolder grouping in character/scene/prop previews and image loading; hand-drawn/uploaded refs persisted into group prompts are now written with forward slashes at the source).
- Windows: Feishu SDK modules are imported via shortened paths, fixing failures from the 260-character path limit.
- Prop preview: asset documents whose array key is `entries` instead of `props` now render their cards.
- Version page: the suggested name for a copied project is now date-only (no time-of-day suffix).

### Added

- Plugin flows can suspend the main DAG's audiovisual branch when they start (`main_dag_on_start.skip`): the derivative-novel plugin declares voiceprint/lighting design (p3-voice, p3-lighting), every p4–p11 stage node and gates g4–g10, and starting a novel marks them `skipped` (with a recorded reason) so scheduling doesn't wander into audiovisual nodes irrelevant to text-only work. The skip only applies when none of the listed nodes have started; a project with any node already dispatched or running is treated as active video production and left untouched. Skipped is suspension, not abandonment — nodes restore to pending whenever video production resumes, and gates treat skipped dependencies as satisfied.

### Changed

- Feishu setup page: the unbound hint is now a single sentence linking to the Feishu Open Platform to create a "Feishu agent application" — the new app template ships with bot capability, long-connection event subscription, callback subscription and message permissions, so the old four-step manual walkthrough was removed. All 11 language dictionaries updated.

### Fixed

- The Feishu SDK (`lark-oapi`) is now a core dependency and is bundled into the desktop runtime package — binding Feishu no longer fails with "Feishu SDK not installed" on standard installs and desktop builds.
- Dispatched tasks with no explicit model and no agent-level override now inherit the top bar's global model choice (when its engine matches the task's engine). Previously such tasks silently ran on the engine CLI's default model — the top-bar selection never took effect — and the run panel badge showed only the engine name without a model.

## [1.0.9] - 2026-08-01

### Added

- Phone messaging channels: the Advanced menu's "ClawBot WeChat" page became "Phone Messages" with three channels — ClawBot WeChat (flow unchanged), Feishu/Lark and WhatsApp. Feishu binds with app credentials and receives messages over the official SDK's WebSocket long connection (no public callback URL needed); WhatsApp connects your own account as a linked device (Baileys multi-device), using self-chat as the command channel — messages from other people are never read or forwarded. Inbound texts go to the chief producer; its replies and system messages are pushed back to every bound channel, and user commands arriving from one channel are mirrored to the others with a source label so all endpoints see the same conversation.
- Feishu interactive sign-off cards: confirmations and human-gate sign-offs raised by agents (`dispatch.py --confirm` / `--sign`) are now also pushed to the bound Feishu account as interactive cards with buttons. Tapping a button answers the same approval as the web popup (either endpoint wins, the other closes); cards are finalized after answering — buttons removed and replaced by the chosen answer — and time-outs / stale approvals render as expired cards. Requires enabling the long-connection callback subscription in the Feishu developer console (setup page updated, all 11 language dictionaries +1 entry).

### Changed

- Agent workflow spec: Context Packages are now two-tier. The context agent assembles a full package only for three kinds of work orders — retries (`attempt > 1`), tasks that need Bible slicing, and tasks that need defect-history aggregation; every other order inlines its context (input paths + hard constraints) in the work order itself and skips the context agent entirely. Previously the spec mandated a context-agent call before every dispatch (~4 minutes per call measured; 395 calls for 310 work orders on one project). The four-file run-record check exempts `context.md` for inline orders, and Bible reads no longer go through memory-bible arbitration (writes and conflict reports only).

## [1.0.8] - 2026-07-31

### Added

- "Smart Kimi" model policy (`smart_kimi`): routes each agent to a Kimi model by task complexity — creative-core agents to K3, the rest to K2.7 Coding.
- Hand-drawn reference images, revamped: phone-submitted sketches with annotations now generate a finished image through the configured image engine before entering the group's reference images, instead of feeding raw line art directly into refs and the prompt; the entry point moved into the "Add reference image" dialog.
- "Add reference image" dialog: local upload (magic-number validated png/jpg/webp/gif, ASCII-sanitized filenames) alongside asset-library selection.
- Reference image management: the dialog shows the group's current refs as a thumbnail strip with [Image N] badges and click-to-zoom; user-added images can be removed with a confirmation prompt, and group cards show the refs too.
- Version management page: "Copy project" section — duplicate the whole current project directory as a new project with name validation.
- Workflow preview: "✏️ Edit" button next to the critical path — prefills a chief-producer message for adjusting the entire workflow (`runs/dag.json`).
- Concept art archiving convention: superseded candidates, intermediate attempts and test images move to a `candidates/` subdirectory; the main directory keeps only the finalized latest versions.
- Production packages are published to Aliyun OSS.

### Changed

- Video prompt spec aligned with the official Seedance 2.0 prompt guide: per-character subject definitions up front with `[Image N]` anchors (bare names afterwards), one camera move per shot (new machine check `one_move_per_shot`), the full official twin-prevention constraint, `[Audio N]` voice anchors with mandatory timbre phrases, `<>`-wrapped sound cues, quantified action-writing rules, and reference-image ordering guidance (character images first, single-view preferred).
- `line-editor` became a stateless fan-out station (one order per chapter, concurrent); `prose-writer` stays stateful and serial.
- Storyboard preview: removed the "(no keyframe)" placeholder — the media column is simply omitted when a group has no keyframes.

### Fixed

- Phone QR access to the hand-drawn canvas: LAN IP detection no longer returns proxy virtual-NIC addresses (Surge/Clash TUN fake-IP range filtered, private-subnet probing first, `VIDEOAGENTS_LAN_IP` override); a new LAN sidecar listener serves only the draw page, its token-gated session API and static assets while the console stays loopback-only; the session URL under the QR code is now a clickable explicit `http://` link so browsers don't auto-upgrade a hand-typed address to unreachable HTTPS.

## [1.0.7] - 2026-07-29

### Added

- Storyboard preview: per-group "🖼 Image" button — browse concept-library assets by character/scene/prop category and pick an image to add to the group's reference images (refs). Shares the 9-image cap with hand-drawn sketches (enforced on both frontend and backend); images already in refs are greyed out to prevent duplicates. New backend `POST /storyboard/refs` (`core.api_grpref_add`) validates the `assets/concepts/` prefix and guards against path traversal. All 11 language dictionaries updated.
- Storyboard preview: per-generation-group "📄 Prompt" button — popup showing the group's video prompt and byte usage.
- Run panel: inline "⏹" button on each run entry to stop that single agent (queued or running) without affecting other runs; new backend `POST /runs/{run_id}/cancel`.

### Changed

- Fresh-install provider defaults: image/video/TTS now default to Volcano Engine and music generation to ElevenLabs (`DEFAULT_GENCONFIG` provider defaults plus the `models.html` initial fallback). Existing installs' `genconfig.json` is unaffected.
- Generation model settings: the active channel for music generation and TTS voice models now follows the selected tab (✅ marker), matching the file-hosting storage channel UX; the per-tab check circles were removed. All 11 language dictionaries updated.
- Video prompt body language now follows the user's UI language instead of defaulting to English.
- WebUI link color switched to high-contrast orange (`--link: #fb923c`) with a new global `a` fallback rule — unstyled links no longer fall back to the browser's default dark blue, which was hard to read on dark backgrounds; explicit link rules (e.g. `.hint a`) updated to match, navigation/icons/menus untouched.

### Fixed

- WeChat binding: fetching the bind QR code now retries up to 2 times on transient network/SSL EOF errors (proxy hiccups causing peer disconnects) before reporting failure.

## [1.0.6] - 2026-07-28

### Added

- "Concurrency" dialog under Settings → Advanced (slider + number box, 1-8, default 5): per-agent concurrency quota for stateless fan-out agents. Stateful creative agents always stay serial to protect session continuity, and every run still passes the global 8-process gate. Persisted in `state.json` via the new `GET/POST /api/v1/config/concurrency`; takes effect immediately for subsequently queued tickets.
- "Agent Memory" toggle under Settings → Advanced (default on): when turned off, every agent — including the Producer — starts a fresh session per run, and cross-ticket information travels only through on-disk artifacts; session ids keep being recorded while off, so re-enabling resumes from the most recent session. New `GET/POST /api/v1/config/agent-memory`.

### Changed

- Same-agent execution gate upgraded from a boolean mutex to per-agent semaphores, and the stateless fan-out set expanded: the `05-scenes/`, `03-characters/` and `06-art/` prefixes plus `01-story/novel-parser` now start a fresh session per ticket and may run in parallel up to the concurrency quota — per-scene/per-character fan-outs (e.g. 70 `p3-environment` tickets on a large project) no longer serialize behind one agent lock.
- Refreshed desktop app icon.

### Fixed

- Desktop auto-update no longer gets stuck mid-update (download/install flow hardened).

## [1.0.5] - 2026-07-28

### Added

- Quality-judge slider in review settings (0-100, default 60, listed first): the value is the evaluation acceptance threshold below which verdicts become "must fix as instructed"; 0 disables evaluation tickets entirely (no scoring for the whole run). Synced to the new-project wizard; prompt injection covers the three states and names `own_review` explicitly.
- H1A "characters & assets confirmation" human sign-off gate added to the default workflow's g3 gate, between H1 and H2; WORKFLOW.md hard rules, flowchart, gate notes and H-point summary table updated.
- The console footer now shows the serving version (read from the API health `__version__`), so release bumps surface automatically in the UI.
- Character/scene/prop preview galleries collect subdirectories (e.g. `candidates`) into folder cards opened on click, keeping drafts out of the final-image grid.

### Changed

- claude engine wired to `opus-5`: the top-bar default is the "opus (latest)" alias with `opus-5` as the second choice; the smart-assignment high tier resolves to it (`smart_claude` → opus-5, `smart_codex` → gpt-5.6-sol).
- Smart-assignment tiers rebalanced: `novel-parser` promoted to the high-complexity tier (back to the 01-story category default); the whole 02-worldbuilding and 04-creatures categories demoted to the low tier, with `world` kept high as the creative core.
- Run panel cards no longer embed an activity-log expander; errors are shown inline in red instead.

### Fixed

- deepagents engine: interpreter auto-discovery, and local endpoints connect directly instead of going through the system proxy.

## [1.0.4] - 2026-07-24

### Changed

- Group anchor packs are now reuse-first: concept-library images (character three-views, scene concepts, prop scale refs) are reused directly as Seedance 2.0 reference images, and groups fully covered by the library generate zero new images (`generation_channel: reuse-only`). Per-group composed opening anchor frames (`anchor_opening`) are no longer produced by default — the multi-reference video mode is mutually exclusive with first-frame input, so composed openings never entered video requests (evidence: 86 such frames across one episode, none used); they remain allowed only as an orchestrator-approved first-frame/split fallback. Gap-fill anchor generation (missing costume state, expression, prop close-up) must record a `gap_reason`, enforced by the new `reuse_first_ok` machine check.
- Reused anchors skip character-consistency correction and visual-QA composition scoring (their source concept images were already reviewed at library intake); `reuse-only` groups pass straight through, saving generation quota and QA time.
- The prompt station emits anchor-image prompts only for genuine concept-library gaps instead of routinely scripting an opening frame per group.
- `tests/` removed from version control (local-only from now on).

### Fixed

- UI language preference is now saved globally in `state.json` (dual-written with genconfig for backward compatibility, read preferring state; the ui-prefs API returns it and the browser restores from the server before auto-detection) — fixes the desktop app reverting to browser-detected language after each randomized-port restart.

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
