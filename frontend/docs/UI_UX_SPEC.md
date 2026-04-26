# UI/UX Specification: Japandi Quant Dashboard

## 1. Design Philosophy: Japandi
- **Minimalist**: Focus on essential data, remove visual noise.
- **Functional**: Every element must serve a clear purpose in decision making.
- **Serene**: Use a calm, earthy palette to reduce trader stress and cognitive load.

## 2. Color Palette (Tailwind Tokens)

| Token | Hex | Role |
|-------|-----|------|
| `oat` | `#ebe6d6` | Main background color |
| `warm-sand` | `#ded5b9` | Card backgrounds, Sidebar |
| `muted-clay` | `#cec4a7` | Borders, grid lines, secondary text |
| `moss` | `#707e57` | Positive growth, buy signals, success |
| `earth` | `#6b6445` | Primary text, main headers |
| `rust` | `#a66144` | Negative trend, sell signals, warnings |

## 3. Typography
- **Primary Font**: Inter (Sans-serif)
- **Hierarchy**:
  - `Heading`: `text-2xl font-semibold text-earth`
  - `Metric`: `text-3xl font-bold tracking-tight text-earth`
  - `Body`: `text-base text-earth/80`
  - `Label`: `text-sm font-medium text-muted-clay`

## 4. Component Inventory
- **Shadcn/UI**: Used for layout (Sidebar, Tabs), interaction (Buttons, Modals), and data display (Tables).
- **Tremor**: Used for specialized financial visualization:
  - `Metric`: Key indicators (DXY, Copper, Rates).
  - `CategoryBar`: Regime/Risk scores.
  - `AreaChart`: Historical trends (Breadth, Regime).
  - `DonutChart`: Portfolio allocation.

## 5. State Management
- **React Query**: Handles all server-side state (API fetching, caching, loading states).
- **Zustand**: Handles local UI state (theme, active tab, sidebar toggle).

## 6. Layout Rules
- **Grid**: 12-column grid system for desktop.
- **Spacing**: Use standard Tailwind spacing (p-4, gap-6).
- **Shadows**: Subtle, soft shadows only (`shadow-sm`).
