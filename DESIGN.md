---
name: Edna Search
description: Product launch page for capital formation research routing.
colors:
  bg: "oklch(0.978 0.006 145)"
  surface: "oklch(0.995 0.003 145)"
  surface-muted: "oklch(0.957 0.008 145)"
  surface-rail: "oklch(0.99 0.004 145)"
  surface-input: "oklch(0.998 0.002 145)"
  table-header: "oklch(0.957 0.008 145)"
  ink: "oklch(0.205 0.025 155)"
  ink-soft: "oklch(0.435 0.028 150)"
  line: "oklch(0.87 0.012 145)"
  line-strong: "oklch(0.735 0.018 145)"
  forest: "oklch(0.34 0.075 156)"
  forest-soft: "oklch(0.91 0.047 150)"
  brass: "oklch(0.47 0.025 150)"
  rust: "oklch(0.51 0.112 38)"
  blue: "oklch(0.43 0.086 245)"
  warning-bg: "oklch(0.94 0.047 78)"
  warning-ink: "oklch(0.4 0.087 55)"
  error-bg: "oklch(0.93 0.04 36)"
  error-border: "oklch(0.67 0.113 32)"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, Avenir Next, Segoe UI, Helvetica, Arial, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.08rem"
    fontWeight: 760
    lineHeight: 1.1
    letterSpacing: "0"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Avenir Next, Segoe UI, Helvetica, Arial, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.18rem"
    fontWeight: 760
    lineHeight: 1.08
    letterSpacing: "0"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Avenir Next, Segoe UI, Helvetica, Arial, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 760
    lineHeight: 1.35
    letterSpacing: "0"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Avenir Next, Segoe UI, Helvetica, Arial, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.88rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Avenir Next, Segoe UI, Helvetica, Arial, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 760
    lineHeight: 1.2
    letterSpacing: "0"
  mono:
    fontFamily: "SFMono-Regular, SF Mono, Consolas, Liberation Mono, ui-monospace, monospace"
    fontSize: "0.78rem"
    fontWeight: 400
    lineHeight: 1.35
    letterSpacing: "0"
rounded:
  md: "8px"
  pill: "999px"
  icon: "12px"
spacing:
  space-1: "0.25rem"
  space-2: "0.5rem"
  space-3: "0.75rem"
  space-4: "1rem"
  space-5: "1.25rem"
  space-6: "1.5rem"
  space-8: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.forest}"
    textColor: "{colors.surface}"
    typography: "{typography.title}"
    rounded: "{rounded.md}"
    padding: "0 0.75rem"
    height: "48px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.title}"
    rounded: "{rounded.md}"
    padding: "0 0.75rem"
    height: "40px"
  input-field:
    backgroundColor: "{colors.surface-input}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "0 0.75rem"
    height: "42px"
  chip-selected:
    backgroundColor: "{colors.forest-soft}"
    textColor: "{colors.forest}"
    typography: "{typography.title}"
    rounded: "{rounded.pill}"
    padding: "0 0.75rem"
    height: "34px"
  metric-tile:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.ink}"
    typography: "{typography.mono}"
    rounded: "{rounded.md}"
    padding: "{spacing.space-3}"
---

# Design System: Edna Search

## 1. Overview

**Creative North Star: "Cited Launch Surface"**

Edna Search should feel like a precise capital-markets product launch, not raw internal tooling. The page is light, minimal, confident, and product-led: a direct hero claim, a visible product proof surface, tight provider credibility, and a short narrative about routing, citations, and exports.

The reference triangle is deliberate: Linear for density, typography, crisp figures, and control ergonomics; Lightfield for minimal product language and systems-minded primitives; Rogo for private-capital language, institutional trust, and finance-specific positioning.

The system rejects generic AI-chat aesthetics, oversized SaaS heroes, neon gradients, glassmorphism, decorative card grids, noisy chrome, and hidden provider logic. The first screen is the launch proof: product claim, demo CTA, product frame, and provider credibility.

**Key Characteristics:**

- Product launch page first, workbench proof second.
- Green-tinted neutral surfaces with forest as the primary action color, blue for cited evidence, and clay for numbered narrative details.
- Large confident display type, thin product rules, compact proof modules, and visible focus states.
- Short sections that explain why routing, confidence, citations, and exports belong together.

## 2. Colors

The palette is a restrained product system: tinted neutrals carry most of the screen, forest marks control and selection, rust handles risk, and blue is reserved for links.

### Primary

- **Ledger Forest** (`forest`): The primary action, selected route, live state, confidence chip, icon, and focus color. Its role is control, not decoration.
- **Soft Forest Wash** (`forest-soft`): The selected chip and positive status wash. Use it behind forest text when a filled control would be too loud.

### Secondary

- **Brass Marker** (`brass`): Eyebrows, rail labels, and small metadata accents. Use it as a signpost, never as a button background.
- **Source Blue** (`blue`): Link and source-opening color. It should only appear where the user can navigate or inspect evidence.

### Tertiary

- **Rust Risk** (`rust`): Error and warning severity. Pair it with soft warm error backgrounds and clear text, not saturated alert blocks.
- **Amber Warning** (`warning-bg`, `warning-ink`): Warning stack surfaces for demo mode, missing keys, or non-blocking run caveats.

### Neutral

- **Warm Ledger Background** (`bg`): The page canvas, slightly gridded, never pure white.
- **Paper Surface** (`surface`): Primary panels and controls.
- **Muted Paper** (`surface-muted`): Secondary panels, metric tiles, icon wells, chips, and inactive blocks.
- **Deep Ink** (`ink`): Main text and primary neutral controls.
- **Soft Ink** (`ink-soft`): Supporting labels, descriptions, disabled explanations, and low-priority metadata.
- **Ruled Lines** (`line`, `line-strong`): Panel division, table rules, input strokes, and component borders.

### Named Rules

**The Evidence Color Rule.** Blue belongs to sources and navigable citations. Forest belongs to operation and selection. Do not swap them.

**The No Pure Neutral Rule.** Never introduce `#000` or `#fff`; all neutrals stay gently warm or green-tinted.

## 3. Typography

**Display Font:** System sans, with Avenir Next and Segoe UI fallbacks
**Body Font:** System sans, with Avenir Next and Segoe UI fallbacks
**Label/Mono Font:** SFMono-Regular, with SF Mono, Consolas, Liberation Mono, and ui-monospace fallbacks

**Character:** A single tuned sans stack keeps the workbench native, compact, and credible. Hierarchy comes from spacing, weight, and alignment, not decorative type.

### Hierarchy

- **Display** (760, `1.08rem`, 1.1): Brand block title only.
- **Headline** (760, `1.18rem`, 1.08): Panel titles and empty-state titles.
- **Title** (760, `1rem`, 1.35): Buttons, selected states, provider names, and compact section values.
- **Body** (400, `0.88rem`, 1.5): Data cells, descriptions, help text, warnings, and control copy.
- **Label** (760, `0.72rem`, uppercase where already established): Rail labels, panel eyebrows, table headings, and compact metadata.
- **Mono** (400, `0.78rem`, 1.35): Costs, latency, provider metrics, route estimates, and other figures that must scan as data.

### Named Rules

**The Sans Restraint Rule.** Use one system sans stack across the app. Never use decorative display type in buttons, labels, metrics, or data tables.

**The Figure Clarity Rule.** Costs, latency, provider scores, and compact estimates use the mono stack so figures line up and read as audit data.

## 4. Elevation

Edna Search is flat by default. Depth comes from ruled borders, tonal layering, sticky table headers, and one ambient shadow on the route brief. Shadows should feel like paper lift, not glass or floating cards.

### Shadow Vocabulary

- **Route Brief Ambient Lift** (`0 24px 80px oklch(0.2 0.025 155 / 0.09)`): Reserved for the current route summary in the rail and rare anchored overlays.
- **Focus Ring** (`0 0 0 3px oklch(0.34 0.075 156 / 0.18)`): Required for keyboard focus on controls and fields.

### Named Rules

**The Ruled Surface Rule.** Use borders and tonal changes before shadows. If a shadow appears on a repeated table, metric tile, or provider list item, it is probably too much.

## 5. Components

### Buttons

- **Shape:** Compact rectangular controls with 8px corners.
- **Primary:** Forest fill, paper text, 48px run-button height, icon plus text when the action benefits from recognition.
- **Hover / Focus:** Keep hover subtle; focus must use the forest 3px ring and visible border shift.
- **Secondary / Export:** Paper background, strong rule border, ink text, 40px minimum height. Disabled states reduce opacity but keep shape stable.

### Chips

- **Style:** Pill-shaped field filters with border-first inactive state and forest-soft selected state.
- **State:** Selected chips use forest text and heavier weight. Inactive chips use soft ink and transparent fill.

### Cards / Containers

- **Corner Style:** 8px corners throughout operational containers.
- **Background:** Paper surface for primary panels, muted paper for secondary wells and metric tiles.
- **Shadow Strategy:** Flat by default. Only anchored summaries may use the ambient route shadow.
- **Border:** 1px ruled borders define panel and component edges.
- **Internal Padding:** Use `space-3` to `space-5` for controls and compact panels; use `space-6` to `space-8` only for main panel padding.

### Inputs / Fields

- **Style:** Warm input surface, 1px strong border, 8px corners, ink text.
- **Focus:** Forest border plus 3px translucent forest ring.
- **Error / Disabled:** Errors use rust text with warm error surface and explicit copy. Disabled controls reduce opacity without shifting dimensions.

### Navigation

- **Style:** The public page uses a minimal sticky top navigation with brand, section anchors, and a single demo CTA.
- **Mobile Treatment:** Hide section anchors and keep brand plus demo CTA visible.

### Signature Component

The product proof frame is the center of gravity. It should show the workflow in miniature: brief, route, cost, confidence, provider, and cited rows. It is a launch-page visual, not the full app shell.

## 6. Do's and Don'ts

### Do:

- **Do** keep the first viewport as a true launch page: claim, CTA, and product proof.
- **Do** use forest for selected controls, run actions, focus rings, confidence chips, and live states.
- **Do** preserve compact 8px controls, precise borders, and table-first density.
- **Do** expose provider, cost, confidence, citations, warnings, and exports near the workflow they affect.
- **Do** keep reduced-motion support and visible keyboard focus on every interactive element.

### Don't:

- **Don't** use generic AI-chat aesthetics, oversized SaaS heroes, neon gradients, glassmorphism, decorative card grids, or noisy chrome.
- **Don't** expose the raw operator workbench as the homepage hero.
- **Don't** hide provider choice, confidence, citations, warnings, or export affordances behind theatrical flows.
- **Don't** use `border-left` or `border-right` greater than 1px as colored accents on cards, list items, callouts, or alerts.
- **Don't** introduce pure black, pure white, gradient text, decorative motion, or full-saturation inactive states.
