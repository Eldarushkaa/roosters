# Rooster v2 runtime art

Approved by the user: four contrasting builds and six sword silhouettes from
`docs/design/concepts/roosters-v2/`. Prepared with built-in `image_gen`.
Original PNG masters and full prompts are in
`docs/design/concepts/roosters-production/`.

- `yard.webp`, `copper.webp`, `storm.webp`, `ember.webp`: each is a transparent
  1536×1024 atlas, 3×2 cells of 512×512. Armor progresses in reading order;
  the bare head and empty sword-hand allow independent equipment overlays.
- `helmets.webp`: five transparent open-face helmets in the same grid; cell 0
  is empty. Face openings stay transparent.
- `swords.webp`: six separate transparent swords, with individually measured
  crops and grip points. Shapes follow the approved sword concept.

Runtime export: WebP quality 95, no resizing, alpha preserved byte-for-byte.
All six atlases total approximately 2.1 MB. Only the active breed, sword atlas,
and (when equipped) helmet atlas are requested; the browser reuses cached files.
PNG masters retain the original generated pixels. Art contains no text or stats.

`web/rooster-art.mjs` owns crops, registration offsets, head/grip anchors, breed
scale and independent equipment selection. Levels map to visual milestones:
0 → 0, 1–2 → 1, 3–4 → 2, 5–7 → 3, 8–9 → 4, 10 → 5. These are appearance
groups only; every upgrade retains the existing server power/price rules.

Use `renderRooster(fighter, {label, className, priority, frame})` for Arena and Battle.
The fixed common stage preserves the size contrast between frail Yard, stocky
Copper, wiry Storm and heavy Ember. The sword is drawn behind the closed fist;
the helmet shell overlays the bare head. Battle passes one common
`roosterBattleFrame([you, opponent])` to both render calls to fit complete
silhouettes without making a frail bird as large as a heavyweight. Mirroring
happens on the whole figure.
Never crop or recolor the assets at runtime to infer equipment from power.

Humans use the exact saved equipment levels. Bots receive a stable server-side
visual loadout when their existing power/breed are selected; this does not
change combat power or random choices. Historical battles missing equipment
render the baseline kit. The old `arena/rooster-hero.webp` remains for the
isolated historical preview; it is no longer the live Arena fighter.
