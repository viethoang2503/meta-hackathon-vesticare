"""Draw decoded SAM masks over their source frames for a by-eye check.

    cd backend/tools/sam_masks && npm install
    node decode_masks.mjs <dir>/*.json > <dir>/masks.json
    python overlay.py <dir>

<dir> holds the frames (<stem>.png) and the responses gate_sam.py saved; writes <stem>.mask.png.
"""
import base64, json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
g2 = Path(sys.argv[1])
masks = json.load(open(g2 / "masks.json"))
html = """<body style="margin:0;background:#000"><canvas id=c></canvas><script>
async function draw(img, frame, objects){
  const im = new Image(); im.src = img; await im.decode();
  const c = document.getElementById('c'); c.width = im.width; c.height = im.height;
  const x = c.getContext('2d'); x.drawImage(im, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height);
  for (const o of objects) {
    const r = Uint8Array.from(atob(o.raster), ch => ch.charCodeAt(0));
    for (let j = 0; j < o.mh; j++) for (let i = 0; i < o.mw; i++) if (r[j*o.mw+i]) {
      const p = ((o.bounds.top+j)*c.width + o.bounds.left+i)*4;
      d.data[p] = 255; d.data[p+1] = d.data[p+1]*0.4; d.data[p+2] = d.data[p+2]*0.4;
    }
  }
  x.putImageData(d, 0, 0);
}</script></body>"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 400, "height": 300})
    pg.set_content(html)
    for m in masks:
        stem = Path(m["file"]).name.split(".")[0]
        img = "data:image/png;base64," + base64.b64encode((g2 / f"{stem}.png").read_bytes()).decode()
        pg.evaluate("([i,f,o]) => draw(i,f,o)", [img, m["frame"], m["objects"]])
        pg.locator("#c").screenshot(path=str(g2 / f"{stem}.mask.png"))
        print("overlay", stem, "objects", len(m["objects"]))
    b.close()
