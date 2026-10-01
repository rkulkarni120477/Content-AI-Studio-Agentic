/**
 * Per-tenant UI wording — the renameable vocabulary and how word forms derive.
 *
 * A platform admin renames four words per tenant in Configuration; every
 * on-screen label is then built from those words. Storing words (not whole
 * phrases) is what keeps the form to four boxes instead of ~50: "Generated
 * Styles" is `Generated ${L.styles}`, so renaming Style → Design Guide yields
 * "Generated Design Guides" with no extra input.
 *
 * Display-only. Route segments (/workspace/:id/style), API payload values
 * (artifact_type: 'cdd'), and anything sent to a model are unaffected.
 *
 * Keys must stay in sync with UI_LABEL_KEYS in app/schemas/ui_labels.py.
 */

export const LABEL_KEYS = ['title', 'style', 'cdd', 'blueprint'];

export const DEFAULT_LABELS = {
  title:     'Title',
  style:     'Style',
  cdd:       'CDD',
  blueprint: 'Blueprint',
};

/** Human-readable copy for the Configuration form, one entry per box. */
export const LABEL_FIELDS = [
  {
    key: 'title',
    label: 'Title',
    hint: 'A single course or book. Renames "Select Title", "Create Title", "All Titles" and more.',
  },
  {
    key: 'style',
    label: 'Style',
    hint: 'Sidebar item, page heading, and every section name on the Style page.',
  },
  {
    key: 'cdd',
    label: 'CDD',
    hint: 'Sidebar item and headings on the CDD page.',
  },
  {
    key: 'blueprint',
    label: 'Blueprint',
    hint: 'Sidebar item and headings on the Blueprint page.',
  },
];

/** Matches the server cap in app/schemas/ui_labels.py. */
export const MAX_LABEL_LENGTH = 40;

/**
 * Pluralise an English display word.
 * Handles the cases our labels realistically hit: "Style" → "Styles",
 * "Class" → "Classes", "Story" → "Stories". Acronyms just take an -s
 * ("CDD" → "CDDs"), which is the conventional form.
 */
export function pluralize(word) {
  if (!word) return word;
  if (/[sxz]$/i.test(word) || /(ch|sh)$/i.test(word)) return `${word}es`;
  if (/[^aeiouAEIOU]y$/.test(word)) return `${word.slice(0, -1)}ies`;
  return `${word}s`;
}

/**
 * Lowercase a word for mid-sentence use, leaving acronyms alone.
 * "Design Guide" → "design Guide" would be wrong, so only the first character
 * is lowered — and not at all for all-caps words like CDD, which must stay
 * uppercase inside a sentence.
 */
export function lowerFirst(word) {
  if (!word) return word;
  if (/^[A-Z0-9]{2,}$/.test(word)) return word;
  return word.charAt(0).toLowerCase() + word.slice(1);
}

/**
 * Prefix a display word with "a" or "an", so a renamed label keeps its grammar:
 * "an Outline", "a Blueprint". Acronyms go by how the first letter is spoken
 * ("a CDD", "an SOP"); other words by their first letter being a vowel.
 */
export function withArticle(word) {
  if (!word) return word;
  const anLetters = /^[A-Z0-9]{2,}$/.test(word) ? /^[AEFHILMNORSX]/ : /^[aeiou]/i;
  return `${anLetters.test(word) ? 'an' : 'a'} ${word}`;
}

/** Drop unknown keys and blank values — a blank box means "use the default". */
export function sanitizeOverrides(overrides) {
  const clean = {};
  if (!overrides || typeof overrides !== 'object') return clean;
  LABEL_KEYS.forEach((key) => {
    const raw = overrides[key];
    if (typeof raw !== 'string') return;
    const word = raw.trim().slice(0, MAX_LABEL_LENGTH).trim();
    if (word && word !== DEFAULT_LABELS[key]) clean[key] = word;
  });
  return clean;
}

/**
 * Build the full label set a component consumes.
 *
 * Returns singular, plural and lowercase forms for each key, e.g.
 *   { title, titles, titleLower, titlesLower, style, styles, ... }
 * Every form always resolves to a non-empty string: an unknown or blank
 * override falls back to the default, so no label can ever render empty.
 */
export function buildLabels(overrides) {
  const clean = sanitizeOverrides(overrides);
  const labels = {};

  LABEL_KEYS.forEach((key) => {
    const word = clean[key] || DEFAULT_LABELS[key];
    const many = pluralize(word);
    labels[key] = word;
    labels[`${key}s`] = many;
    labels[`${key}Lower`] = lowerFirst(word);
    labels[`${key}sLower`] = lowerFirst(many);
  });

  // Which keys actually differ from the default — drives the "Renamed labels"
  // column in Configuration without re-deriving it there.
  labels.overrides = clean;
  labels.isCustomized = Object.keys(clean).length > 0;

  /**
   * For headings that spell out a term in full, e.g. "Title Design Document (CDD)".
   * A tenant that renamed the term gets their word; everyone else keeps the
   * original phrase, so default tenants lose no wording they have today.
   */
  labels.phrase = (key, defaultPhrase) => (clean[key] ? labels[key] : defaultPhrase);

  return labels;
}

/** Default label set, for components rendered outside any tenant context. */
export const FALLBACK_LABELS = buildLabels({});

/** Build labels from a project row (or null → defaults). */
export function labelsFromProject(project) {
  let raw = project?.ui_labels ?? project?.uiLabels;
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw);
    } catch {
      raw = {};
    }
  }
  return buildLabels(raw);
}

/**
 * Build labels from Redux state (thunks / non-React code).
 * Accepts `getState` or an already-read state object.
 */
export function labelsFromState(getStateOrState) {
  try {
    const state = typeof getStateOrState === 'function'
      ? getStateOrState()
      : getStateOrState;
    const selected = state?.dashboard?.selectedProject;
    const listed = (state?.dashboard?.projects?.items || [])
      .find((p) => p?.id === selected?.id);
    const hasOverrides = (p) => {
      const raw = p?.ui_labels ?? p?.uiLabels;
      if (!raw) return false;
      if (typeof raw === 'string') return raw.trim().length > 2;
      return typeof raw === 'object' && Object.keys(raw).length > 0;
    };
    return labelsFromProject(hasOverrides(selected) ? selected : (listed || selected));
  } catch {
    return FALLBACK_LABELS;
  }
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Soft-replace default product terms in display/generated text with the tenant's
 * wording. Word-boundary only by default — does not touch identifiers like
 * blueprint_id. Pass `{ identifiers: true }` to also rewrite snake_case slugs
 * such as default_style_prompt. Pass `keys` to limit which vocabulary is
 * rewritten (e.g. ['blueprint']).
 */
export function applyTerminology(text, L = FALLBACK_LABELS, keys = LABEL_KEYS, options = {}) {
  if (!text || typeof text !== 'string') return text;
  let out = text;
  const identifiers = Boolean(options?.identifiers);
  const all = {
    title: [
      ['Titles', L.titles], ['Title', L.title],
      ['titles', L.titlesLower], ['title', L.titleLower],
    ],
    style: [
      ['Styles', L.styles], ['Style', L.style],
      ['styles', L.stylesLower], ['style', L.styleLower],
    ],
    cdd: [
      ['CDDs', L.cdds], ['CDD', L.cdd],
      ['cdds', L.cddsLower], ['cdd', L.cddLower],
    ],
    blueprint: [
      ['Blueprints', L.blueprints], ['Blueprint', L.blueprint],
      ['blueprints', L.blueprintsLower], ['blueprint', L.blueprintLower],
    ],
  };
  for (const key of keys) {
    const pairs = identifiers
      ? ({
        title: [['Titles', L.titles], ['titles', L.titles], ['Title', L.title], ['title', L.title]],
        style: [['Styles', L.styles], ['styles', L.styles], ['Style', L.style], ['style', L.style]],
        cdd: [['CDDs', L.cdds], ['cdds', L.cdds], ['CDD', L.cdd], ['cdd', L.cdd]],
        blueprint: [
          ['Blueprints', L.blueprints], ['blueprints', L.blueprints],
          ['Blueprint', L.blueprint], ['blueprint', L.blueprint],
        ],
      }[key] || [])
      : (all[key] || []);
    for (const [from, to] of pairs) {
      if (!to || to === from) continue;
      const pattern = identifiers
        ? new RegExp(`(?<![A-Za-z0-9])${escapeRegExp(from)}(?![A-Za-z0-9])`, 'g')
        : new RegExp(`\\b${escapeRegExp(from)}\\b`, 'g');
      out = out.replace(pattern, to);
    }
  }
  return out;
}

/**
 * "Title → Block, Style → Design Guide" for the Configuration tenant list.
 * Returns [] when the tenant uses default wording.
 */
export function describeOverrides(overrides) {
  const clean = sanitizeOverrides(overrides);
  return LABEL_KEYS
    .filter((key) => clean[key])
    .map((key) => ({ key, from: DEFAULT_LABELS[key], to: clean[key] }));
}
