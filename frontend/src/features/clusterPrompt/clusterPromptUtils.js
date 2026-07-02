export function promptLabel(p) {
  const desc = p.description ? ` — ${p.description.slice(0, 40)}` : '';
  return `${p.name}${desc}`;
}
