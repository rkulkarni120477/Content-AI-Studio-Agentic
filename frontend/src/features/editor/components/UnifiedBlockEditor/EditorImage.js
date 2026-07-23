import Image from '@tiptap/extension-image';

/**
 * Editor image node with width (resize) and alignment support.
 *
 * A plain image with no width/alignment renders as a bare <img> so it serializes
 * back to standard Markdown `![alt](src)`. When width or alignment is set — things
 * Markdown can't express — it renders as a `<figure data-type="image">` island
 * carrying `text-align` (the only style our sanitizer allows), which turndown
 * preserves verbatim. Either shape parses back into this node on load.
 */
const ALIGNMENTS = ['left', 'center', 'right'];

const EditorImage = Image.extend({
  name: 'image',

  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        renderHTML: (attrs) => (attrs.width ? { width: attrs.width } : {}),
        parseHTML: (el) => el.getAttribute('width'),
      },
      align: {
        default: null,
        // Alignment is applied by the figure wrapper in renderHTML, never on <img>.
        renderHTML: () => ({}),
        parseHTML: () => null,
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: 'figure',
        getAttrs: (fig) => {
          const img = fig.querySelector('img');
          if (!img) return false;
          const m = (fig.getAttribute('style') || '').match(/text-align:\s*(left|center|right)/i);
          return {
            src: img.getAttribute('src'),
            alt: img.getAttribute('alt'),
            title: img.getAttribute('title'),
            width: img.getAttribute('width'),
            align: m ? m[1].toLowerCase() : null,
          };
        },
      },
      {
        tag: 'img[src]',
        getAttrs: (img) => ({
          src: img.getAttribute('src'),
          alt: img.getAttribute('alt'),
          title: img.getAttribute('title'),
          width: img.getAttribute('width'),
          align: null,
        }),
      },
    ];
  },

  renderHTML({ node, HTMLAttributes }) {
    const align = node.attrs.align;
    // HTMLAttributes already excludes align (its renderHTML returns {}).
    const imgAttrs = { ...HTMLAttributes };
    if (align && ALIGNMENTS.includes(align)) {
      return ['figure', { style: `text-align: ${align}` }, ['img', imgAttrs]];
    }
    return ['img', imgAttrs];
  },
});

export default EditorImage;
