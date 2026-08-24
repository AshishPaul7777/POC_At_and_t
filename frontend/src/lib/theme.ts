/**
 * Theme registry — AT&T brand.
 *
 * Values were read off att.com rather than recalled: deep navy #00388F for
 * feature surfaces, #0057B8 and #0072B2 for interaction, #009FDB cyan for
 * energy, #1D2329 text on white with #F3F4F6 and #F2FAFD as the quiet
 * surfaces. Buttons there are 28px pills, cards 16px, body copy 18px, and
 * headings are set LIGHT and tightly tracked rather than bold — that last
 * detail is most of what makes the brand recognisable.
 *
 * Aleck Sans is licensed to AT&T and is not redistributed here; the stack
 * below degrades to a humanist sans of similar proportion. Drop the real woff2
 * files into public/fonts and add them to --ui to make it exact.
 *
 * The semantic slots are fixed across every theme:
 *
 *   used / unused / review / scope   verdict semantics
 *   ai                               machine-written content, and nothing else
 *   accent                           interactive affordances
 *   danger                           things that actually broke
 *
 * Two rules a palette must respect, and a blue-dominant brand makes the second
 * one harder rather than easier:
 *
 *   1. UNUSED is never red and never green. Unused is a finding, not a failure,
 *      and a red/green pair is unreadable for roughly one man in twelve.
 *   2. `accent`, `review` and `ai` stay visibly distinct. With AT&T blue owning
 *      interaction, `review` is pushed to teal and `ai` to violet so a verdict
 *      pill, a link and a generated summary never read as the same thing.
 */

export interface Theme {
  id: string
  label: string
  mode: 'dark' | 'light'
  vars: Record<string, string>
}

/** The AT&T feature surface. Reserved for one element per view. */
const HERO_LIGHT = 'linear-gradient(120deg, #00388F 0%, #0057B8 55%, #009FDB 100%)'
const HERO_DARK = 'linear-gradient(120deg, #002837 0%, #00509E 55%, #009FDB 100%)'

export const THEMES: Theme[] = [
  {
    id: 'att',
    label: 'AT&T',
    mode: 'light',
    vars: {
      '--bg': '#FFFFFF',
      '--bg-deep': '#F3F4F6',
      '--surface': '#FFFFFF',
      '--surface-solid': '#FFFFFF',
      '--raised': '#FFFFFF',
      '--sunken': '#F3F4F6',
      '--hover': '#F2FAFD',
      '--border': '#E3E6EA',
      '--border-strong': '#C7CCD3',
      '--text': '#1D2329',
      '--text-dim': '#454B52',
      '--text-faint': '#878C94',
      '--accent': '#0057B8',
      '--accent-hover': '#00388F',
      '--on-accent': '#FFFFFF',
      '--accent-soft': '#F2FAFD',
      '--used': '#16794C',
      '--unused': '#B25A00',
      '--review': '#00707F',
      '--scope': '#6B7280',
      '--danger': '#C4262E',
      '--ai': '#5B3FD6',
      '--used-soft': '#EAF6F0',
      '--unused-soft': '#FDF3E7',
      '--review-soft': '#E8F6F8',
      '--danger-soft': '#FCEDEE',
      '--ai-soft': '#F1EEFC',
      '--grad-hero': HERO_LIGHT,
      '--shadow-sm': '0 1px 2px rgba(29,35,41,.07)',
      '--shadow-md': '0 4px 14px -4px rgba(29,35,41,.12)',
      '--shadow-lg': '0 18px 40px -12px rgba(0,56,143,.22)',
      '--glow': '0 6px 20px -8px rgba(0,87,184,.45)',
    },
  },
  {
    id: 'att-dark',
    label: 'AT&T dark',
    mode: 'dark',
    vars: {
      // Built on AT&T's own dark teal rather than a neutral black, so the dark
      // mode still reads as the same brand.
      '--bg': '#001A24',
      '--bg-deep': '#00121A',
      '--surface': '#002837',
      '--surface-solid': '#002837',
      '--raised': '#013242',
      '--sunken': '#00121A',
      '--hover': '#013A4D',
      '--border': '#0A3F52',
      '--border-strong': '#14586F',
      '--text': '#F2FAFD',
      '--text-dim': '#A9C2CE',
      '--text-faint': '#7593A3',
      '--accent': '#3FB6E8',
      '--accent-hover': '#6FCCF0',
      '--on-accent': '#00232F',
      '--accent-soft': 'rgba(63,182,232,.16)',
      '--used': '#3DD68C',
      '--unused': '#F5A524',
      '--review': '#22C3D6',
      '--scope': '#7593A3',
      '--danger': '#FF6B75',
      '--ai': '#A78BFA',
      '--used-soft': 'rgba(61,214,140,.14)',
      '--unused-soft': 'rgba(245,165,36,.14)',
      '--review-soft': 'rgba(34,195,214,.14)',
      '--danger-soft': 'rgba(255,107,117,.14)',
      '--ai-soft': 'rgba(167,139,250,.15)',
      '--grad-hero': HERO_DARK,
      '--shadow-sm': '0 1px 2px rgba(0,0,0,.35)',
      '--shadow-md': '0 6px 18px -6px rgba(0,0,0,.5)',
      '--shadow-lg': '0 20px 44px -14px rgba(0,0,0,.65)',
      '--glow': '0 6px 22px -8px rgba(0,159,219,.45)',
    },
  },
]

const KEY = 'sfc.theme'

export function initialTheme(): string {
  const saved = localStorage.getItem(KEY)
  if (saved && THEMES.some((t) => t.id === saved)) return saved
  // AT&T is a white brand, so light is the default; the OS preference only
  // wins when it explicitly asks for dark.
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'att-dark'
    : 'att'
}

export function applyTheme(id: string): void {
  const theme = THEMES.find((t) => t.id === id) ?? THEMES[0]
  const root = document.documentElement
  for (const [k, v] of Object.entries(theme.vars)) root.style.setProperty(k, v)
  root.dataset.mode = theme.mode
  root.style.colorScheme = theme.mode
  localStorage.setItem(KEY, id)
}
