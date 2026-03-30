# Church Budget Tool — Design Specification

**Document**: `design_spec.md`
**Status**: Approved
**Owner**: Jimmy (UI & Design Guardian)
**Version**: 1.0
**Date**: 2026-03-30

---

## 1. Design Token System

All colors and typography are defined as CSS custom properties in a single `tokens.css` file. No raw color values should appear in component markup or Tailwind classes. Every visual decision flows from these tokens.

### 1.1 Core Tokens (CSS Custom Properties)

```css
/* tokens.css */
:root {
  /* ── Surfaces ── */
  --bg: #FAFAFA;             /* Page background */
  --surface: #FFFFFF;        /* Card / panel backgrounds */
  --muted: #E8ECF1;         /* Subdued backgrounds, borders, dividers */

  /* ── Text ── */
  --text: #1A1A2E;          /* Body text, headings */

  /* ── Brand ── */
  --primary: #2C5F8A;       /* Dominant brand — trustworthy blue */
  --accent: #E8913A;        /* CTA / attention — warm amber */

  /* ── Semantic ── */
  --success: #2E7D32;       /* Positive variance, on/under budget */
  --warning: #F9A825;       /* Approaching threshold */
  --danger: #C62828;        /* Over budget, negative variance */

  /* ── Typography ── */
  --font-heading: 'Inter', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
```

**Total custom properties**: 12 (9 color tokens + 3 font tokens).

---

## 2. WCAG AA Contrast Verification

All contrast ratios are calculated using the WCAG 2.1 relative luminance formula:

```
Relative luminance L = 0.2126 * R_lin + 0.7152 * G_lin + 0.0722 * B_lin
Contrast ratio = (L_lighter + 0.05) / (L_darker + 0.05)
```

Where `R_lin`, `G_lin`, `B_lin` are sRGB values linearized via:
- If C_srgb <= 0.04045: C_lin = C_srgb / 12.92
- Else: C_lin = ((C_srgb + 0.055) / 1.055) ^ 2.4

### 2.1 Luminance Values

| Token | Hex | Relative Luminance |
|-------|-----|-------------------|
| `--bg` | #FAFAFA | 0.9547 |
| `--surface` | #FFFFFF | 1.0000 |
| `--text` | #1A1A2E | 0.0098 |
| `--primary` | #2C5F8A | 0.1016 |
| `--accent` | #E8913A | 0.3736 |
| `--success` | #2E7D32 | 0.1524 |
| `--danger` | #C62828 | 0.1358 |
| `--warning` | #F9A825 | 0.4810 |
| `--muted` | #E8ECF1 | 0.8280 |

### 2.2 Contrast Ratio Results

| Pairing | Foreground | Background | Ratio | Requirement | Result |
|---------|-----------|------------|-------|-------------|--------|
| Body text on page | `--text` #1A1A2E | `--bg` #FAFAFA | **16.81:1** | 4.5:1 (AA normal) | PASS |
| Page bg on primary | `--bg` #FAFAFA | `--primary` #2C5F8A | **6.63:1** | 4.5:1 (AA normal) | PASS |
| CTA button text on accent | `--text` #1A1A2E | `--accent` #E8913A | **7.09:1** | 4.5:1 (AA normal) | PASS |
| Success text on surface | `--success` #2E7D32 | `--surface` #FFFFFF | **5.19:1** | 4.5:1 (AA normal) | PASS |
| Danger text on surface | `--danger` #C62828 | `--surface` #FFFFFF | **5.65:1** | 4.5:1 (AA normal) | PASS |
| Warning badge text on warning bg | `--text` #1A1A2E | `--warning` #F9A825 | **8.88:1** | 4.5:1 (AA normal) | PASS |
| Footer text on dark bg | `--bg` #FAFAFA | `--text` #1A1A2E | **16.81:1** | 4.5:1 (AA normal) | PASS |
| Primary text on surface | `--primary` #2C5F8A | `--surface` #FFFFFF | **6.92:1** | 4.5:1 (AA normal) | PASS |
| Text on muted bg | `--text` #1A1A2E | `--muted` #E8ECF1 | **14.68:1** | 4.5:1 (AA normal) | PASS |

### 2.3 Design Decision: CTA Button Text Color

The original spec proposed `--bg` (#FAFAFA) on `--accent` (#E8913A), which yields a contrast ratio of only **2.37:1** — failing WCAG AA at every level. Using `--text` (#1A1A2E) on `--accent` (#E8913A) achieves **7.09:1**, comfortably passing AA for normal text. Therefore:

> **CTA buttons use `--text` as their text color, not `--bg`.**

This is reflected in the component token table below.

---

## 3. Typography Scale

Optimized for data-heavy financial dashboards where scannability and numeric clarity are paramount.

| Element | Size | Weight | Font Stack | CSS Class | Use |
|---------|------|--------|-----------|-----------|-----|
| **H1** | 2rem (32px) | 700 | `var(--font-heading)` | `.text-h1` | Page titles ("Budget Overview") |
| **H2** | 1.5rem (24px) | 600 | `var(--font-heading)` | `.text-h2` | Section headers ("Income", "Expenses") |
| **H3** | 1.25rem (20px) | 600 | `var(--font-heading)` | `.text-h3` | Card titles ("Tithes & Offerings") |
| **Body** | 1rem (16px) | 400 | `var(--font-body)` | `.text-body` | General text, descriptions |
| **Caption** | 0.875rem (14px) | 400 | `var(--font-body)` | `.text-caption` | Labels, metadata, table headers |
| **Small** | 0.75rem (12px) | 400 | `var(--font-body)` | `.text-small` | Timestamps, footnotes |
| **Figures** | 1rem (16px)+ | 500 | `var(--font-mono)` | `.text-figure` | Financial amounts, percentages |
| **Figure-lg** | 1.5rem (24px) | 600 | `var(--font-mono)` | `.text-figure-lg` | Hero KPI numbers |

### 3.1 Typography CSS

```css
.text-h1      { font: 700 2rem/1.2 var(--font-heading); color: var(--text); }
.text-h2      { font: 600 1.5rem/1.3 var(--font-heading); color: var(--text); }
.text-h3      { font: 600 1.25rem/1.3 var(--font-heading); color: var(--text); }
.text-body    { font: 400 1rem/1.5 var(--font-body); color: var(--text); }
.text-caption { font: 400 0.875rem/1.4 var(--font-body); color: var(--text); }
.text-small   { font: 400 0.75rem/1.4 var(--font-body); color: var(--text); opacity: 0.7; }
.text-figure  { font: 500 1rem/1.4 var(--font-mono); color: var(--text); }
.text-figure-lg { font: 600 1.5rem/1.2 var(--font-mono); color: var(--text); }
```

### 3.2 Font Loading

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

---

## 4. Component Token Table

Every UI element maps exclusively to design tokens. No raw hex values in markup.

| Component | Background | Text | Border / Accent | Additional Notes |
|-----------|-----------|------|-----------------|------------------|
| **Page** | `var(--bg)` | `var(--text)` | -- | `min-height: 100vh` |
| **Nav** | `var(--surface)` | `var(--text)` | `var(--primary)` bottom border | `position: sticky; top: 0; z-index: 50; shadow-sm` |
| **Dashboard Card** | `var(--surface)` | `var(--text)` | `var(--muted)` 1px border | `border-radius: 8px; shadow-sm; padding: 1.5rem` |
| **KPI Card** | `var(--surface)` | `var(--text)` | `var(--primary)` left-4px accent | Hero figure uses `.text-figure-lg` |
| **Budget Table** | `var(--bg)` | `var(--text)` | `var(--muted)` row borders | Striped rows: odd `var(--surface)`, even `var(--bg)` |
| **Table Header** | `var(--muted)` | `var(--text)` | -- | `.text-caption`, `font-weight: 600`, `text-transform: uppercase` |
| **Positive Variance** | `var(--surface)` | `var(--success)` | -- | Prefix with "+" sign, mono font |
| **Negative Variance** | `var(--surface)` | `var(--danger)` | -- | Prefix with "-" sign, mono font |
| **Warning Badge** | `var(--warning)` | `var(--text)` | -- | `border-radius: 9999px; padding: 2px 10px; font-size: 0.75rem` |
| **Success Badge** | `var(--success)` | `#FFFFFF` | -- | Same shape as warning badge |
| **Danger Badge** | `var(--danger)` | `#FFFFFF` | -- | Same shape as warning badge |
| **CTA Button** | `var(--accent)` | `var(--text)` | -- | `border-radius: 6px; min-height: 44px; padding: 0.625rem 1.5rem; font-weight: 600` |
| **Secondary Button** | `transparent` | `var(--primary)` | `var(--primary)` 1px border | `border-radius: 6px; min-height: 44px; padding: 0.625rem 1.5rem` |
| **Ghost Button** | `transparent` | `var(--primary)` | -- | Hover: `background: var(--muted)` |
| **Input Field** | `var(--surface)` | `var(--text)` | `var(--muted)` 1px border | Focus: `border-color: var(--primary); ring: var(--primary)/20%` |
| **Sidebar / Filter Panel** | `var(--surface)` | `var(--text)` | `var(--muted)` right border | Width: 280px on desktop, drawer on mobile |
| **Footer** | `var(--text)` | `var(--bg)` | -- | `padding: 1.5rem; text-align: center` |
| **Tooltip** | `var(--text)` | `var(--bg)` | -- | `border-radius: 4px; font-size: 0.75rem; max-width: 200px` |
| **Skeleton Loader** | `var(--muted)` | -- | -- | Animated pulse, same dimensions as target element |

---

## 5. Spacing & Layout

Consistent spacing uses a 4px base unit. The dashboard follows an 8-point grid.

| Token | Value | Use |
|-------|-------|-----|
| `--space-1` | 0.25rem (4px) | Tight gaps (icon-to-text) |
| `--space-2` | 0.5rem (8px) | Compact padding |
| `--space-3` | 0.75rem (12px) | List gaps |
| `--space-4` | 1rem (16px) | Standard padding |
| `--space-6` | 1.5rem (24px) | Card padding, section gaps |
| `--space-8` | 2rem (32px) | Section separators |
| `--space-12` | 3rem (48px) | Page-level margins |

### 5.1 Grid

- **Desktop** (>=1024px): 12-column grid, `gap: 1.5rem`
- **Tablet** (>=768px): 6-column grid, `gap: 1rem`
- **Mobile** (<768px): Single column stack, `gap: 1rem`

Dashboard cards should be arranged in a responsive grid: 3 columns on desktop, 2 on tablet, 1 on mobile.

---

## 6. Chart Color Palette (Superman Collaboration)

For Chart.js visualizations, use the following token-mapped palette:

| Purpose | Token | Hex | Chart.js Usage |
|---------|-------|-----|---------------|
| Series 1 (Budget) | `var(--primary)` | #2C5F8A | `borderColor`, `backgroundColor` with alpha |
| Series 2 (Actuals) | `var(--accent)` | #E8913A | `borderColor`, `backgroundColor` with alpha |
| Positive / Under Budget | `var(--success)` | #2E7D32 | Bar fills, annotations |
| Negative / Over Budget | `var(--danger)` | #C62828 | Bar fills, annotations |
| Neutral / Baseline | `var(--muted)` | #E8ECF1 | Grid lines, zero-line |

### 6.1 Chart.js Token Integration

```javascript
// Read CSS custom properties at runtime for Chart.js
function getToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const chartColors = {
  primary: getToken('--primary'),
  accent: getToken('--accent'),
  success: getToken('--success'),
  danger: getToken('--danger'),
  muted: getToken('--muted'),
  text: getToken('--text'),
  surface: getToken('--surface'),
};

// Apply to Chart.js defaults
Chart.defaults.color = chartColors.text;
Chart.defaults.borderColor = chartColors.muted;
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
```

### 6.2 Chart Typography

- **Chart title**: 14px, weight 600, `var(--text)`
- **Axis labels**: 12px, weight 400, `var(--text)`, opacity 0.7
- **Tooltip**: 12px, `var(--bg)` on `var(--text)` background, `border-radius: 4px`
- **Legend**: 12px, weight 400, color squares 12x12px

---

## 7. Tailwind CSS Configuration

Tailwind is configured to consume CSS custom properties. No raw color values appear in utility classes.

```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        text: 'var(--text)',
        primary: 'var(--primary)',
        accent: 'var(--accent)',
        muted: 'var(--muted)',
        surface: 'var(--surface)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        danger: 'var(--danger)',
      },
      fontFamily: {
        heading: ['var(--font-heading)'],
        body: ['var(--font-body)'],
        mono: ['var(--font-mono)'],
      },
      fontSize: {
        'h1': ['2rem', { lineHeight: '1.2', fontWeight: '700' }],
        'h2': ['1.5rem', { lineHeight: '1.3', fontWeight: '600' }],
        'h3': ['1.25rem', { lineHeight: '1.3', fontWeight: '600' }],
        'caption': ['0.875rem', { lineHeight: '1.4', fontWeight: '400' }],
        'figure': ['1rem', { lineHeight: '1.4', fontWeight: '500' }],
        'figure-lg': ['1.5rem', { lineHeight: '1.2', fontWeight: '600' }],
      },
      borderRadius: {
        'card': '8px',
        'btn': '6px',
        'badge': '9999px',
      },
      minHeight: {
        'btn': '44px',
      },
      boxShadow: {
        'card': '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)',
      },
    }
  }
}
</script>
```

### 7.1 Usage Examples

```html
<!-- Page background -->
<body class="bg-bg text-text font-body">

<!-- Nav -->
<nav class="bg-surface text-text border-b-2 border-primary sticky top-0 z-50 shadow-card">

<!-- Dashboard card -->
<div class="bg-surface border border-muted rounded-card shadow-card p-6">
  <h3 class="font-heading text-h3">Tithes & Offerings</h3>
  <p class="font-mono text-figure-lg text-success">+$12,450</p>
</div>

<!-- CTA button -->
<button class="bg-accent text-text font-semibold rounded-btn min-h-btn px-6">
  Export Report
</button>

<!-- Secondary button -->
<button class="border border-primary text-primary rounded-btn min-h-btn px-6 hover:bg-muted">
  View Details
</button>

<!-- Warning badge -->
<span class="bg-warning text-text rounded-badge px-3 py-0.5 text-xs font-medium">
  85% spent
</span>

<!-- Budget table -->
<table class="w-full border-collapse">
  <thead class="bg-muted">
    <tr>
      <th class="text-caption font-semibold uppercase text-left px-4 py-2">Account</th>
      <th class="text-caption font-semibold uppercase text-right px-4 py-2">Budget</th>
      <th class="text-caption font-semibold uppercase text-right px-4 py-2">Actual</th>
      <th class="text-caption font-semibold uppercase text-right px-4 py-2">Variance</th>
    </tr>
  </thead>
  <tbody>
    <tr class="odd:bg-surface even:bg-bg border-b border-muted">
      <td class="px-4 py-3">Pastoral Salary</td>
      <td class="px-4 py-3 text-right font-mono">$5,000</td>
      <td class="px-4 py-3 text-right font-mono">$5,000</td>
      <td class="px-4 py-3 text-right font-mono text-success">$0</td>
    </tr>
  </tbody>
</table>

<!-- Footer -->
<footer class="bg-text text-bg py-6 text-center text-caption">
  &copy; 2026 Church Name. All rights reserved.
</footer>
```

---

## 8. White-Label Override Instructions

The design system is built for easy rebranding. Any church can restyle the entire application by providing a single CSS file that overrides the token values.

### 8.1 How It Works

1. The application loads `tokens.css` first, which sets the `:root` custom properties.
2. A church-specific `theme-override.css` is loaded immediately after, overriding any tokens.
3. Because all components reference tokens (not raw values), the entire UI updates automatically.

### 8.2 Load Order

```html
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/theme-override.css">  <!-- church-specific -->
<link rel="stylesheet" href="/static/css/components.css">
```

### 8.3 Example Override File

```css
/* theme-override.css — Example: "Grace Community Church" */
:root {
  --primary: #1B5E20;        /* Forest green brand */
  --accent: #FF8F00;         /* Gold accent */
  --font-heading: 'Merriweather', Georgia, serif;
}
```

Only the tokens that differ need to be declared. All other tokens inherit from `tokens.css`.

### 8.4 Override Checklist

When creating a `theme-override.css`, verify:

- [ ] `--primary` and `--accent` still pass WCAG AA contrast against `--surface` and `--bg`
- [ ] `--success`, `--warning`, `--danger` are semantically appropriate (green/yellow/red family)
- [ ] Heading font is loaded via Google Fonts or self-hosted
- [ ] Test on both desktop and mobile viewports
- [ ] Run a contrast checker on any new color combinations

### 8.5 Advanced: Multi-Tenant Configuration

For a hosted multi-tenant deployment, the theme override path can be resolved dynamically:

```python
# FastAPI: resolve theme per church
@app.middleware("http")
async def theme_middleware(request: Request, call_next):
    church_slug = request.headers.get("X-Church-Slug", "default")
    request.state.theme_css = f"/static/themes/{church_slug}/theme-override.css"
    response = await call_next(request)
    return response
```

```html
<!-- Jinja2 template -->
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="{{ request.state.theme_css }}">
```

---

## 9. Interaction States

All interactive elements must have visible state changes for accessibility and usability.

| State | Treatment |
|-------|-----------|
| **Hover** | Slight background shift or opacity change (0.9). Buttons: darken background 10%. |
| **Focus** | 2px ring in `var(--primary)` with 3px offset. Never remove outline. |
| **Active** | Scale to 0.98 for tactile feedback on buttons. |
| **Disabled** | Opacity 0.5, `cursor: not-allowed`, no pointer events. |
| **Loading** | Skeleton pulse animation using `var(--muted)` to `var(--bg)` gradient. |

### 9.1 Focus Ring CSS

```css
*:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 3px;
}
```

---

## 10. Responsive Breakpoints

| Name | Min Width | Tailwind Prefix | Target |
|------|-----------|-----------------|--------|
| Mobile | 0px | (default) | Phones |
| Tablet | 768px | `md:` | Tablets, small laptops |
| Desktop | 1024px | `lg:` | Standard laptops |
| Wide | 1280px | `xl:` | Large monitors |

### 10.1 Dashboard Layout Rules

- KPI cards: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`
- Budget tables: horizontal scroll on mobile, full-width on desktop
- Charts: min-height 300px, responsive via Chart.js `maintainAspectRatio: false`
- Nav: horizontal on desktop, hamburger drawer on mobile

---

## 11. Implementation Checklist for Felicity

- [ ] Create `/static/css/tokens.css` with all custom properties from Section 1
- [ ] Configure Tailwind per Section 7
- [ ] Load Inter and JetBrains Mono fonts per Section 3.2
- [ ] Apply component tokens per Section 4 — no raw hex values
- [ ] Implement focus ring globally per Section 9.1
- [ ] Set up Chart.js token integration per Section 6.1
- [ ] Create `/static/css/theme-override.css` as empty placeholder
- [ ] Verify all contrast ratios hold after any token changes
- [ ] Test responsive breakpoints per Section 10

---

*This specification is the single source of truth for all visual decisions in the Church Budget Tool. Any deviation requires Jimmy's review.*
