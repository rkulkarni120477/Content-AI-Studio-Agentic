/** Client-side parser matching promptops_app/parsers/blueprint_parser.parse_items_from_section */

const NUM_RE = /^(\s*)(\d+)([.):])\s+(.+)$/;
const BULL_RE = /^(\s*)([-*•])\s+(.+)$/;
const HEAD_RE = /^(#{1,4})\s+(.+)$/;

export function parseItemsFromSection(text) {
  if (!text?.trim()) return [];
  const items = [];
  const lines = text.split('\n');

  for (let li = 0; li < lines.length; li += 1) {
    const line = lines[li];
    const nm = NUM_RE.exec(line);
    const bm = BULL_RE.exec(line);
    const hm = HEAD_RE.exec(line);

    if (nm) {
      items.push({
        index: items.length,
        type: 'numbered',
        prefix: `${nm[2]}${nm[3]}`,
        text: nm[4].trim(),
        line_index: li,
        indent: nm[1],
      });
    } else if (bm) {
      items.push({
        index: items.length,
        type: 'bullet',
        prefix: bm[2],
        text: bm[3].trim(),
        line_index: li,
        indent: bm[1],
      });
    } else if (hm) {
      items.push({
        index: items.length,
        type: 'heading',
        prefix: hm[1],
        text: hm[2].trim(),
        line_index: li,
        indent: '',
      });
    }
  }

  if (!items.length) {
    const paras = text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
    return paras.map((p, i) => ({
      index: i,
      type: 'paragraph',
      prefix: '',
      text: p,
      line_index: i,
      indent: '',
    }));
  }

  return items;
}
