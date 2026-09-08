import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// History charts: the pure row-shaping behind the power chart. The whole
// inline script is syntax-checked as a side effect.
const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
// powerRows: frozen-link buckets arrive as null from /api/history and must
// stay null (a gap), never become 0; counter-bridged buckets carry a flag.
const prStart = script.indexOf('function powerRows(');
const prEnd = script.indexOf('/* ---------- power chart', prStart);
const powerRows = vm.runInNewContext(script.slice(prStart, prEnd) + ';powerRows');
const series = (t, o) => ({ t, solar_w: [], watts_out: [], ac_out_w: [], dc_out_w: [], bridged: [], ...o });
const flatB = t => series(t, { solar_w: t.map(() => 0), ac_out_w: t.map(() => 0), dc_out_w: t.map(() => 0),
  watts_out: t.map(() => 0), bridged: t.map(() => false) });

test('powerRows keeps a frozen bucket as a gap, not a zero', () => {
  const A = series([0, 1, 2], { solar_w: [10, null, 30], ac_out_w: [5, null, 7], dc_out_w: [1, null, 1],
    watts_out: [6, null, 8], bridged: [false, false, false] });
  const rows = powerRows({ series: { A, B: flatB([0, 1, 2]) } });
  assert.equal(rows[1].solA, null); assert.equal(rows[1].load, null);
  assert.equal(rows[1].gapA, true); assert.equal(rows[0].gapA, false);
  assert.equal(rows[2].load, 8);
});
test('powerRows flags counter-bridged buckets', () => {
  const A = series([0, 1], { solar_w: [10, 12], ac_out_w: [5, 31], dc_out_w: [1, 5], watts_out: [6, 36],
    bridged: [false, true] });
  const rows = powerRows({ series: { A, B: flatB([0, 1]) } });
  assert.equal(rows[1].bridgedA, true); assert.equal(rows[1].load, 36); assert.equal(rows[0].bridgedA, false);
});
