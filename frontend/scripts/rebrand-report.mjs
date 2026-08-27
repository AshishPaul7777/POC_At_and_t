/**
 * Rebrand the bundled executive report and place it in public/.
 *
 * The source is a self-extracting "bundled page": a manifest of gzipped base64
 * assets plus a JSON-encoded HTML template, reassembled by an inline script at
 * load time. Two consequences shape this script:
 *
 *   1. The template is plain text inside a JSON string, so the client name can
 *      be replaced with a straight substitution.
 *   2. The logo is a wordmark drawn as vector paths -- no text nodes -- so a
 *      text replacement cannot touch it. It has to be swapped for a different
 *      asset, re-gzipped and re-encoded, or the report says one company in its
 *      prose and shows another in its header.
 *
 * Run:  node scripts/rebrand-report.mjs <source.html>
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '../public/executive-report.html')

const FROM = 'Bounteous'
const TO = 'Northern Trail Outfitters'

/** Certificate names are identifiers, not prose: keep them shaped like one. */
const LITERALS = [
  ['Bounteous_SSO_2023', 'NTO_SSO_2023'],
  ['Bounteous_SSO_2025', 'NTO_SSO_2025'],
  ['Bounteous PPT palette', 'NTO palette'],
]

/**
 * Header/footer artwork to strip.
 *
 * Two kinds, both removed rather than replaced:
 *
 *   * The agency wordmark, drawn as vector paths with no text nodes. There is
 *     no replacement mark to put there, and an invented one is worse than
 *     none.
 *   * An empty `image-slot` placeholder with `placeholder="Client logo"`,
 *     which renders as a visible 108x36 box captioned "Client logo" -- the
 *     report was built expecting a logo to be dropped in, and none was.
 *
 * The divider between them goes too: a 1px rule with nothing on either side
 * of it is just a stray line.
 */

const src = process.argv[2]
if (!src) {
  console.error('usage: node scripts/rebrand-report.mjs <source.html>')
  process.exit(1)
}

let html = readFileSync(src, 'utf8')
const before = html.length

// ---- 1. the template: strip the logos ------------------------------------
// Order matters: the divider is matched together with the image before it, so
// removing the images first would strand it.
const HEADER = /<img src=\\"[^"]*?\\" alt=\\"Bounteous\\"[^>]*?>(\\n\s*)?<span style=\\"width: 1px;[^>]*?>\s*<\\u002Fspan>/
const CLIENT_SLOT = /<x-import[^>]*?id=\\"client-logo\\"[^>]*?>(\s*<\\u002Fx-import>)?/
const ANY_LOGO = /<img src=\\"[^"]*?\\" alt=\\"Bounteous\\"[^>]*?>/g

let removed = 0
for (const [name, re] of [['header logo + divider', HEADER], ['client logo slot', CLIENT_SLOT]]) {
  const next = html.replace(re, '')
  if (next === html) throw new Error(`could not find the ${name} -- the report's markup has changed`)
  html = next
  removed++
}
const before2 = html
html = html.replace(ANY_LOGO, '')
if (html !== before2) removed++

// ---- 2. the template: swap the name ----------------------------------------
for (const [from, to] of LITERALS) {
  html = html.split(from).join(to)
}
const remaining = (html.match(new RegExp(FROM, 'gi')) || []).length
html = html.split(FROM).join(TO)

// Nothing may survive -- a demo that leaks the original client name in one
// stray string is worse than one that was never rebranded.
const leaked = (html.match(new RegExp(FROM, 'gi')) || []).length
if (leaked > 0) throw new Error(`${leaked} occurrences of ${FROM} survived`)

// The substitution is for prose. Anywhere the name lands inside an
// identifier -- a certificate, an app name -- it produces something like
// `Northern Trail Outfitters_SSO_2025`, with spaces where none belong. Those
// need an entry in LITERALS above, so fail rather than ship one.
const mangled = [...html.matchAll(new RegExp(`${TO}[_A-Za-z0-9]+`, 'g'))]
  .map((m) => m[0])
if (mangled.length) {
  throw new Error(
    `the name landed inside ${mangled.length} identifier(s); add them to ` +
    `LITERALS: ${[...new Set(mangled)].join(', ')}`)
}

html = html.replace(/<title>[^<]*<\/title>/, '<title>Executive Summary</title>')

mkdirSync(dirname(OUT), { recursive: true })
writeFileSync(OUT, html, 'utf8')

console.log(`logo elements removed     : ${removed}`)
console.log(`name occurrences replaced: ${remaining}`)
console.log(`size                     : ${before} -> ${html.length} bytes`)
console.log(`written                  : ${OUT}`)
