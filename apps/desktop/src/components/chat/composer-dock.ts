import { cn } from '@/lib/utils'

/**
 * The composer surface and the status/queue stack paint ONE shared
 * `--composer-fill` var. The state ladder (rest / scrolled) lives in styles.css
 * on `[data-slot='composer-root']`, so the layers can never disagree.
 */
export const composerFill = 'bg-(--composer-fill)'

/** Backdrop treatment for the composer input surface. Harmless when the fill
 *  goes opaque (drawer open) — nothing shows through to blur.
 *
 *  The blur radius comes from `--composer-glass-blur`, set on the document root
 *  by store/composer-glass.ts from the Appearance → Composer glass lever. At
 *  lever 0 the var is `0`, which makes the filter a no-op — worth having,
 *  because blurring the backdrop over the transcript means re-sampling that
 *  region whenever content behind it changes, i.e. on every streaming flush.
 *  The default lives in styles.css `:root` so the var is always defined (no
 *  comma-fallback inside the arbitrary value, which Tailwind would mangle). */
export const composerSurfaceGlass = cn(
  'backdrop-blur-(--composer-glass-blur) backdrop-saturate-[1.12]',
  '[-webkit-backdrop-filter:blur(var(--composer-glass-blur))_saturate(1.12)]',
  'transition-[background-color] duration-150 ease-out'
)

const composerDockEdge = (edge: 'bottom' | 'top') =>
  cn('border border-border/65', edge === 'top' ? 'rounded-t-2xl border-b-0' : 'rounded-b-2xl border-t-0')

/** Glassy docked card — the status stack / queue. Paints the SAME
 *  `--composer-fill` as the surface, so rest / scrolled / focused / drawer-open
 *  all match the composer by construction. */
export const composerDockCard = (edge: 'bottom' | 'top' = 'top') =>
  cn(composerDockEdge(edge), composerFill, composerSurfaceGlass)

/** Floating composer panel skin — the `/`·`@`·`?` completion drawer and the
 *  attach (`+`) menu. Glassy translucent card, hairline border, full radius,
 *  smallest type, soft nous shadow. Uses an explicit fill (not `--composer-fill`)
 *  so it renders identically whether mounted inside the composer or portaled out
 *  of it. Visual skin only — consumers add their own size/position/padding. */
export const composerPanelCard = cn(
  'rounded-2xl border border-border/65 shadow-nous text-[length:var(--conversation-tool-font-size)]',
  'bg-[color-mix(in_srgb,var(--dt-card)_72%,transparent)]',
  composerSurfaceGlass
)
