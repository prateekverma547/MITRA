// Compress the candidate app's build once, at build time (live/interview_static.py
// serves the variants; nothing is compressed per response).
//
//   node backend/scripts/precompress.mjs <dist dir> [--report <file.json>]
//
// For each compressible file writes <file>.br (brotli, quality 11) and <file>.gz
// (gzip, level 9), and keeps a variant only when it saves at least MIN_SAVING
// of the original: a "compressed" file that is barely smaller costs CPU in the
// browser for nothing. Node's own zlib, so no new dependency in either the
// Docker frontend stage or a developer's machine. Prints one line per file and
// the totals; --report also writes them as JSON.
import { readdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const COMPRESSIBLE = new Set([".wasm", ".js", ".mjs", ".css", ".html", ".svg", ".json", ".task", ".txt"]);
const MIN_SAVING = 0.1;

const [dist, ...rest] = process.argv.slice(2);
if (!dist) {
  console.error("usage: node precompress.mjs <dist dir> [--report file.json]");
  process.exit(2);
}
const reportAt = rest.includes("--report") ? rest[rest.indexOf("--report") + 1] : null;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else if (COMPRESSIBLE.has(extname(p))) yield p;
  }
}

const rows = [];
for (const path of files(dist)) {
  const raw = readFileSync(path);
  const br = brotliCompressSync(raw, {
    params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: raw.length },
  });
  const gz = gzipSync(raw, { level: 9 });
  const row = { file: relative(dist, path), bytes: raw.length, gzip: gz.length, brotli: br.length, kept: [] };
  for (const [suffix, data] of [[".br", br], [".gz", gz]]) {
    const target = path + suffix;
    if (data.length <= raw.length * (1 - MIN_SAVING)) {
      writeFileSync(target, data);
      row.kept.push(suffix);
    } else {
      try { unlinkSync(target); } catch { /* none */ }
    }
  }
  rows.push(row);
  console.log(`${row.file}\t${row.bytes}\tgzip ${row.gzip}\tbrotli ${row.brotli}\tkept ${row.kept.join(",") || "none"}`);
}
const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
const totals = { files: rows.length, bytes: sum("bytes"), gzip: sum("gzip"), brotli: sum("brotli") };
console.log(`total\t${totals.bytes}\tgzip ${totals.gzip}\tbrotli ${totals.brotli}`);
if (reportAt) writeFileSync(reportAt, JSON.stringify({ rows, totals }, null, 2));
