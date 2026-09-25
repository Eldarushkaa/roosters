# Roosters — Design System

This document defines the visual and interaction direction for the Roosters Telegram Mini App.

All new UI and significant UI changes should follow this document together with the `frontend-design` and `telegram-miniapp-design` skills.

---

# 1. Product personality

Roosters should feel like a:

**Compact illustrated neighborhood fighting club.**

The product should feel:

* competitive
* playful
* confident
* slightly rough
* energetic
* game-like
* native to mobile

It should NOT feel like:

* a SaaS dashboard
* a crypto app
* a generic AI-generated landing page
* a casino
* a glassmorphism showcase
* a corporate productivity tool

The rooster character and fighting-club personality are the strongest parts of the visual identity.

Develop them rather than covering them with generic decoration.

---

# 2. Core UX principle

The game itself is more important than decoration.

Important gameplay actions must appear before:

* promotional copy
* large illustrations
* statistics
* history
* secondary explanations

For returning players, the Arena should feel immediately playable.

A user opening the Arena should understand within a few seconds:

1. Who their fighter is.
2. What mode they can play.
3. What they are risking.
4. What they can win.
5. Where to press to fight.

---

# 3. Visual hierarchy

Every screen should have a clear order of importance.

Prefer:

1. Current state / context
2. Primary gameplay information
3. Primary action
4. Supporting information
5. History / secondary content

Do not give equal visual weight to everything.

There should normally be only one dominant primary action per screen.

---

# 4. Color system

Use semantic colors rather than assigning colors independently to components.

## Primary action

Amber is the main product/action color.

Use amber for:

* primary fight actions
* important confirmations
* primary CTA
* selected interactive emphasis when appropriate

Do NOT use amber simply as decoration everywhere.

## Success

Use green only for meaningful positive states:

* victory
* reward successfully claimed
* purchase completed
* positive balance change
* success confirmation

Do not use green as a second generic action color.

## Danger

Use red for:

* destructive actions
* defeat emphasis when appropriate
* critical errors
* negative balance change
* dangerous states

## Neutral

Use neutral surface and text colors for ordinary UI.

### Dark theme

Prefer:

* charcoal backgrounds
* slightly lighter secondary surfaces
* warm off-white primary text
* muted gray secondary text

Avoid pure black everywhere.

### Light theme

Prefer:

* warm neutral backgrounds
* clean light surfaces
* dark neutral text

Avoid a sterile pure-white corporate appearance.

## Telegram

Respect Telegram theme colors and light/dark mode.

Custom product colors should coexist with Telegram's theme rather than fight against it.

---

# 5. Typography

Typography should feel strong and compact.

Use system/native UI fonts unless there is a strong reason to introduce another font.

Suggested hierarchy:

## Screen title

24–28px
Weight: 700

Use sparingly.

## Section title

17–20px
Weight: 600–700

## Body

15–16px
Weight: 400–500

## Supporting text

14px
Weight: 400

## Caption / metadata

12–13px

Do not use essential explanatory text below 14px.

Avoid extremely small 6–10px labels for important gameplay information.

Numbers related to:

* power
* money
* stake
* payout
* countdown
* battle statistics

may receive stronger weight or size than surrounding text.

---

# 6. Spacing

Use a consistent spacing scale:

* 4px
* 8px
* 12px
* 16px
* 24px
* 32px

Default horizontal screen padding:

**16px**

Use 24px when a section needs stronger separation.

Do not introduce arbitrary spacing values unless necessary.

Prefer whitespace over adding another container or separator.

---

# 7. Border radius

Use a limited radius system.

## Small

8px

For:

* small controls
* badges
* compact elements

## Medium

12px

For:

* buttons
* inputs
* rows
* selectors

## Large

20px

For:

* important panels
* bottom sheets
* major grouped surfaces

Do not randomly mix many similar radius values.

Not every element needs rounded corners.

---

# 8. Surfaces and cards

Cards should be used only when they communicate meaningful grouping.

Do NOT wrap every section in a bordered rounded card.

Prefer hierarchy through:

* spacing
* typography
* background levels
* alignment

Avoid deeply nested cards.

Avoid repeating:

card → card → colored icon box → pill → bordered button.

A screen should not look like a collection of independent widgets.

---

# 9. Borders and shadows

Use borders sparingly.

Prefer subtle tonal separation between surfaces.

Shadows should be rare and restrained.

Avoid:

* large blurry shadows
* glowing controls
* floating glass panels
* excessive backdrop blur

The interface should feel solid and game-like, not translucent.

---

# 10. Buttons

Controls must be comfortable for touch.

Minimum interactive height:

**44px**

Primary buttons should generally be:

**48–52px**

The main battle/tap action may be larger when gameplay requires it.

## Primary button

Use for one main action.

Characteristics:

* amber
* visually dominant
* strong readable label
* obvious pressed state
* clear loading state

## Secondary button

Use neutral styling.

It must not compete visually with the primary action.

## Destructive button

Use danger styling only for genuinely destructive actions.

## Disabled

Do not simply apply low opacity to the entire component.

Different unavailable states should communicate different reasons.

Examples:

* insufficient funds
* already equipped
* cooldown active
* action temporarily unavailable

When useful, show the reason close to the control.

---

# 11. Icons

The application should use one coherent icon language.

Prefer:

* custom/simple SVG
* consistent stroke or fill treatment
* iconography inspired by the existing illustrated equipment and rooster world

Avoid mixing:

* chess pieces
* emoji
* random Unicode symbols
* generic icon packs
* detailed illustrations

inside the same functional interface.

Icons should support understanding, not decorate every row.

---

# 12. Illustration

The rooster is a major part of the brand.

Use rooster illustrations for:

* fighter identity
* breeds
* major game moments
* important empty states
* victory / defeat personality

Do not repeat a large promotional hero illustration above routine gameplay every time the user returns.

For returning players, use a compact fighter presentation.

Large hero treatments should be reserved for moments where they add value.

---

# 13. Arena

Arena is the playable home screen.

For returning users, prioritize:

1. Compact fighter summary
2. Mode selection
3. Stake
4. Expected payout / risk information
5. Fight CTA
6. Latest result
7. Secondary information
8. History

The user should not need a long scroll before they can start a fight.

Remove or reduce promotional landing-page content from the normal returning-player flow.

The first-visit experience may be more explanatory and theatrical.

---

# 14. Battle

Battle is the most interaction-critical screen.

The following should fit together whenever reasonably possible:

* countdown / battle state
* both fighters
* essential fight statistics
* main tap action

The main tap action must always remain reachable.

Bottom navigation must never cover the battle control.

During a timed battle, remove or visually suppress navigation that is not useful.

Gameplay has priority over global navigation.

The battle interface should minimize unrelated information.

---

# 15. Battle feedback

Every accepted interaction should have immediate visual feedback.

Use:

* pressed states
* small scale feedback
* short transitions
* number/value updates

Haptics may reinforce meaningful gameplay events.

Do not vibrate excessively for every trivial interaction.

Important events may use stronger feedback:

* battle starts
* decisive hit/event
* victory
* reward
* important purchase

---

# 16. Results

Results should be clear but not obstructive.

Prioritize:

1. Victory / defeat
2. Money gained/lost
3. Important battle result
4. Continue / fight again action

Do not cover a large portion of the interface longer than necessary.

Once acknowledged, results should remain available in a compact persistent form.

---

# 17. Gear

Do not force equipment into cramped three-column layouts on narrow devices.

Prefer readable upgrade rows or appropriately sized cards.

Each item should clearly communicate:

* item
* effect
* current level/state
* price
* availability
* action

Unavailable actions should explain why.

Equipped and unaffordable states must look meaningfully different.

---

# 18. Breeds

Breed selection should emphasize the rooster itself.

Prefer recognizable rooster previews over abstract symbols.

Clearly distinguish:

* owned
* equipped
* available to buy
* locked/unavailable

The equipped breed should be immediately recognizable.

---

# 19. Roost / rewards

Rewards should clearly communicate state.

Examples:

**Ready**

Reward is available now.

**Next reward**

Show when it becomes available.

**Claiming**

Action is currently processing.

**Claimed**

Provide immediate success feedback.

Avoid showing multiple equally prominent actions.

---

# 20. Rankings

Rankings should prioritize readability.

Use clean player rows.

Avoid excessive decorative containers.

The user should quickly scan:

* position
* player
* relevant score
* own position

Power/PvP selection should use the same segmented-control pattern used elsewhere in the product.

---

# 21. Navigation

Keep the four primary destinations if they remain the main product structure.

Navigation should be:

* compact
* stable
* easy to tap
* visually quieter than gameplay

Selected navigation state should be clear without becoming the brightest element on the screen.

Avoid a large floating glass-style navigation dock.

Navigation must reserve appropriate layout space and never obscure content.

---

# 22. Telegram environment

The app runs inside Telegram.

Account for:

* Telegram light/dark theme
* viewport changes
* expanded/collapsed viewport
* device safe area
* Telegram content safe area
* virtual keyboard
* Telegram BackButton
* Telegram lifecycle/resume behavior

Fixed bottom UI must never assume browser viewport dimensions alone.

Use Telegram-native capabilities when they improve UX.

Do not use them merely because they exist.

---

# 23. Loading states

An asynchronous action must visibly react immediately.

Depending on context, use:

* button loading state
* changed button label
* local spinner
* skeleton
* optimistic update

Avoid full-screen loading for small local operations.

Loading feedback should appear where the action occurred.

---

# 24. Empty states

An empty state should answer:

1. What is missing?
2. Why is it empty?
3. What can the user do?

Keep empty states compact.

Do not use giant illustrations unless the empty state is an important product moment.

---

# 25. Errors

Errors should be understandable to ordinary users.

Do not expose raw backend errors.

Whenever recovery is possible, provide a relevant action such as:

* Retry
* Refresh
* Return
* Change selection

Network errors should not destroy existing useful screen state.

---

# 26. Motion

Motion should reinforce interaction.

Preferred duration:

approximately **120–220ms** for ordinary UI transitions.

Use:

* subtle fade
* small translate
* scale feedback
* sheet transitions
* value transitions

Avoid:

* excessive bounce
* animated gradients
* constant ambient movement
* long decorative transitions

Respect reduced-motion preferences.

---

# 27. Responsive targets

Review important screens at approximately:

* 360px width
* 390px width
* 430px width

Also test short viewport heights.

Do not solve responsive problems by shrinking important text until everything fits.

Prefer changing layout.

---

# 28. Components

Prefer reusable components for recurring patterns.

Important shared components should include:

* AppShell
* BottomNavigation
* FighterSummary
* PrimaryButton
* SecondaryButton
* IconButton
* SegmentedControl
* MoneyValue
* BattleResult
* UpgradeRow
* RewardRow
* StatusNotice
* EmptyState
* ErrorState
* Disclosure
* BattleLayout

Do not introduce abstraction solely for abstraction's sake.

Create shared components when they provide real visual or interaction consistency.

---

# 29. Interaction states

Interactive components should consider:

* default
* pressed
* selected
* loading
* success
* error
* disabled
* unavailable

Do not treat all non-default states as opacity variations.

---

# 30. Anti-patterns

Avoid unless explicitly justified:

* glassmorphism
* glowing buttons
* gradient-heavy UI
* excessive blur
* giant marketing hero sections
* decorative stars everywhere
* arbitrary oversized numbers
* excessive pills
* nested cards
* colored icon boxes on every row
* tiny informational text
* desktop dashboards squeezed onto mobile
* multiple competing CTA colors
* animation for decoration only

---

# 31. Priority rule

When visual polish conflicts with gameplay clarity:

**gameplay clarity wins.**

When decoration conflicts with readability:

**readability wins.**

When global navigation conflicts with the battle interaction:

**battle interaction wins.**

When adding something does not make the game clearer, more enjoyable, or more distinctive:

**consider removing it.**
