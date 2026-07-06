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

// Legacy Python-.format()-style {single}-brace placeholders. Every prompt the
// backend renders — library AND pipeline — uses {{double}}; {single} survives
// only in backend fallback constants that were never meant to be pasted into
// the console. Detecting them lets the form warn instead of silently treating
// the body as variable-less. Identifier-shaped tokens only, so JSON examples
// ({"key": ...}) never match.
export function findLegacyVarNames(content) {
  const found = new Set();
  const re = /\{(\w+)\}/g;
  let m;
  while ((m = re.exec(content)) !== null) {
    // Part of a {{double}} placeholder? Checked via neighbors instead of a
    // lookbehind so the regex parses on older Safari.
    const before = content[m.index - 1];
    const after = content[m.index + m[0].length];
    if (before !== '{' && after !== '}') found.add(m[1]);
  }
  return [...found];
}

export function toLabel(name) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Pipeline rows have no freeform category — their classification is the
// load-bearing (component_type, variant) resolution key. Where the UI shows a
// category for library rows, show the stage instead of an empty cell.
//
// Display names follow the CAS-sync requirements doc's six categories
// (Style, CDD, Blueprint, Assessment, Component, Lesson Generation). The
// resolution keys themselves never change: quiz → "Assessment",
// generate → "Lesson Generation", (generate, interactive) → "Component".
const STAGE_LABELS = {
  style: 'Style',
  cdd: 'CDD',
  blueprint: 'Blueprint',
  generate: 'Lesson Generation',
  quiz: 'Assessment',
};

export function componentCategoryLabel(componentType, variant) {
  if (!componentType) return variant ? `Pipeline / ${variant}` : 'Pipeline';
  if (componentType === 'generate') {
    if (variant === 'interactive') return 'Component';
    if (!variant || variant === 'lesson') return 'Lesson Generation';
  }
  const base = STAGE_LABELS[componentType] || toLabel(componentType);
  return variant ? `${base} / ${variant}` : base;
}

export function pipelineStageLabel(p) {
  if (p?.prompt_kind !== 'pipeline') return '';
  const pipe = p.pipeline || {};
  return componentCategoryLabel(pipe.component_type, pipe.variant);
}
