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

import { gunzipSync, gzipSync } from 'node:zlib'
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

/** Replacement wordmark, matching the original's 146x24 box. */
const WORDMARK = `<svg width="146" height="24" viewBox="0 0 146 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <text x="0" y="17" font-family="Nunito Sans, Helvetica, Arial, sans-serif"
        font-size="15" font-weight="700" letter-spacing="-0.2" fill="currentColor">Northern Trail</text>
  <text x="103" y="17" font-family="Nunito Sans, Helvetica, Arial, sans-serif"
        font-size="15" font-weight="300" fill="currentColor">Outfitters</text>
</svg>`

const src = process.argv[2]
if (!src) {
  console.error('usage: node scripts/rebrand-report.mjs <source.html>')
  process.exit(1)
}

let html = readFileSync(src, 'utf8')
const before = html.length

// ---- 1. the asset manifest: swap the wordmark ------------------------------
const mRe = /(<script type="__bundler\/manifest">)([\s\S]*?)(<\/script>)/
const m = html.match(mRe)
if (!m) throw new Error('no bundler manifest found -- is this a bundled page?')

const manifest = JSON.parse(m[2])
const encoded = gzipSync(Buffer.from(WORDMARK, 'utf8')).toString('base64')

let swapped = 0
for (const [key, entry] of Object.entries(manifest)) {
  if (entry.mime !== 'image/svg+xml') continue
  let raw = Buffer.from(entry.data, 'base64')
  if (entry.compressed) raw = gunzipSync(raw)
  const svg = raw.toString('utf8')
  // The wordmark is the one with no text nodes and a 146-wide box. Guarding on
  // the dimensions stops this quietly replacing a chart or an icon if the
  // report's assets ever change.
  if (!/width="146"/.test(svg) || /<text/.test(svg)) continue
  manifest[key] = { ...entry, compressed: true, data: encoded }
  swapped++
}
if (swapped === 0) throw new Error('no wordmark asset matched -- refusing to ship a half-rebranded report')

html = html.replace(mRe, (_all, open, _body, close) =>
  open + JSON.stringify(manifest) + close)

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

console.log(`wordmark assets replaced : ${swapped}`)
console.log(`name occurrences replaced: ${remaining}`)
console.log(`size                     : ${before} -> ${html.length} bytes`)
console.log(`written                  : ${OUT}`)
