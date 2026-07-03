export function visibilityLabel(p) {
  const vis = p.visibility || 'draft';
  const map = {
    global: { className: 'badge-global', text: 'Global' },
    team: {
      className: 'badge-team',
      text: (p.teams || []).length ? `Team: ${(p.teams || []).join(', ')}` : 'Team',
    },
    draft: { className: 'badge-draft', text: 'Draft' },
    Global: { className: 'badge-global', text: 'Global' },
    Team: { className: 'badge-team', text: 'Team' },
    Private: { className: 'badge-draft', text: 'Draft' },
    Public: { className: 'badge-global', text: 'Global' },
  };
  return map[vis] || { className: 'badge-draft', text: vis };
}

export function starsDisplay(avg) {
  const f = Math.floor(avg);
  const h = avg - f >= 0.5 ? 1 : 0;
  return '★'.repeat(f) + (h ? '½' : '') + '☆'.repeat(5 - f - h);
}

export function fillPromptContent(template, variables, values) {
  let out = template;
  for (const v of variables) {
    const val = values[v.name] ?? '';
    out = out.replaceAll(`{{${v.name}}}`, val);
  }
  return out;
}

export function extractVarNames(content) {
  const found = new Set();
  const re = /\{\{(\w+)\}\}/g;
  let m;
  while ((m = re.exec(content)) !== null) found.add(m[1]);
  return [...found];
}

export function toLabel(name) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
