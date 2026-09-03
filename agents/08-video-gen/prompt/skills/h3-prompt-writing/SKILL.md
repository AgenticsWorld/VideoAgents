---
name: h3-prompt-writing
description: Write MiniMax H3 video generation prompts for T2VA, I2VA, FL2VA, L2VA, and Ref2VA. Use when rewriting multimodal requests into H3 prompt structures, composing integrated_multimodal_description, overall_soundscape, and non_diegetic_music, aligning keyframes, or defining reference labels for images, videos, and audio.
---

# H3 Prompt Writing

## Workflow

1. Identify the input mode: T2VA, I2VA, FL2VA, L2VA, or full-reference Ref2VA.
2. For base text/keyframe modes, read `references/base-en.txt` and follow its final prompt structure.
3. For full-reference mode, read `references/ref-en.txt` and follow its six-section rewrite format.
4. Preserve the exact field names, section order, labels, and timing notation from the selected guide.

## Base Modes

- T2VA: build the full audiovisual timeline from text.
- I2VA: start from the first frame and develop forward from it.
- FL2VA: describe the continuous path between the first and last frames.
- L2VA: infer a plausible opening and converge to the supplied last frame.

Use `integrated_multimodal_description`, `overall_soundscape`, and `non_diegetic_music` in the order shown in `references/base-en.txt`.

## Full-Reference Mode

Ref2VA rewrites use `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, and `non_diegetic_music` in that order. Reference labels stay consistent across all sections.

Read `references/ref-en.txt` for label rules, retention analysis, and complete examples.

## H3 Binding Rules

These rules are specific to H3 and take precedence over provider-neutral or
Seedance prompt conventions:

- Use H3's six-section Ref2VA structure exactly. Do not submit a prompt that
  only contains `Shot N:` paragraphs when a full-reference rewrite is required.
- Define every visible character before the first shot as a stable
  `<Subject N>` and bind each speaking character to a stable speaker ID
  `(S1)`, `(S2)`, etc. Reuse the same IDs throughout the prompt.
- Put spoken content only inside `<d>[Language] ...</d>`. Preserve the source
  dialogue verbatim, including its language and punctuation. Do not use the
  project shorthand `{...}` as the only dialogue marker, and do not include a
  translated duplicate of the same line.
- Bind voice references explicitly: `<Audio N>` must identify the voiceprint
  for the matching subject/speaker. A voiceprint is a timbre reference only,
  not a recording of the target line; never ask H3 to copy or lip-sync an
  external TTS waveform.
- In a dialogue shot, state which subject speaks each line. A silent listener
  must be staged explicitly with `listens silently, mouth closed`; for a
  single-speaker shot, state that the other characters do not speak.
- Keep a dialogue group to at most three visible speakers and prefer one or
  two. If more speakers are needed, split the group before writing the prompt.

## Reference Image Rules

- Reference indices are one-based and follow the upload array exactly:
  `<Picture 1>` is the first uploaded image, `<Picture 2>` the second, and so
  on. Never renumber by asset type or use zero-based indices.
- Define the character-to-picture mapping explicitly before `[Shot 1]`; do
  not rely on a filename or on visual inference alone. Every visible person,
  including a back-facing or out-of-focus listener, must map to one reference.
- Prefer one clean, single-person canonical image per character (stable face,
  hair, age, and costume). Avoid multi-view sheets, collages, labels, and
  multiple people in one identity reference when identity matching is failing.
- Keep the reference set small and ordered by importance: character images
  first, then the blocking/layout anchor, then scene or prop references, and
  continuity frames last. Start with four or fewer images while debugging;
  adding references can dilute identity conditioning.
- A blocking map is only a spatial constraint. State that it controls standing
  position, facing, and movement, and that its markers, arrows, labels, and
  legend must never appear in the rendered video.
- A 3x3 layout sheet is only an architecture, lighting, and camera-space
  reference. State that H3 must not reproduce its tiling or annotations.
- Do not write `opening continues from` for a continuity frame when the next
  shot introduces a new character or location. Use an explicit cut to a
  different composition and describe the new character's entrance.

## Audio And Debugging Rules

- For narration-over or ambient-only shots, explicitly require silent acting:
  `no dialogue, no speech, no singing, no vocal sounds`; include only the
  stated ambience and physical effects.
- If H3 invents speech or mismatches speakers, reproduce the same run with
  native audio disabled and without `audio_refs`. This isolates visual/prompt
  binding from H3's joint audio generation; do not compensate by attaching an
  external TTS track to drive lip motion.
- Before delivery, verify: every `<Subject N>` has the intended picture,
  every `(Sx)` has one speaker, every `<Audio N>` points to that speaker's
  voiceprint, and no dialogue appears outside `<d>`.

## Output Rules

- Write rewrite sections in English; preserve dialogue, lyrics, and visible scene text in their original language.
- Describe each shot by composition, subjects, environment, actions, camera, sound, and the exact point where referenced content appears.
- Avoid plot summaries, unresolved reference labels, and timing that does not match the requested duration.
