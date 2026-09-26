# Quick-start illustrations

Generated with the built-in `image_gen` tool for the user's illustrated tutorial
reference on 2026-09-26. The coach uses the supplied tutorial and approved Arena
as style references. Existing Arena artwork is unchanged.

- `rooster-coach.webp`: 640 × 534, transparent, 58,470 bytes.
- `reward-chest.webp`: 480 × 499, transparent, 64,876 bytes.

Encoded with `cwebp -q 88`, preserving alpha. All labels and tutorial content
are localized DOM; these assets contain no text. Consumed only by
`renderGuideContent` in `web/app.js`, composed by `web/guide.css`.

## Coach prompt

```text
Use case: stylized-concept. Create one standalone transparent RGBA sprite for a cheerful mobile rooster fighting game tutorial. A waist-up cream-white rooster knight, large scarlet comb, stern playful eyebrows, golden orange beak, deep navy blue tail feathers spreading to the left, brown leather gloves, cobalt blue and gold armor and blue diamond shield at lower left. Right hand raised in an unmistakable thumbs-up to the right. Polished 2D cartoon game illustration: extremely bold near-black outlines, simple chunky silhouettes, cel shading, warm highlights, saturated colors. Match the rooster in the supplied game screenshot style reference. Character only, centered, generous transparent padding, all feathers and thumb inside frame. NO text, letters, numbers, watermark, panel, backdrop, floor or shadow outside character. Genuinely transparent alpha background. This is a reusable character asset, not a full UI mockup.
```

## Chest prompt

```text
Use case: stylized-concept. Asset type: isolated transparent mobile game reward illustration. One chunky purple treasure chest with thick gold edges and clasp, slightly open with bright gold coins spilling at front and three coins arcing above. Friendly polished 2D cartoon, near-black thick outlines, rich purple panels, warm golden yellow metal, crisp cel shading and small white highlights. Three-quarter view, complete silhouette with padding. A small diamond embossed on the coins. No text, lettering, numerals, watermark, UI, floor or background. Genuine transparent alpha. Matches the bold illustrated rooster fighting game style of the reference.
```
