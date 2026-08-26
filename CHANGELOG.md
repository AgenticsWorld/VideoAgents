# Changelog

All notable public changes to VideoAgents are documented here.

## [Unreleased]

### Changed

- RunningHub is no longer a separate channel tab: in Settings → Generation Models (image/video/music/TTS) it is back inside the **ComfyUI** tab as a run mode (Local / Cloud (Comfy Cloud) / RunningHub China (.cn) / RunningHub International (.ai)), together with the per-site keys, cloud-workflow favorites and instance type; the per-agent model override dialog lists ComfyUI only. Stored configuration was never split, so a configuration saved with the RunningHub tab active (`provider=comfyui` + `mode=rh_*`) simply opens the ComfyUI tab with the matching RunningHub run mode selected; a per-agent `runninghub` override saved by the previous UI is treated as `comfyui` (run mode follows the global ComfyUI section). The RunningHub video skill still honours the per-agent channel override. Reverts the separate-tab change (fc786da).
- Digital human: the RunningHub tab is merged into the **ComfyUI** tab as a run mode as well, and the digital-human ComfyUI tab gains **Cloud (Comfy Cloud)** alongside Local. `digital_human.comfyui` now has the same shape as the other ComfyUI sections (`mode`, `url`, `cloud_api_key`, `workflow`, `rh_api_key_cn/ai`, `rh_workflow_id`, `rh_workflows`, `rh_instance_type`); an old `digital_human.runninghub` section is migrated on load (fields moved into `comfyui.rh_*`, `provider=runninghub` becomes `comfyui` with `mode=<site>`), both by the service and by `modules/digitalhuman.py` reading the file directly. The connection test follows the run mode (local/Comfy Cloud check the InfiniteTalk/MultiTalk nodes and the workflow file; RunningHub checks the key and balance), and the per-segment job ledger keeps recording RunningHub runs as `runninghub` so interrupted tasks resume against the same remote task.

### Added

- New execution engine **grok** (Grok Build CLI, install from https://grok.com/build; listed as `grok` in the engine menus, above deepagents). Runs `grok -p … --output-format streaming-messages-json --always-approve --max-turns … --rules <role prompt>` — the stream is the Anthropic Messages wire format, so the existing Claude event handler and token accounting are reused after normalising Grok's built-in tool names (`write`/`search_replace`/`read_file`/`run_terminal_command` → Write/Edit/Read/Bash); sessions resume via `--resume <uuid>` (a stale id falls back to a fresh session like the other engines); Thinking Effort maps to `--reasoning-effort` (no `max`, mapped to `xhigh`). The language-model menu is populated from `grok models` (`GET /api/v1/engines/grok/models`, 30 s TTL, static fallback Grok 4.6 / 4.5) and a **Grok Smart Assignment** policy (`smart_grok`: creative-core agents → `grok-4.6`, others → `grok-4.5`) is the default when the engine is selected. The binary is resolved via `GROK_BIN`, then PATH, then `~/.grok/bin` / `~/.local/bin`; the macOS desktop client adds `~/.grok/bin` to the inherited PATH. `--engine grok` is accepted by `dispatch.py` and the run/global-model APIs. All 11 language dictionaries updated.
- Settings → Advanced → **Skill Packs**: a new dialog that auto-scans every agent's `skills/<skill>/SKILL.md` (built-in agents and enabled plugin agents alike, name/description read from the frontmatter) and lists them grouped by agent with a checkbox each. Semantics are "ticked = allowed": a ticked skill's load instruction is injected into that agent's runtime prompt (for the existing conditional skills — Seedance 2.0/2.5 and H3 prompt writing, MiniMax Regenerate-2K upscale, RunningHub cloud workflow — the runtime condition still has to hold, the checkbox is only the master switch); an unticked skill is never injected, and for skills hard-referenced by SOUL.md (caption styling, audio transcription, publisher platform drafts) the prompt additionally declares them disabled for the run. Skills not covered by the built-in registry get a generic "read SKILL.md first, apply if its description matches the job" instruction when ticked. Everything is enabled by default (newly added skills are enabled automatically), stored globally in `state.json#skills_disabled`, effective for runs started after saving. New endpoints `GET/POST /api/v1/config/skills`. All 11 language dictionaries updated.

### Changed

- MiniMax H3 prompt-writing skill trigger is now engine-agnostic: H3 is an open-weight model that other channels (RunningHub, ComfyUI, …) can run, so the `h3-prompt-writing` skill is injected whenever the effective video model id / ComfyUI workflow file name / cached RunningHub workflow JSON contains both `minimax` and `h3` (case-insensitive, any separator or order — e.g. `minimax/h3-preview`, `MiniMax: H3`, `MiniMaxH3ReferenceToVideo`), instead of requiring the literal `minimax-h3` token or the exact H3 node class name. The Skill Packs condition text (all 11 language dictionaries) and the prompt agent's SOUL wording are updated accordingly.
- Scene layout packs: the 3x3 multi-angle sheet (`grid_9views.png`) now follows explicit camera-diversity rules written into `layout.json#views` before generation. Each view carries a new `angle` field (`eye` | `low` | `high_oblique`); no tile may be a vertical top-down view (that is the job of `layout_top.png`, and the plan image was being copied into tiles), `(camera_from, looking_at)` pairs must be pairwise distinct, at most 3 tiles may look at the same landmark, at most 4 `wide` tiles, at least 2 `close`/`detail` tiles, at least 1 `low` tile, and at most 3 tiles may share the same ±30° camera bearing computed from landmark coordinates (so linear spaces such as alleys no longer yield seven near-identical corridor shots). The rules are self-checked by the environment-concept agent and documented in its SOUL, the grid template JSON (`view_rules`) and WORKFLOW.md; no new script check. Existing projects are unaffected until a scene is regenerated.
- Scene top-down layout image (`layout_top.png`) prompt guidance: the environment-concept SOUL now prescribes true-nadir orthographic wording (no `bird's-eye`), describing doors and windows as gaps in the wall lines rather than by their elevation features (door leaves, bars, lattices — which a model can only satisfy by laying them flat on the floor), explicitly asserting the near (bottom) wall, and adding matching negative terms. Landmark coordinates for openings are calibrated to the wall-line gap, never to a door drawn on the floor.

## [1.0.23] - 2026-08-24

### Changed

- The desktop download-progress window (app updates and the first-launch Python runtime download) now shows a live stats line under the progress bar — downloaded / total size, percentage, and a smoothed download speed (e.g. `123.4 MB / 274.0 MB · 45% · 3.2 MB/s`). Also fixes the progress bar itself, which previously rendered as full from the start (`<progress>` was fed 0–100 values against its default max of 1); DOM updates are now throttled to at most once per 200 ms instead of once per network chunk.

## [1.0.22] - 2026-08-24

### Added

- Virtual Portrait Library "Fully automatic management" option (checkbox to the left of Save Settings on the /avatars page, off by default; stored as `avatar_assets.auto_manage`). When the library is enabled, the option is on, and the effective video channel for the video-generation agent is Volcano Engine, every `08-video-gen/video-generation` run is prepared automatically before the agent starts: on the first run for a given (project, episode) the library is cleared (page-by-page ListAssets + DeleteAsset — Ark caps the asset count), then every character concept image referenced by that episode's group prompt refs (`assets/concepts/characters/` paths in `assets/prompts/epNN/grp*.json`) is registered, and the run waits up to 10 minutes for the asynchronous review to reach Active (genmedia then submits those refs as `asset://` URIs by sha256 ledger match). Subsequent runs for the same episode are idempotent (no re-clear; already-registered images are skipped); failures only warn in the run's progress line and never block generation. Episode is parsed from the work order's `epNN` mention; concurrent runs are serialized. All 11 language dictionaries updated.

- Episode duration can now be set to "auto from script": both the new-project wizard (duration step) and the per-project Duration Settings dialog gain an "Auto from script" checkbox (unchecked by default; when checked the minutes input is disabled). Stored as `duration.episode_minutes: "auto"` in the project's settings.json — no fixed per-episode budget is injected into agent prompts; instead episode-planner decides the episode split and per-episode length from the script's structure, and pacing/edit follow the actual budgets written in episode_plan. All 11 language dictionaries updated.

- The group video-prompt popup on the storyboard preview page is now editable: an Edit button switches the prompt text to an editable box, turns into Save, and writes the edited `video_prompt` straight back to the group's prompt file via the new `POST /api/v1/storyboard/prompt` endpoint (a note is appended to the file recording the manual edit; regenerating the group still rewrites the prompt from the design files). The previous "read-only; use 📝 Notes" hint is gone.

### Changed

- Smart Assignment is now built into the top-bar language-model dropdown and is the default: for every engine except deepagents the first dropdown item is "Smart Assignment", which picks a model per agent based on task complexity. The pi engine gains its own smart policy (high tier → openai-codex/gpt-5.6-sol, low tier → gpt-5.6-terra) and the claude high tier now uses the latest-Opus alias. Switching the engine or picking a model syncs all agents at once and clears per-agent overrides; the old Settings → Model Strategy submenu is removed. Existing installs are migrated once at startup to the smart policy matching their current engine. All 11 language dictionaries and both READMEs updated.
- Resource-usage monitoring now follows the selected engine automatically: engines manually enabled in the Resource Usage settings always show, and in addition the usage check for the engine currently selected in the top bar turns on by itself (pi counts toward Codex, since pi typically runs on a ChatGPT/Codex subscription; deepagents has no usage source). The settings dialog keeps showing only the manual switches, so saving never freezes an automatic state into a manual one. The manual Codex switch also no longer defaults to on for fresh installs — selecting codex/pi in the top bar is what enables it now, so users who never touch Codex don't get their `~/.codex/sessions` directory scanned.
- Kimi usage can now be read without pasting an API key: when the key field in Resource Usage settings is empty, the probe reads the local kimi CLI login credentials (new and old data directories), falling back to the `KIMI_CODE_API_KEY`/`KIMI_API_KEY`/`MOONSHOT_API_KEY` environment variables. Expired tokens are skipped and never refreshed (re-run `kimi login` to restore the panel), matching the claude probe's no-refresh policy.
- Group anchor packs are now reference-based for reused images: image-generation no longer copies concept-library images (character sheets, scene grid-9 sheets, prop scale refs) into `assets/keyframes/epNN/<grp>/` — a reused anchor is registered in the pack's `meta.json` only (`file: null`, `source: reuse:<path>`, plus a new `source_sha256` provenance fingerprint recording the exact bytes referenced at pack time), and group prompt refs / video requests keep pointing at the original concept-library paths (an audit of the offer project showed all 36 groups' submitted Seedance refs already bypassed the copies entirely). Only genuinely new files — gap-fill generations, sketch renders, approved opening frames, upscaled to-spec variants — are written into the pack. This avoids mass file duplication, lets a concept-image update propagate to every group, and keeps the avatar-asset ledger (keyed by content sha256) free of diverging-copy re-registrations. Machine checks adapt: resolution/aspect dereference the `source` path for reference-style anchors, `prop_ref_attached` means "registered in meta and target file exists", and the per-position `[Image N]` mapping is guarded by the now-canonical `imageref_order_bound` check (meta `anchors[].image_index` vs prompt refs). Legacy copy-style packs stay valid (checks branch on `file`); the storyboard preview needs no change — it already falls back to showing the prompt's concept-image refs when a pack contains no images.
- The rendered blocking map (`render_blocking_map.py`) now supports CJK text on the image: the font loader prefers system CJK fonts (PingFang/STHeiti/Hiragino on macOS, Noto Sans CJK on Linux, Microsoft YaHei/SimHei on Windows), falling back to the previous Latin fonts with a WARN when legend text contains CJK glyphs the fallback cannot draw. The legend also moved: it is now a solid black strip appended **below** the layout image (the canvas grows in height) instead of a semi-transparent box overlaid on the top-left corner, so it no longer occludes any part of the map. It shows each character's name/label next to its marker letter — `A = 章墨 (CHAR-0001) [moves]` instead of the old ASCII-stripped id-only line — plus the character's `route_en` movement sentence, wrapped to fit (word-wrap for Latin text, per-character wrap for CJK). Chinese labels and route sentences from Chinese-UI projects therefore render correctly on the map. The `Map markers` sentence and verbatim `route_en` injection in the video prompt are unchanged and still machine-checked by `layout_map_bound_check.py` (image and prompt text double-anchor the mapping). Existing projects keep their current maps until `render_blocking_map.py` is re-run.
- Default shot duration range is now 2–8 seconds (minimum lowered from 4s): new-project wizard defaults, Duration Settings placeholders, the server-side default config, and the WORKFLOW.md note all updated. Existing projects with saved settings are unaffected.
- The group video-prompt popup no longer shows a byte-limit warning ("limit 5000 bytes (model)…" with the red/yellow over-limit coloring) — the limit was only a reference value and differs per model; the popup now just shows bytes used and character count.
- Prompt language follow-up (second revision of the 2026-08-24 rule): (1) the blocking-map chain no longer requires English — `route_en`/`offset_en`, `layout.json` landmark `name_en`/`desc_en` and landmark words inside fragments now follow the UI language too (field names keep the `_en` suffix; the rendered blocking map's legend now also renders CJK — see the entry above); the `route_en` pure-ASCII machine check in `render_blocking_map.py` is dropped (replaced by non-empty + length ≤40 English words or ≤60 chars). (2) Image-generation prompts (concept/anchor/reference images via `genmedia image`) also follow the UI language: the style section is taken verbatim from `style_fragment_ui` (falling back to the English `style_fragment_en` on legacy projects), while negative prompt lists stay English (`negative_prompt_en` — machine checks match English substrings). Verbatim-injection discipline and the legacy-English-project rule are unchanged.
- Upstream video-prompt fragments now follow the UI language (previously English-only): the injected style anchor string (new `style.json` field `style_fragment_ui`; the English `style_fragment_en`/`negative_prompt_en` remain for the image pipeline), blocking `space_fragment_en`, lighting `prompt_fragment_en`, costume `visual_en`, prop `scale.prompt_token`, and SFX/ambience cues are produced in the interface language for new artifacts (field names keep their historical `_en` suffix). Verbatim-injection discipline is unchanged — existing English fragments are still injected as-is and switching an existing project's language requires regenerating the fragments upstream as a set. Exceptions stay English: structural anchors (`Overall visual style:`/`Shot N:`/`[Image N]`…), fixed constraint sentences (Identity lock, Spatial layout declaration, Global constraints), and dialogue (the blocking-map chain was initially kept English too — lifted the same day by the second revision above).
- Upgrading from v1.0.20 or earlier now resets the agent conversation-memory quota to the 16 KB default on first startup, regardless of the old on/off switch — installs that lacked the memory slider previously mapped a disabled switch to 0 KB. Installs that already set the slider (v1.0.21+) are unaffected.

## [1.0.21] - 2026-08-21

### Added

- `/clear` chat command: sending `/clear` to any agent clears its stored session records for the current project across all engines, so the next message starts a completely fresh session (engine-side history files stay on disk — only the resume linkage is removed). Useful when a bloated session makes resume requests too large for the network, or to shed stale context. If the agent still has queued/running tasks the reply warns that their sessions will be re-recorded on finish.
- Signature gates now trigger the same cleanup automatically: when the user signs off a phase gate, the orchestrator's session records are cleared before it is woken (a run still in progress is flagged and cleaned when it ends), so the post-gate phase starts on a fresh session — project state is always file-based, and the Producer's conversation no longer grows without bound as the project advances.
- Storyboard preview reference labels are now clickable: 📎/🖼 labels that reference character/scene/prop concept images link straight to the matching preview page, which deep-links via `?id=` and auto-selects the asset (selection, detail panel and list scroll into place). All 11 language dictionaries updated.

### Changed

- Agent conversation memory is now a size slider instead of an on/off switch (Settings → Advanced → Agent Advanced Settings): 0–256 KB, default 16 KB. A stateful agent resumes its previous session until the chat history exceeds the quota, then automatically starts a fresh one — resume re-sends the full history every turn, so larger values are slower and costlier (a single codex run once accumulated 14M input tokens). 0 disables memory entirely. The old boolean `agent_memory` setting migrates automatically, and the deepagents engine keeps its stricter 32 KB cap.
- Default music generation channel switched from Eleven Music to MiniMax.
- `make run-auto` now performs a clean backend restart: processes still listening on the API port are identified, verified to actually be the VideoAgents API service (never an unrelated process that happens to hold the port), and terminated before the new instance starts.
- The model-strategy label "DeepSeek Smart Assignment" is renamed "OpenCode Smart Assignment" to match the channel it runs on.

### Fixed

- Storyboard blocking-map rendering: the legend box is now composited as a separate translucent layer, so character markers that fall inside the legend area show through instead of being blanked out by the legend background.
- `genmedia.py` info/dry-run output for the `comfyui` provider now always states the mode — RunningHub (`rh_cn`/`rh_ai` + workflow id + instance type), Comfy Cloud, or local (+ URL). Previously a RunningHub configuration printed the same output as an unconfigured local ComfyUI, and agents following the "don't run without a configured channel" red line wrongly refused to submit.
- Session resume for lazily-persisted engines (pi, opencode): runs that were stopped or failed before producing any assistant output no longer store a session id — resuming such never-written "ghost" sessions always failed with "No session found" — and that message is now also recognized as an invalid-session error that transparently falls back to a fresh session.

## [1.0.20] - 2026-08-20

### Added

- Network Check panel (Settings → Advanced → 网络检测) for diagnosing whether a VPN/proxy actually covers the command-line environment: many VPNs only take over the browser's system proxy while curl/git/pip in the terminal still connect directly, which silently breaks CLI-based agents. A deliberately minimal panel: the current egress IP + geolocation (ip-api.com with an ipinfo.io fallback), a single connectivity column over a fixed site list — baidu.com as a mainland-China baseline (to tell "offline" from "blocked"), google.com / www.youtube.com / openai.com, and, as a real-usability check, the Claude Code install script `https://claude.ai/install.sh` (strict HEAD probe, headers only; only 2xx counts as reachable, so a Cloudflare 403 challenge or region block shows as failure — i.e. whether `curl -fsSL https://claude.ai/install.sh | bash` would be accepted) — and a plain-language verdict (baseline dead → the network itself is down; overseas sites dead → the terminal needs a proxy; all green → the terminal's network is fine). Every probe is issued exactly the way the terminal would run curl (a `curl` subprocess with no forced proxy flags, following the process's default network environment; urllib fallback when curl is absent), with per-site latency shown. Implemented as the standard-library-only `services/runtime/netcheck.py` behind three endpoints — `POST /api/v1/network/probe`, `POST /api/v1/network/ip`, and `GET /api/v1/network/proxy` (API-only local-proxy discovery: system proxy via `scutil`/registry + proxy env vars + a scan of common local proxy ports for Clash/V2Ray/Surge/Privoxy/Astrill/sing-box, returning a ready-made `export https_proxy=…` one-liner); probe targets are a server-side whitelist and proxy parameters must be loopback addresses. All 11 language dictionaries updated.
- New Output Settings switch **Precise character positions** (`output.spatial_blocking`, default on; New Project wizard step 2 and Project Settings → Output Settings, injected into every agent's "用户输出设定" prompt block). On = the scene layout-pack + character-route flow below; off = the legacy single scene-concept-image flow (no layout pack, no `blocking_map`, prompt binds the scene concept image as an ordinary `[Image N]` ref, and the `scene_layout_pack_ok` / `blocking_map_present` / `blocking_on_map` / `layout_map_bound` checks are skipped — the two check scripts read the project's `settings.json` and report `skipped`).
- Scene "layout pack" replaces the single main-view scene concept image (Phase 4 environment-concept): every scene now gets a top-down spatial layout map (`assets/concepts/scenes/<id>/layout_top.png`, clean, no people/labels), a 3x3 multi-angle scene sheet (`grid_9views.png`, generated with the layout map + new `agents/06-art/environment-concept/templates/scene_grid_template.png` as refs so all nine tiles show the same space) and a text source of truth `layout.json` (schema `scene_layout.v1`: map orientation, ≥3 landmarks with normalized `xy` and canonical `name_en`, semantics of tiles 1–9). Machine check `scene_layout_pack_ok` (`code/render_blocking_map.py --scene <id> --check-only`); the §6A concept coverage audit counts legacy `main_*.png`-only scenes as gaps.
- Storyboard groups now carry per-group character staging on the layout map: each `groups_draft[]` (and, after shot-planning, each `generation_groups[]`) has `scene_refs` + `blocking_map` — for every on-screen character a `start` (required), optional `path` / `end` (landmark ids from `layout.json`, optional `xy` nudge) and an English `route_en` sentence; each shot has `view_tile` (nearest tile of the 3x3 sheet). New `code/render_blocking_map.py` renders the annotations (colored start ●, end ■, arrows, legend) onto the layout map → `directing/epNN/blocking_maps/<grp>.png` and enforces `blocking_map_present` (landmark refs valid, `route_en` English, character set = `characters_union`, and start-of-group continuity with the previous group's end in the same scene). Per-shot blocking must lie on the group route and take landmark words verbatim from `layout.json#name_en` (`blocking_on_map`).
- Prompt agent: groups with a `blocking_map` must put the rendered layout map and the scene's `grid_9views.png` in `refs` (after character sheets), write a fixed "Spatial layout: [Image N] is the top-down layout map … do not render the map, its markers, arrows or labels. [Image M] is the 3x3 multi-angle sheet … do not copy its tiling." sentence before `Shot 1:` followed by `Map markers: A = <name> (<CHAR id>), B = …` (the rendered map's legend carries only letter = CHAR id — no CJK glyphs in the render font — so the name↔id↔letter mapping travels in prompt text), inject each character's `route_en` verbatim and may cite `framed like tile <view_tile> of [Image M]` per shot. New machine check `layout_map_bound` (`code/layout_map_bound_check.py`), also re-run by video-generation before submitting. Storyboard preview shows the group's rendered layout map among pipeline refs.
- Captions ("花字") now appear and disappear exactly when their words are spoken. A new per-episode word-level speech timeline `edit/epNN/word_track.json` (schema `wordtrack.v1`, built by `render_captions.py speech-align` from the sentence-level transcript — `av/epNN/beat_track.json` for audio-to-video projects, `edit/epNN/subtitles.srt` for the main pipeline — plus the episode audio) gives every spoken character/word its start/end; backends: `interp` (zero-dependency: sentence timecodes + ffmpeg silence detection + per-character weighting, sentences that fell entirely into silence are merged with their neighbour), `whisper` (optional `faster-whisper` word timestamps aligned to the transcript, transcript stays the text authority) and `import` (external ASR word lists such as Volcengine file-recognition `words[]`, seconds or milliseconds). `speech-lookup --text` prints when a caption's text is spoken; `speech-snap` rewrites every caption's `start/end/local_start/local_end` to the spoken span (moves the caption to the group where the words start, clips at the group end, min 0.25 s), and the new design-stage machine check `caption_speech_aligned` (±0.15 s, fails on a missing/stale word_track; `speech_free: true` exempts non-spoken on-screen annotations in main-pipeline projects only) is wired into workflow.yaml, WORKFLOW.md §9A, the caption SOUL and the caption-styling skill (`modules/speechalign.py`).
- Bundled LTX-2.5 Distilled text-to-video ComfyUI workflow (`video-ltx2.5-distilled-bf16-t2v-api.json` + bilingual documentation), wired into genmedia with resolution/aspect handling for the model.
- Storyboard-group settings gain per-model "Defaults" preset buttons (Seedance 2.0 / Seedance 2.5 / MiniMax H3) that fill duration and reference caps in one click; the recommended defaults are now 15 s / 9 reference images / 3 videos / 3 audio segments.

### Changed

- Seedance 2.0-family video models (including fast/mini) now auto-inject the bundled `sd20-prompt-writing` skill into the prompt agent, the same way 2.5 and H3 models already inject theirs.
- Minor UI polish: the standing explanation paragraphs under "Output aspect" and "Dialogue voice" in the wizard/Output Settings are collapsed behind inline ⓘ icons, and a redundant settings caption was removed.

### Fixed

- The web service no longer restarts a healthy API process on a transient health-check failure (which surfaced as Windows error 10054 mid-run): a short streak of failed probes is tolerated before the restart fires.
- DeepAgents runs that hit the engine's `recursion_limit` now resume automatically from the checkpoint instead of failing the whole task.

## [1.0.19] - 2026-08-18

### Added

- Stylized captions ("花字") post-production chain: a caption designer agent (10-editing/caption) plans per-group headline/keyword captions with entrance/exit animations and paired sound effects, the host renders them into a separate captioned edition of every clip and the final cut (the original edition is kept alongside), and the storyboard preview shows a ✨ caption button, a design dialog with per-caption ✏️ edit shortcuts that hand off to the Producer, and captioned clips side by side. The rendering engine is HTML+CSS templates (`captpl.v1`, Playwright batch rendering with seek-idempotent clocks, glyph-coverage and alpha gates before burning; the earlier ASS/libass path was retired), captions.json is at schema v3 with project-local templates, and fonts/SFX are synced from the remote asset repository (`assets-sync`, local `data/fonts|sfx` are pure caches). Enabled per project via `output.caption_enabled` (default off) with new `p9-caption-render`/`p9-caption-final` stations; the accompanying `caption-styling` skill fixes size calibration, animation and SFX discipline. All 11 language dictionaries updated.
- RunningHub is now its own channel tab in Settings → Generation Models (image/video/music/TTS, placed left of ComfyUI) and in the per-agent model override dialog: site (.cn/.ai), the two keys, cloud-workflow favorites and instance type all live there, while the ComfyUI tab keeps only local/Comfy Cloud. Stored configuration is unchanged (RunningHub active = `provider=comfyui` + `mode=rh_*`), old configs are placed on the new tab automatically, and the RunningHub skill injection now honours per-agent overrides.
- Bundled SeedVR2 video-upscale workflow (`video-upscale-seedvr2-api.json` + documentation) usable through `genmedia.py upscale` on local ComfyUI or Comfy Cloud, with resolution/aspect handling adjusted for it.
- Dialogue voice mode in Output Settings (New Project wizard step 2 and Project Settings → Output Settings): "Native video audio" (default) keeps the existing paradigm — dialogue speech is generated natively by the video model along with the picture and no dialogue TTS is ever produced. "Post-production dubbing" adds a per-group `p7-dub` task after video generation: the group clip's native track is analysed for the moments each character speaks, every frozen line is synthesized with the character's cast TTS voice (casting.json / voice.json), fitted to the on-screen mouth timing (speed ±25% + tempo ±10%, start aligned to the speech onset) and mixed back over the ducked native track with the video stream untouched (native audio backed up as `grpNNN.native_audio.wav`). Implemented by the new `code/dub_group.py` helper (auto speech-segment detection with `--detect-only`/`--segments` manual override, dry-run, idempotent re-runs, refuses to run in native mode) and wired through workflow.yaml (`p7-dub`, downstream lipsync/upscale/mix take the dubbed clip), WORKFLOW.md §8C plus voice-generation / lip-sync / audio-mixing / audio-qa / orchestrator SOULs. Persisted as `output.dialogue_voice` (`native`|`dubbing`), injected into every agent's role prompt, validated by `POST /api/v1/projects/{p}/config`; all 11 language dictionaries updated.
- Reference Files page: new "Video" category (`refs/video/`, mp4/mov/webm) placed after "Audio" for user-supplied reference clips (motion / camera movement / pacing / transition examples) with inline video preview; video-generation agents must inventory it first and inject clips via `genmedia.py video --ref-video` on models that support reference video (WORKFLOW.md §2 rule 8, role-prompt injection updated). Category order changed so "Thumbnails" now sits right below "Visual Style". All 11 language dictionaries updated.

### Changed

- `genmedia.py upscale` now supports RunningHub as the SeedVR2 backend. When the video ComfyUI run mode is RunningHub, the upscale submits the cloud workflow selected on the RunningHub tab instead of refusing ("暂不支持 RunningHub"). Because workspace exports normally carry no `{{VIDEO}}`/`{{WIDTH}}` placeholders, the tool binds nodes directly (idempotent when placeholders exist): the single `*LoadVideo*` node gets the uploaded source clip, the resize node upstream of `SeedVR2Preprocess` gets the target size (`ImageScaleByAspectRatio V2` — `scale_to_length` or its `Int` primitive per `scale_to_side`, keeping the source aspect; `ResizeImageMaskNode` / `width`+`height` nodes get both dimensions), the primary sampler gets `--seed`; VHS meta-batching (`VHS_BatchManager` + `meta_batch` links) is stripped because VHS re-queues the prompt server-side under new prompt ids that RunningHub does not track (the task reported SUCCESS with an empty output list), and preview-only `VHS_VideoCombine` sinks are pruned so the cloud encodes once and the download picks the real output. RunningHub product downloads now retry up to 4 times on a mid-stream network break (`IncompleteRead`/timeout) instead of failing a task that already succeeded and was billed. Verified on a real 864×480 / 12.25 s clip → 1280×712 (source aspect kept by the cloud workflow's longest-side scaling), 24 fps, 294 frames and audio preserved. `--dry-run` validates the binding against the cached cloud workflow without uploading or creating a task.
- Settings → Advanced: the "Concurrency & Timeouts" and "Agent Memory" dialogs are merged into a single "Agent Advanced Settings" dialog (per-agent concurrency, run / no-output timeouts, conversation memory switch) that gains a new **Retry count** setting (default 3, range 0–10): the cap on how many times agents automatically rerun / reroll — acceptance-scoring, QA or machine-check failures sent back by the Producer, and media generation rerolls after failed machine checks. Once a task hits the cap and still fails it is escalated to the user; 0 disables auto-retry entirely. Persisted in state.json as `max_retries`, exposed via the new combined `GET/POST /api/v1/config/agent-advanced` (the previous `/config/concurrency` and `/config/agent-memory` endpoints keep working), and injected into every agent's runtime prompt as a "用户重跑次数设定" section that overrides the "at most 3 times / ≤3 rerolls / max_retries: 3" hard-coded in the orchestrator dispatch rules, SOULs and WORKFLOW.md (those documents now note the setting). All 11 language dictionaries updated.
- Prompt agent (08-video-gen/prompt) no longer enforces the legacy "video_prompt < 1000 words" acceptance rule (SOUL.md DoD + WORKFLOW.md §7 table). None of the bundled official prompt skills set a numeric cap (sd25-pe explicitly says no fixed word limit; sd20 only asks for concision; H3's 350–500 English words is a target range for the `detailed_description` section, not a ceiling), the `split()`-based word count is meaningless for CJK prose since prompt language follows the UI language, and shipped projects routinely exceeded 1000 words while passing acceptance. Replaced by a de-redundancy check (no restated sentences, no repeated style/constraint clauses, no re-described appearance inside Shot sections) plus the H3-specific 350–500-word guidance; `video_prompt_word_count` stays as an observational field only.
- Delivery discipline for structured deliverables: every agent's runtime prompt, the orchestrator dispatch rules and WORKFLOW.md §2/§6 now state that JSON/MD/YAML design outputs must be written directly as final files — no "write a Python generator script, then run it" detour, no splitting one batch work order into several 2–3-item rounds, and no per-file duplication of shared boilerplate (input lists, coordinate/frame conventions). Scripts are only for real computation/media/check work and belong in the project `code/`, never in `runs/<task_id>/`. Batch (for_each-merged) work orders must end with an explicit "write the JSON files directly, no generator scripts, no batching" line. Motivated by an archigram p6-composition run that produced 13 valid composition.json files via seven generator scripts in six rounds, taking 4× as long as the sibling camera/blocking batches.
- Manually stopped runs are now recorded as such instead of looking like ordinary failures. Stopping a queued/running task from the Runs panel (⏹) sets a `stopped: "user"` field on the run (`stopped: "shutdown"` when the service itself is closing; both exposed via `/runs` and `/runs/{id}`), forces the final error text to "已被用户手动停止" even if the engine reported an abort first, and writes a chat entry that opens with an explicit "⏹ 已被用户手动停止 … 非程序错误,无需追查失败原因" banner (partial output preserved below it) instead of a bare truncated reply flagged as an error. `dispatch.py --status/--runs/--wait/--wait-all` now print the same marker (and `--status` prints the error line for failed runs, which it previously omitted), and the chat header shows "⏹ 已手动停止" rather than "⚠ 运行出错". WORKFLOW.md §5 and the orchestrator SOUL gain a rule: a manually stopped child task is not a failure to investigate or retry automatically — the user decides whether to redispatch. Failed runs that had partial output also now append the error reason to their chat entry.
- Run timeouts are now configurable and smarter. The former fixed 3600s per-run cap is replaced by two settings in Settings → Advanced → "Concurrency & Timeouts" (the renamed "Concurrency" dialog): a wall-clock run timeout (default 2 hours; the Producer automatically gets 4×) and a no-output timeout that terminates a run whose engine event stream has been silent for the configured duration (default 30 minutes, 0 disables; the Producer is exempt since it mostly waits silently on sub-tasks). Timed-out runs now kill the whole child process tree (previously only the engine binary, leaving yt-dlp/ffmpeg/dispatch descendants orphaned). Both values persist in state.json, are exposed via `GET/POST /api/v1/config/concurrency` (`run_timeout` / `idle_timeout`, seconds), and apply to runs started after saving. `dispatch.py --wait/--wait-all` default wait raised from 3600s to 7200s to match. All 11 language dictionaries updated.
- RunningHub submissions now prune inert "island" nodes from the workflow before task creation: note/template text nodes left in the cloud workflow by its author (e.g. `JjkText` blocks holding multi-kilobyte prompt-writing templates) were submitted verbatim with every billed request. A node is removed only when it has no downstream consumers, no incoming node links (pure literals) and is not a Save/Preview output node; pruned node ids are echoed in the submission log. Verified against a captured production payload: the three H3 template nodes are dropped (−16.6K characters) while the execution graph is untouched.
- Prop reference images must not contain people: the scale-anchor image now uses inanimate reference objects, character/costume sheets are no longer passed as `--ref` for props (people trigger channel moderation rejects and dilute the prop subject), and a `prop_image_no_person` machine check is added to the prop agent's DoD.
- Small UI polish: elapsed times over 60 s are shown as `1m0s`/`1h1m0s`, the chat box gains a subtle glow, the plugin-repository link now points to shumati.cn with a store icon, and the MiniMax settings hints link straight to the API-key console pages.

### Fixed

- Comfy Cloud generation and upscale no longer fail after billing: result polling used `/history/{prompt_id}`, which is 404 on Comfy Cloud, so every Cloud job was submitted and charged but never collected. Polling now goes through `/jobs/{prompt_id}` with the response normalized to the local history shape; the local ComfyUI path is unchanged.

## [1.0.18] - 2026-08-14

### Added

- Plugin repository link in the Agent Plugins dialog: a "🔗 插件仓库" entry next to the "Install plugin package (zip)" button opens the official plugin collection at github.com/AgenticsWorld/VideoAgents-Plugins in a new tab. All 11 language dictionaries updated.
- OpenCode execution engine (`opencode` in the top-bar engine selector, default model DeepSeek V4 Pro): runs agents through `opencode run --format json --auto` with persistent `--session` continuation, streams text/tool/token events into the run panel, and counts token usage per model call. The language-model menus are populated dynamically from `opencode models` (with a static DeepSeek V4 Pro/Flash fallback covering both the Zen `opencode/` and the Go-subscription `opencode-go/` channels), and `OPENCODE_BIN` overrides the executable (`~/.opencode/bin` is probed automatically, including by the desktop client).
- DeepSeek Smart Assignment model strategy (Settings → Model Strategy): creative-core agents run on `opencode-go/deepseek-v4-pro`, all other agents on `opencode-go/deepseek-v4-flash` (the OpenCode Go subscription channel), using the same task-complexity tiers as the existing Claude/Codex/Kimi smart strategies.
- OpenCode Go usage in the Resource Usage panel: a new toggle in Settings → Resource Usage queries the OpenCode Go subscription quota (5-hour/weekly windows) via the official console endpoint, using either a configured API Key or, when left blank, the local opencode login credential. All 11 language dictionaries updated.
- RunningHub account balance in the Resource Usage panel: a new toggle with separate API keys for the .ai and .cn sites (the two accounts are not interchangeable; keys are stored locally, independent of the generation-models page), showing remaining RH coins and money per configured site with the usual 10-minute cache and force-refresh.
- RunningHub placeholder-free direct binding extended from video to the image, music and TTS ComfyUI branches: cloud workflow exports without `{{TOKEN}}` placeholders previously let the template author's demo prompts, lyrics, images and voices silently blend into billed production requests. The prompt/negative/seed/reference image (image), style prompt/lyrics/duration/seed (music) and line text/reference voice/seed (TTS) are now bound by following the node wiring, with every unresolvable case failing before submission so nothing is billed. Task submission also prints the RunningHub taskId immediately for billing reconciliation, and status polling tolerates up to 3 transient network drops before giving up with a check-the-web-console warning.
- RunningHub instance type selector: each media type's ComfyUI section gains a Standard/Plus/Ultra run-mode dropdown (Standard keeps the previous behavior; other tiers are passed through to task creation and echoed in the submission log).
- Style library grew by 58 animation styles (52 2D / 6 3D, 94 → 152 total), and every style card gains a zoom button for full-size preview.

### Changed

- Saving Settings → Generation Models now auto-syncs the local caches of every currently selected RunningHub workflow (image main/reference, video, music, TTS) to the latest cloud version. Previously the cache never expired, so edits made in the RunningHub web editor (e.g. swapping the diffusion model) silently did not take effect until the cache file was deleted by hand. Sync failures do not block saving — submissions keep using the old cache and the save status shows a prominent warning; when a cloud workflow's content actually changed, the producer agent is notified to update dependent agents. All 11 language dictionaries updated.
- MiniMax H3 Ref2VA reference-image sizing is now selectable per request: `genmedia.py video` gains `--ref-image-size match|max` (default stays `match`, which downscales references to the generation's pixel area; `max` passes short edge ≤2048 through untouched for better identity fidelity at higher time/cost). The chosen value is force-applied to RunningHub cloud workflows at submission time, and non-H3 channels/workflows reject the flag instead of silently ignoring it.
- Character reference sheet template redesigned from five panels to four: the two stacked head-and-shoulders close-ups on the right are merged into a single full-height large close-up (three full-body views front/side/back on the left are unchanged). Template PNG, panel semantics JSON, the character-concept agent's layout prompt/self-check contract and WORKFLOW.md updated accordingly.
- Settings menu reorganized: Plugins is promoted from the Advanced submenu to a top-level item right below Resource Usage, and the Advanced submenu now sits above Interface Language.

### Fixed

- Desktop auto-update failures: `__pycache__`/`*.pyc` are excluded from the packaged plugins, and the Python bytecode cache is redirected outside the app bundle (`PYTHONPYCACHEPREFIX`) — on macOS, bytecode written inside the signed .app invalidated the signature and Gatekeeper refused the next launch. The updater also resolves the correct .app root on macOS, validates the application path before downloading, and rejects App-Translocation launches up front with a move-to-Applications hint.

## [1.0.15] - 2026-08-13

### Added

- Virtual Portrait Library (Settings → Advanced): integration with Volcano Ark's private virtual-portrait media-asset library. A dedicated settings page walks through activating the Advanced Creation Package and configuring Volcano Engine IAM Access Key / Secret Key (falling back to the File Hosting TOS credentials when left blank), with an enable/disable toggle; once enabled it lists every asset in the account's library with total count, name search, 50-per-page pagination, thumbnails, review status and irreversible-delete actions. On the Character Preview page every character image gains a one-click Register button with live status (registering is asynchronous: Processing → Active/Failed, tracked in a content-hash ledger so identical files are registered once). During video generation, reference images whose content hash matches an Active library asset are automatically submitted as `asset://<asset-ID>` URIs instead of inline base64 (Seedance 2.x only), which avoids face-reference moderation blocks; the Storyboard Preview marks such references with a 🛡 badge. Also adds a `genmedia upload` subcommand that uploads a local file through the configured object-storage channel and prints a presigned URL. All 11 language dictionaries updated.
- `runninghub-cloud-workflow` skill for the video-generation agent: documents the parameterization mechanics, asset upload and wiring conventions, the honest-reporting rules for parameters that cannot reach the cloud on placeholder-free templates (`--seed`, and `--resolution`/`--aspect`), `promptTips`/`failedReason` troubleshooting, and billing discipline. Injected only when the video channel is ComfyUI on a RunningHub site.

### Changed

- Minor UI copy: the TOS file-hosting hint now links to the Volcano IAM Key Management console for obtaining the AccessKey, and the ComfyUI workflow selector labels were renamed from "workflow JSON" to "workflow API".

### Fixed

- RunningHub placeholder-free cloud workflows regressed to silently running with the template author's demo prompt and default duration (the working implementation had lived only in uncommitted worktree files and was lost to an overwrite). Prompt and duration injection are reimplemented by following the H3 node wiring — the prompt rewrites the upstream text primitive, the duration rewrites the frame-count primitive using the workflow's duration-to-frame-count conversion semantics — and any unrecognizable wiring now fails before submission, so no request is sent and nothing is billed. Reference wiring also detaches all leftover template `ref_images.*`/`ref_audios.*` connections and deletes orphaned loader nodes first, so the author's demo assets can no longer silently blend into production requests when fewer references are submitted than the template has slots.

## [1.0.14] - 2026-08-13

### Added

- Diagnostics data (Settings → Advanced → Diagnostics): agent issues and lessons collected after distribution can be manually exported as a diagnostics package for the developers — local collection only, manual export only, never uploaded automatically. A single privacy boundary module enforces a field whitelist at the entry point (prompts, project content, file paths and API keys can never get in), error messages are templated into 200-char patterns with 16-char signatures so identical failures cluster across machines, and project names are stored as 12-char hashes. Two collection points cover run-level events (engine/agent/status/duration/error signature) and generation-level events (kind/provider/model/outcome/duration). Optional per-run lesson cards (`runs/*/lesson.md`, written only under strict conditions) can be previewed and individually selected — none are exported by default. The export zip carries a versioned manifest, event files, an aggregated summary, and the selected lesson cards.
- Seedance 2.x reference-to-video on Comfy Cloud: two bundled workflow templates (Seedance 2.5 and 2.0, built on the paid ByteDance2ReferenceNode API node, Comfy Cloud only) wired into a dedicated genmedia branch — reference images uploaded per item and attached dynamically, all validation performed before anything is billed (local-ComfyUI rejection, unsupported input modes, reference-count caps of 9/30, integer duration ranges, resolution clamping, ratio fallback), audio generation on by default, and the continuity last-frame extracted locally from the finished clip.
- `sd20-prompt-writing` skill: the official Doubao Seedance 2.0 prompt-writing guide ships with the repo (quick-reference entry plus a fully reorganized reference including formulas, worked examples and a 12-item troubleshooting section), structured to match the existing `h3-prompt-writing` skill.

### Changed

- MiniMax and RunningHub API keys are now stored separately per interface region/site (the global and China platforms use non-interchangeable accounts and keys; a shared single key field could silently overwrite one side when switching region or run mode). Existing single-key configs are migrated automatically into both slots with unchanged behavior, and the settings page shows the key field matching the selected region/run mode.
- Default Volcano Ark / BytePlus image model switched to Seedream 5.0 Lite, whose output containing faces passes Seedance review by default; the dropdown labels note this recommendation.
- The audio-to-video plugin's source tree was removed from the repository — it ships solely as a standalone installable plugin package (as introduced in v1.0.12).

### Fixed

- MiniMax-H3 reference audio no longer rejected with error 2013: audio MIME types are now mapped explicitly (mp3/wav/m4a/aac/flac/ogg) instead of relying on `mimetypes` guesses (`.mp3` → `audio/mpeg` fell outside H3's whitelist), and unsupported extensions fail fast with a transcode hint rather than sending a request that is guaranteed to be refused.
- Comfy Cloud component preflight and connection test no longer break on the missing per-node `object_info` endpoint (Cloud returns 404 and only supports the full listing): the preflight now performs a single full fetch, and gzip transfer encoding shrinks the ~9.4 MB plaintext response to ~0.7 MB, which also stops local proxies from truncating the stream mid-body.
- Clip-cutter concurrent-overwrite hazard fixed: the host footage module gained an atomic JSON write primitive and all plugin-side writes are forced through it.
- Footage downloads no longer stall indefinitely on unstable proxy exits: yt-dlp now runs with a 30-second socket timeout and 3 retries, failing fast instead of hanging until the outer one-hour timeout kills it.

## [1.0.13] - 2026-08-10

### Added

- MiniMax generation provider across all four media types, each as a new tab placed before ComfyUI on the 🎨 generation-models page: image (Image-01, single subject-reference image), video (MiniMax-H3 only — the project resolution tiers are mapped onto H3's two-tier 768P/2K ladder automatically, duration 4–15 integer seconds, first/last frame plus multi-reference image/audio/video modes, native audio-video co-generation, continuity anchor extracted locally from the finished clip), music (Music 3.0/2.6 with the same force-instrumental toggle as Eleven Music; auto-written lyrics when disabled), and TTS (Speech 2.8 family with a voice-library browser backed by `get_voice`). A per-tab API-region selector covers the non-interchangeable global (api.minimax.io) and China (api.minimaxi.com) platforms, `MINIMAX_API_KEY` works as the environment fallback, and MiniMax is selectable in per-agent image/video provider overrides.
- ComfyUI channel cloud execution: besides the local server, every ComfyUI tab (image/video/music/TTS) can now run on Comfy Cloud (cloud.comfy.org, X-API-Key, same API surface passed through) or on RunningHub's hosted platform — China (.cn) and International (.ai) sites with non-interchangeable keys. RunningHub workflows are added by pasting a workflow ID or page link (verified, cached locally, reported with node/placeholder/H3 details), driven through the existing `{{TOKEN}}` placeholder pipeline with automatic input upload, status polling and result download; the connection test reports account balance and concurrent-task count.
- Seedance 2.5 video models in both the Volcano Ark and BytePlus/Dreamina dropdowns, with a project-level storyboard-group settings block and generation-aware parameter validation in genmedia; the official `sd25-pe` prompt-writing skill ships with the repo and is injected into the prompt agent only when a 2.5 model is selected.
- MiniMax H3 prompt-writing skill: the official `h3-prompt-writing` skill (base + reference-to-video guides) is injected into the prompt agent only when an H3 channel is active.
- Video upscaling via MiniMax Regenerate-2K: a new upscale-agent skill plus a `genmedia upscale` subcommand — H3 768P-output preflight (fps/audio/dimensions/frame-count), fixed 2K output rescaled to the delivery profile in post, `--source-task-id` shortcut that skips re-uploading the source, and >45MB sources routed through presigned object storage.
- Pi execution engine: run Agents through the local `pi` CLI with JSON event streaming, session resume, per-run model selection, and dynamic language-model menus populated from the models available to the current Pi login.
- DeepAgents cloud model channel: the language-model page gained a third channel tab (default DeepSeek endpoint, v4-flash/pro) wired to the top-bar model dropdown and backend model resolution.
- Feishu asset push: finished concept art (characters/scenes/props) and clips are pushed automatically to the bound Feishu chat as captioned images and playable videos; oversized files degrade to a text notice, the pushed ledger survives restarts, and regenerated files are pushed again.
- Feishu `/auto` command: toggle per-project auto-run from chat — a status card with one row and an on/off button per project, plus `/auto on|off [project]` and `/auto off all`. Handled locally without engine quota, so it keeps working when engines are exhausted or stalled.
- Reference panel revamped from "reference images" to "reference files": a new text category, a copy-project-relative-path button on every file card, and the music category re-scoped as "audio" (background-track candidates the music agent picks up first). Accepted image formats extended with bmp/tiff to match Seedance's official list.
- Resource-usage panel: Codex and KimiCode usage checking gained on/off switches, and usage bars now show when the quota window resets.
- Workflow preview: the "spent" row now includes project-level video/image/language token consumption, and Volcano Ark image generation records usage to the ledger.
- Voice library moved to a remote catalog: a built-in 58-voice catalog (gender/age/pitch/tags metadata) backs the timbre selector, and selected voices are downloaded on first use into the local cache with atomic writes — no bundled audio needed.
- Bundled local ComfyUI media workflows, renamed and categorized with bilingual documentation, a console dropdown selector and a documentation popup.
- Host-side footage module for the mashup plugin: footage ingestion and integrity checking.
- Character sheets rejected by real-face review can now be salvaged conditionally: the face is rendered in colored pencil while body and background keep their original texture.
- Watchdog wake-up messages now carry the project's standing instructions.
- Desktop packaging and updater now distinguish distribution sources across build info, the runtime index and runtime downloads.

### Changed

- Standardized local development and CI/desktop packaging on Node.js 24.19.0 through `.nvmrc`; GitHub Actions now reads the same version file instead of independently pinning Node 22.
- Generation-models page copy reworked: sections renamed to "×× models", and the language-model / DeepAgents channel tabs show a ✅ marker on the active channel.
- Seedance 2.0 dropdown labels now note the intended use — Fast "suited to animation", Mini "suited to advertising" — on both the Volcano and Dreamina channels.
- Workflow preview no longer shows money estimates anywhere (only claude-engine sessions report a USD cost and session logs are TTL-cleaned, so the figures were inevitably incomplete and misleading); duration statistics remain.
- Confirmation/signature popups moved from the top-right to the bottom-right corner; multiple popups still stack upward.

### Fixed

- Pi streaming replies no longer render one token per vertical line while a run is active; consecutive text deltas are coalesced into one live text block while preserving tool-event ordering.
- Long engine/model identifiers in the run panel now truncate with a tooltip instead of pushing durations and per-run stop buttons under the scrollbar.
- `make run` now exits cleanly on the first Ctrl+C even while the browser's SSE stream is open; shutdown also reaps the managed API, draw sidecar, active Agent process groups, messaging relays, and keep-awake helper, with a bounded timeout fallback.
- Page loads no longer stall for seconds: SSE disconnect cleanup was moved off the event loop, codex usage caching got a matching TTL, and startup requests are issued in parallel.
- Signature confirmations no longer re-pop after the waiting dispatcher times out: re-asking the same question reuses the unanswered confirm, so clicking the original popup takes effect immediately, and an answer given within 10 minutes is back-filled to the new waiter.
- Preview sidebar entries (characters/scenes/props/worldview) no longer squeeze long names down to a single character: name and badge share the first line, the id moves to its own smaller second line.
- Workflow preview durations: the meta.json end time is accepted under both `ended_at` and `finished_at` (both spellings exist in stored runs), restoring durations for tasks that only wrote `finished_at`.
- Windows desktop updates: fixed updates failing to install or not being applied after download.
- CI: `npm ci` no longer fails on a lock-file desync — sharp's 26 platform packages and `@emnapi/runtime` (pulled in by the baileys dependency) were missing from `package-lock.json`.

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

### Fixed

- deepagents: raise the default LangGraph `recursion_limit` from 50 to 250 (500 for dispatchers) so multi-tool runs no longer fail with `GraphRecursionError` mid-task.
- deepagents: use one real host-path view for filesystem tools and shell execution, with explicit workspace/project roots, so absolute project paths no longer alternate between virtual `/data` paths and nested workspace mirrors.
- orchestrator/dispatch: after errors or rework, do not switch execution engines; force `--engine` overrides are ignored.

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
