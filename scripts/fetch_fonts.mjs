/* Refresh the self-hosted fonts in web/assets/fonts.
   Run from the project root:  node scripts/fetch_fonts.mjs

   Google serves Oxanium and IBM Plex Sans as VARIABLE fonts — the same woff2 comes back
   for every weight you ask for, so those get one file and a weight range in fonts.css.
   IBM Plex Mono is static, one file per weight. Latin subset only.
   fonts.css is hand-maintained; this script only refreshes the woff2 payloads and prints
   what it wrote so you can confirm nothing moved. */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const OUT = 'web/assets/fonts';
mkdirSync(OUT, { recursive: true });

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';

const SPECS = [
  { spec: 'Oxanium:wght@600', out: ['Oxanium-var.woff2'] },
  { spec: 'IBM+Plex+Sans:wght@400', out: ['IBMPlexSans-var.woff2'] },
  { spec: 'IBM+Plex+Mono:wght@400;500;600',
    out: ['IBMPlexMono-400.woff2', 'IBMPlexMono-500.woff2', 'IBMPlexMono-600.woff2'] },
];

for (const { spec, out } of SPECS) {
  const css = await (await fetch(
    `https://fonts.googleapis.com/css2?family=${spec}&display=swap`,
    { headers: { 'User-Agent': UA } })).text();

  // one @font-face block per weight; keep the latin one (the block carrying U+0000-00FF)
  const urls = css.split('@font-face').slice(1)
    .filter(b => /unicode-range:[^;]*U\+0000-00FF/.test(b))
    .map(b => /url\((https:\/\/[^)]+\.woff2)\)/.exec(b)?.[1])
    .filter(Boolean);

  if (urls.length !== out.length) {
    throw new Error(`${spec}: expected ${out.length} latin faces, got ${urls.length} — `
      + 'Google changed the family; update SPECS and fonts.css together.');
  }
  for (const [i, url] of urls.entries()) {
    const buf = Buffer.from(await (await fetch(url, { headers: { 'User-Agent': UA } })).arrayBuffer());
    writeFileSync(join(OUT, out[i]), buf);
    console.log(`${out[i].padEnd(24)} ${buf.length} bytes`);
  }
}
console.log('\nfonts.css is hand-maintained — check it still matches these filenames.');
