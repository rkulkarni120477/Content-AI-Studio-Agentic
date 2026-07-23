import { Node, mergeAttributes } from '@tiptap/core';
import { isAllowedEmbedSrc } from '@utils/markdownHtml';

/**
 * Block node for an embedded YouTube video.
 *
 * Renders (and parses) a bare <iframe> — no wrapper marker — so it survives the
 * sanitizer (which strips data-* / class) and round-trips through Markdown as an
 * inline-HTML island. Only allowlisted YouTube-embed srcs are accepted on parse;
 * anything else is left for the sanitizer to drop.
 */
const YoutubeEmbed = Node.create({
  name: 'youtubeEmbed',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,

  addAttributes() {
    return {
      src: { default: null },
      width: { default: 560 },
      height: { default: 315 },
    };
  },

  parseHTML() {
    return [
      {
        tag: 'iframe[src]',
        getAttrs: (el) => (isAllowedEmbedSrc(el.getAttribute('src'))
          ? {
            src: el.getAttribute('src'),
            width: el.getAttribute('width') || 560,
            height: el.getAttribute('height') || 315,
          }
          : false),
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['iframe', mergeAttributes(HTMLAttributes, {
      frameborder: '0',
      allowfullscreen: 'true',
      loading: 'lazy',
      allow: 'accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture',
    })];
  },

  addCommands() {
    return {
      setYoutubeEmbed: (attrs) => ({ commands }) => commands.insertContent({
        type: this.name,
        attrs,
      }),
    };
  },
});

export default YoutubeEmbed;
