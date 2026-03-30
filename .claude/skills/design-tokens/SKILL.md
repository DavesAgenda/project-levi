---
name: design-tokens
description: CSS custom property design system for New Light Anglican Church budget dashboard — tokens, Tailwind config, WCAG AA, component mapping
metadata:
  internal: false
---

# Design Token System

Jimmy's skill for establishing the visual language of the church budget tool. All UI styling flows from this token system.

## Brand: New Light Anglican Church
Source: https://newlightanglican.church/brand/

## Core Tokens

```css
/* static/tokens.css */
:root {
  /* Surfaces */
  --bg: #FFFFFF;
  --surface: #FFFFFF;
  --muted: #e8e9eb;

  /* Text */
  --text: #313638;          /* Headings — dark charcoal */
  --text-body: #000000;     /* Body text */

  /* Brand */
  --primary: #ff7300;       /* New Light orange */
  --primary-hover: #ff9d4c;
  --accent: #ff8a2a;        /* Light orange */
  --secondary: #e0dfd5;     /* Cream */

  /* Financial semantic */
  --success: #2E7D32;
  --warning: #F9A825;
  --danger: #C62828;

  /* Neutral */
  --outline: #60695c;
  --neutral: #747d7d;

  /* Typography */
  --font-heading: 'Inter Display', 'Inter', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
```

## White-Label Override

Any church can restyle by providing a single override file:

```css
/* static/theme-override.css — loaded AFTER tokens.css */
:root {
  --primary: #8B0000;      /* Their brand color */
  --accent: #DAA520;
  /* Only override what differs — defaults cascade */
}
```

**This is the only restyling mechanism.** No Tailwind color utilities, no inline hex codes.

## Tailwind Configuration

```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        text: 'var(--text)',
        'text-body': 'var(--text-body)',
        primary: 'var(--primary)',
        'primary-hover': 'var(--primary-hover)',
        accent: 'var(--accent)',
        secondary: 'var(--secondary)',
        muted: 'var(--muted)',
        surface: 'var(--surface)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        danger: 'var(--danger)',
        outline: 'var(--outline)',
        neutral: 'var(--neutral)',
      },
      fontFamily: {
        heading: ['var(--font-heading)'],
        body: ['var(--font-body)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        'card': '12px',
        'btn': '8px',
        'badge': '999px',
      }
    }
  }
}
</script>
```

Usage: `bg-surface`, `text-primary`, `border-muted` — never `bg-white`, `text-blue-700`.

## Typography Scale

| Element | Size | Weight | Font | Use |
|---------|------|--------|------|-----|
| H1 | 2rem (32px) | 900 | heading (Inter Display) | Page titles |
| H2 | 1.5rem (24px) | 700 | heading (Inter Display) | Section headers |
| H3 | 1.25rem (20px) | 600 | heading (Inter Display) | Card titles |
| Body | 1rem (16px) | 400 | body (Inter) | General text |
| Caption | 0.875rem (14px) | 400 | body (Inter) | Labels, metadata |
| Figures | 1rem+ | 500 | mono (JetBrains Mono) | Financial amounts |

Financial figures use monospace for tabular alignment.

## Component Token Table

| Component | Background | Text | Border/Accent | Notes |
|-----------|-----------|------|--------------|-------|
| Page | `var(--bg)` | `var(--text)` | — | |
| Nav | `var(--surface)` | `var(--text)` | `var(--primary)` | Sticky top, logo + title |
| KPI Card | `var(--surface)` | `var(--text)` | `var(--muted)` | Left accent border, shadow |
| Sortable Table | `var(--bg)` | `var(--text)` | `var(--muted)` | Zebra rows, pinned summary |
| Chart Card | `var(--surface)` | `var(--text)` | `var(--muted)` | Canvas wrapper |
| Positive Variance | `var(--surface)` | `var(--success)` | — | Green text |
| Negative Variance | `var(--surface)` | `var(--danger)` | — | Red text |
| CTA Button | `var(--primary)` | `white` | — | Rounded-btn, min 44px height |
| Secondary Button | transparent | `var(--primary)` | `var(--primary)` | Outlined |
| Footer | `var(--text)` | `var(--bg)` | — | |

## Chart Colors (Superman Collaboration)

Chart series colors derive from tokens. Read at runtime via `getComputedStyle`:
- Series 1: `var(--primary)` — `#ff7300`
- Series 2: `var(--accent)` — `#ff8a2a`
- Positive: `var(--success)`
- Negative: `var(--danger)`
- Neutral: `var(--muted)`
- Additional series: `hexToRgba(color, opacity)` variations

## Rules
- **Never** use raw hex in templates — only `var(--token)` or Tailwind token classes
- **Never** use Tailwind built-in colors (`bg-blue-500`) — only extended token colors
- Every new component must be added to the Component Token Table
- Financial figures always use `font-mono`
- All color pairings must pass WCAG AA before deployment
