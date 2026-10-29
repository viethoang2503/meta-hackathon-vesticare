// Decode SAM 3.1 masks from saved /v1/responses JSON into full-frame 0/1 rasters.
// usage: node decode_masks.mjs <response.json>... > masks.json
import { readFileSync } from 'node:fs';
import { decodeMaskToRaster, parseImageStream, recordsOfKind } from '@meta-sam/parser';

function outputText(response) {
  const parts = [];
  for (const item of response.output ?? []) {
    for (const c of item.content ?? []) if (c.type === 'output_text') parts.push(c.text);
  }
  return parts.join('');
}

async function* events(text) {
  yield { type: 'response.output_text.delta', item_id: 'm', output_index: 0, content_index: 0, delta: text };
  yield { type: 'response.output_text.done', item_id: 'm', output_index: 0, content_index: 0, text };
  yield { type: 'response.completed' };
}

const out = [];
for (const path of process.argv.slice(2)) {
  const text = outputText(JSON.parse(readFileSync(path, 'utf8')));
  const size = text.match(/;w=(\d+);h=(\d+)\|>/);
  const parsed = parseImageStream(events(text));
  for await (const _ of parsed);
  const result = await parsed.finalResult;
  const objects = recordsOfKind(result.records, 'mask').map((r) => {
    const raster = decodeMaskToRaster(r.mask);
    let on = 0;
    for (const v of raster) on += v;
    return { bounds: r.bounds, mw: r.mask.width, mh: r.mask.height, on, raster: Buffer.from(raster).toString('base64') };
  });
  out.push({
    file: path,
    status: result.outcome.status,
    diagnostics: result.diagnostics.map((d) => d.code),
    frame: size ? { w: +size[1], h: +size[2] } : null,
    objects,
  });
}
process.stdout.write(JSON.stringify(out));
