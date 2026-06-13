# Chromatic Effects — Usage Guide & Decision Matrix

## Quick Decision: Which Effect to Use?

### Visual Impact Level Matrix

```
┌─────────────────────────────────────────────────────────────────┐
│                     VISUAL IMPACT                                │
│                                                                   │
│   HIGH   │  Liquid Metal Panel  │  Spectral Ring               │
│          │  Conic Border        │                              │
│          ├──────────────────────┼──────────────────────────────┤
│   MED    │  Void-to-Prism Card  │  Oil-Slick Border           │
│          │  Button Hover        │                              │
│          ├──────────────────────┼──────────────────────────────┤
│   LOW    │  Ambient Glow        │  Subtle Glows               │
│          │  (background only)   │                              │
│          └──────────────────────┴──────────────────────────────┘
             Subtle              |              Animated
                                 |
```

---

## Effect Selection by Use Case

### Hero/Landing Page Elements

**Best Choice:** `chromatic-liquid-metal` or `chromatic-border-conic`

- **Why:** High visual impact, premium feel
- **Placement:** Hero CTAs, feature highlights, founder quotes
- **Animation Speed:** 5-6 seconds (slow, contemplative)
- **Opacity:** 0.6-1.0 (visible)

**Example:**
```html
<section class="hero">
  <div class="chromatic-liquid-metal">
    <h1>Welcome to OpenJarvis</h1>
    <button class="chromatic-button-void">Start Now</button>
  </div>
</section>
```

---

### Chat/Message Interface Cards

**Best Choice:** `chromatic-void-card` or subtle `chromatic-border-conic`

- **Why:** Doesn't distract from message content, activates on hover
- **Placement:** AI responses, user bubbles (light version)
- **Animation Speed:** 3-4 seconds (medium)
- **Opacity:** 0 at rest, 1 on hover (appears on interaction)

**Example:**
```html
<div className="chat-message">
  <div className="chromatic-void-card">
    <p>{message.content}</p>
  </div>
</div>
```

---

### Navigation/UI Controls

**Best Choice:** `chromatic-button-void` for buttons, `chromatic-void-card` for nav items

- **Why:** Clear focus state, activates on hover, doesn't block interaction
- **Placement:** Primary CTAs, nav links, command palette
- **Animation Speed:** 2-3 seconds (fast, responsive)
- **Opacity:** 0 at rest, 1 on hover

**Example:**
```html
<nav>
  <button class="chromatic-button-void">Dashboard</button>
  <button class="chromatic-button-void">Settings</button>
  <button class="chromatic-button-void">Help</button>
</nav>
```

---

### Data/Analytics Dashboard

**Best Choice:** `chromatic-void-card` + `chromatic-ambient-glow`

- **Why:** Frames data without distraction, ambient glow adds atmosphere
- **Placement:** Metric cards, chart containers, summary panels
- **Animation Speed:** 4-5 seconds (slow background), 3 seconds (card hover)
- **Opacity:** 0.02-0.06 for glow (barely visible), 1 on card hover

**Example:**
```html
<div class="chromatic-ambient-glow" />

<div class="dashboard">
  <div class="chromatic-void-card">
    <h3>Active Users</h3>
    <p className="metric">2,847</p>
  </div>
  <div class="chromatic-void-card">
    <h3>Revenue</h3>
    <p className="metric">$47,293</p>
  </div>
</div>
```

---

### Showcase/Portfolio Elements

**Best Choice:** `chromatic-liquid-metal` or `chromatic-spectral-ring`

- **Why:** Premium, eye-catching, fits portfolio aesthetic
- **Placement:** Project cards, featured work, testimonials
- **Animation Speed:** 5-8 seconds (slow, premium feel)
- **Opacity:** 0.5-1.0 (always visible)

**Example:**
```html
<div class="portfolio-project chromatic-liquid-metal">
  <img src="project.jpg" />
  <h3>FreightX Platform</h3>
  <p>SaaS marketplace for 3 Aces Trucking</p>
</div>
```

---

### Attention-Grabbing Alerts/Notifications

**Best Choice:** `chromatic-spectral-border` (oil-slick effect)

- **Why:** Moving animation catches eye, indicates active state
- **Placement:** Alerts, warnings, real-time indicators
- **Animation Speed:** 4-6 seconds (moderate)
- **Opacity:** 0.7-1.0 (must be visible)

**Example:**
```html
<div class="alert chromatic-spectral-border">
  <span className="icon">⚠</span>
  <p>System update available</p>
</div>
```

---

### Background/Atmosphere (Page-Wide)

**Best Choice:** `chromatic-ambient-glow` (always)

- **Why:** Adds atmosphere without distraction, all effects benefit from it
- **Placement:** One per page, at root level, z-index: 0
- **Animation Speed:** 20 seconds (very slow, ambient)
- **Opacity:** 0.01-0.06 (barely noticeable)

**Example:**
```html
<body>
  <div className="chromatic-ambient-glow" />
  {/* All other content */}
</body>
```

---

## Effect Combinations (What Works Together)

### Golden Combo: Maximum Premium Feel
```
Ambient Glow (background)
+ Liquid Metal Panel (hero)
+ Void-to-Prism Cards (content)
+ Button Void (CTAs)
```
**Use for:** Brand hero, premium showcases, flagship features

---

### Balanced Combo: Professional Without Overwhelming
```
Ambient Glow (background, subtle)
+ Void-to-Prism Cards (hover-activated)
+ Button Void (CTAs)
```
**Use for:** SaaS dashboards, admin interfaces, chat apps

---

### High-Energy Combo: Attention-Grabbing
```
Ambient Glow (background)
+ Conic Border (elements)
+ Spectral Border (alerts)
+ Liquid Metal (hero CTA)
```
**Use for:** Event pages, product launches, calls to action

---

### Minimal Combo: Subtle Sophistication
```
Ambient Glow (background only, very subtle)
+ Void-to-Prism Cards (hover only)
```
**Use for:** Enterprise tools, conservative brands, accessibility-first

---

## Performance Guidelines by Context

### Desktop High-Performance (3+ effects)
```css
/* Can handle all animations simultaneously */
.chromatic-ambient-glow { animation: ambient-glow-shift 20s ease-in-out infinite; }
.chromatic-void-card:hover::before { animation: spectral-travel 3s linear infinite; }
.chromatic-button-void:hover::before { animation: spectral-travel 2s linear infinite; }
```

### Mobile or Low-Power (1-2 effects max)
```css
/* Disable background animations on mobile */
@media (max-width: 768px) {
  .chromatic-ambient-glow {
    animation: none !important;
    opacity: 0.25; /* Static, dimmed */
  }

  /* Keep only hover effects */
  .chromatic-void-card:hover::before {
    animation: spectral-travel 3s linear infinite;
  }
}
```

### Reduced Motion (Accessibility)
```css
@media (prefers-reduced-motion: reduce) {
  /* All animations become static, 50% opacity */
  .chromatic-border-conic::before,
  .chromatic-void-card::before,
  .chromatic-liquid-metal::before,
  .chromatic-spectral-border::before,
  .chromatic-button-void::before,
  .chromatic-ambient-glow {
    animation: none !important;
    opacity: 0.5;
  }
}
```

---

## Color Customization by Brand

### For Cyan-Forward Brands (like OpenJarvis)
Use as-is. The palette is optimized for cyan (#00e5ff) + violet (#8c00ff).

```
Primary spectrum: #00e5ff → #8c00ff → #ff0080
```

### For Red/Warm Brands
```css
Replace spectrum with:
#ff0080 → #ff6d00 → #ffea00
```

### For Blue-Only Brands
```css
Replace spectrum with:
#0080ff → #00e5ff → #40efff
```

### For Multi-Brand (Custom Palette)
```css
.chromatic-border-conic::before {
  background: conic-gradient(
    from var(--spectral-hue),
    #YOUR-COLOR-1 0deg,
    #YOUR-COLOR-2 60deg,
    #YOUR-COLOR-3 120deg,
    #YOUR-COLOR-4 180deg,
    #YOUR-COLOR-5 240deg,
    #YOUR-COLOR-6 300deg,
    #YOUR-COLOR-1 360deg
  );
}
```

---

## Animation Speed by Context

### Very Fast (1-2 seconds)
**Use when:** Button hover, quick feedback, user interaction
```css
animation: spectral-travel 2s linear infinite;
```

### Fast (2-3 seconds)
**Use when:** Navigation, UI controls, interactive elements
```css
animation: spectral-travel 3s linear infinite;
```

### Medium (4-6 seconds)
**Use when:** Card hovers, featured content, moderate emphasis
```css
animation: spectral-travel 5s linear infinite;
```

### Slow (8-10 seconds)
**Use when:** Hero elements, premium feel, background glows
```css
animation: spectral-travel 8s ease-in-out infinite;
```

### Very Slow (15-20 seconds)
**Use when:** Background only, ambient atmosphere
```css
animation: ambient-glow-shift 20s ease-in-out infinite;
```

---

## Opacity Guidelines by Context

| Context | At Rest | Hover | Purpose |
|---------|---------|-------|---------|
| **Background glow** | 0.02–0.06 | N/A | Barely noticeable atmosphere |
| **Card border** | 0–0.2 | 0.8–1.0 | Reveal on interaction |
| **Button border** | 0 | 1.0 | Activation feedback |
| **Always-visible effect** | 0.5–0.6 | 0.8–1.0 | Constant but not overwhelming |
| **Hero element** | 0.7–1.0 | 1.0 | Maximum impact |
| **Alert/notification** | 0.8–1.0 | 1.0 | Must be visible |

---

## Layout Integration Patterns

### Pattern 1: Full-Page with Background Glow
```html
<html>
  <body>
    <div class="chromatic-ambient-glow" />
    <!-- All content overlays -->
  </body>
</html>
```

### Pattern 2: Card Grid with Hover Effects
```html
<div class="card-grid">
  <div class="chromatic-void-card">Content 1</div>
  <div class="chromatic-void-card">Content 2</div>
  <div class="chromatic-void-card">Content 3</div>
</div>
```

### Pattern 3: Hero Section
```html
<section class="hero">
  <div class="chromatic-liquid-metal">
    <h1>Welcome</h1>
    <button class="chromatic-button-void">Start</button>
  </div>
</section>
```

### Pattern 4: Dashboard
```html
<div class="dashboard">
  <div class="chromatic-ambient-glow" />

  <div class="metrics">
    <div class="chromatic-void-card">Metric 1</div>
    <div class="chromatic-void-card">Metric 2</div>
  </div>

  <div class="charts">
    <div class="chromatic-void-card">Chart 1</div>
    <div class="chromatic-void-card">Chart 2</div>
  </div>
</div>
```

---

## Accessibility Checklist

- [ ] All animated elements support `prefers-reduced-motion`
- [ ] Text contrast meets WCAG AA (4.5:1) minimum
- [ ] Hover effects don't affect keyboard navigation
- [ ] No color-only information (effects enhance, not inform)
- [ ] Focus rings visible on interactive elements
- [ ] Mobile touch targets remain clickable
- [ ] Background glows don't interfere with readability

---

## Testing Checklist by Device

### Desktop (Chrome, Firefox, Safari)
- [ ] All animations smooth (60fps)
- [ ] No GPU memory issues
- [ ] Effects visible on dark background
- [ ] Hover states work correctly
- [ ] Reduced motion respected

### Tablet (iPad, Android)
- [ ] Performance acceptable (reduce animations if needed)
- [ ] Touch targets large enough (44px minimum)
- [ ] No hover effects on touch (use `:active` instead)
- [ ] Landscape and portrait work

### Mobile (iPhone, Android phone)
- [ ] Background glow disabled (performance)
- [ ] Button/card borders work on hover-simulated states
- [ ] Touch feedback clear
- [ ] No jank during scroll

### Accessibility (Screen Reader, Keyboard)
- [ ] Tab navigation unaffected
- [ ] Focus visible
- [ ] No motion sickness triggers (>3fps baseline)
- [ ] Reduced motion enabled for prefers setting

---

## Real-World OpenJarvis Integration

### Current Setup (after chromatic update)
```
frontend/src/
├── chromatic-advanced.css     (new - all 13 techniques)
├── chroma.css                 (existing - simplified)
├── hud.css                    (existing - UI kit)
├── index.css                  (existing - theme)
└── App.tsx                    (apply classes to components)
```

### Recommended Rollout

**Phase 1: Background + Buttons**
```
1. Add chromatic-ambient-glow to root
2. Apply chromatic-button-void to CTAs
3. Test performance on mobile
```

**Phase 2: Cards**
```
4. Add chromatic-void-card to chat messages
5. Add to data panels
6. Monitor GPU usage
```

**Phase 3: Premium Elements**
```
7. Add chromatic-liquid-metal to hero
8. Add spectral effects to featured content
9. Fine-tune opacity/animation speeds
```

**Phase 4: Refinement**
```
10. A/B test with users
11. Adjust speeds/opacities based on feedback
12. Disable on low-power devices if needed
```

---

## Example: Complete OpenJarvis Integration

```tsx
// App.tsx
import './chromatic-advanced.css';

export default function App() {
  return (
    <div className="app">
      {/* Background atmosphere */}
      <div className="chromatic-ambient-glow" />

      {/* Navigation */}
      <nav>
        <button className="chromatic-button-void">Home</button>
        <button className="chromatic-button-void">Chat</button>
        <button className="chromatic-button-void">Settings</button>
      </nav>

      {/* Hero Section */}
      <section className="hero">
        <div className="chromatic-liquid-metal">
          <h1>Welcome back, User</h1>
          <p>Your AI assistant is ready</p>
          <button className="chromatic-button-void">
            Start Conversation
          </button>
        </div>
      </section>

      {/* Chat Messages */}
      <div className="chat-area">
        <div className="chromatic-void-card">
          <p>How can I help you today?</p>
        </div>
      </div>

      {/* Dashboard Cards */}
      <section className="dashboard">
        <div className="chromatic-void-card">
          <h3>Recent Conversations</h3>
          <p>12 total</p>
        </div>
        <div className="chromatic-void-card">
          <h3>Usage This Month</h3>
          <p>847 requests</p>
        </div>
      </section>
    </div>
  );
}
```

---

## Summary Table

| Effect | Impact | Complexity | Use For | Animation Speed |
|--------|--------|------------|---------|-----------------|
| Ambient Glow | Low | Simple | Background | 20s |
| Void Card | Medium | Simple | Cards/Panels | Hover-triggered |
| Conic Border | High | Moderate | Featured elements | 6s |
| Spectral Border | High | Moderate | Alerts/Attention | 8s |
| Button Void | Medium | Moderate | CTAs/Navigation | 2s hover |
| Liquid Metal | Very High | Complex | Hero/Premium | 5s |
| Spectral Ring | High | Simple | Decorative | 4s |

Choose by balancing visual impact, performance, and context. Start minimal, add as needed.
