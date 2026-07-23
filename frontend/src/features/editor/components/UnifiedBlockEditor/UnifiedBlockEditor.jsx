import { useEffect, useMemo, useRef, useState } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import TextAlign from '@tiptap/extension-text-align';
import Placeholder from '@tiptap/extension-placeholder';
import Table from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableHeader from '@tiptap/extension-table-header';
import TableCell from '@tiptap/extension-table-cell';
import { api } from '@services/apiClient';
import { ASSETS } from '@services/endpoints';
import { mdToHtml, htmlToMd } from '@utils/markdownHtml';
import EditorImage from './EditorImage';
import YoutubeEmbed from './YoutubeEmbed';
import ImageDialog from './ImageDialog';
import EmbedDialog from './EmbedDialog';
import styles from './UnifiedBlockEditor.module.scss';

/**
 * Unified block editor with Preview (default) and Edit modes over a single,
 * full-width content area.
 *
 * The canonical value is Markdown: `content` in, `onChange(markdown)` out. The
 * editor works in HTML internally and converts at the boundary via
 * `@utils/markdownHtml`. onChange fires ONLY on real user edits — never on load
 * or mode switch — so an unedited block's stored Markdown is never rewritten.
 */

const EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3, 4] },
  }),
  Underline,
  Link.configure({
    openOnClick: false,
    autolink: true,
    HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: '_blank' },
  }),
  TextAlign.configure({ types: ['heading', 'paragraph'] }),
  Placeholder.configure({ placeholder: 'Start writing…' }),
  Table.configure({ resizable: false }),
  TableRow,
  TableHeader,
  TableCell,
  EditorImage.configure({ inline: false, allowBase64: true }),
  YoutubeEmbed,
];

function ToolbarButton({ label, icon, onClick, active = false, disabled = false }) {
  return (
    <button
      type="button"
      className={`${styles.tbtn} ${active ? styles['tbtn--active'] : ''}`}
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={label}
    >
      <span aria-hidden="true">{icon}</span>
    </button>
  );
}

function Toolbar({ editor, disabled, onImageClick, onEmbedClick }) {
  if (!editor) return null;

  const setLink = () => {
    const previous = editor.getAttributes('link').href || '';
    const url = window.prompt('Link URL (leave empty to remove):', previous);
    if (url === null) return; // cancelled
    if (url.trim() === '') {
      editor.chain().focus().extendMarkRange('link').unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange('link').setLink({ href: url.trim() }).run();
  };

  return (
    <div className={styles.toolbar} role="toolbar" aria-label="Text formatting" aria-disabled={disabled}>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Heading 2" icon="H2" disabled={disabled} active={editor.isActive('heading', { level: 2 })} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} />
        <ToolbarButton label="Heading 3" icon="H3" disabled={disabled} active={editor.isActive('heading', { level: 3 })} onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()} />
        <ToolbarButton label="Paragraph" icon="¶" disabled={disabled} active={editor.isActive('paragraph')} onClick={() => editor.chain().focus().setParagraph().run()} />
      </div>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Bold" icon="B" disabled={disabled} active={editor.isActive('bold')} onClick={() => editor.chain().focus().toggleBold().run()} />
        <ToolbarButton label="Italic" icon="I" disabled={disabled} active={editor.isActive('italic')} onClick={() => editor.chain().focus().toggleItalic().run()} />
        <ToolbarButton label="Underline" icon="U" disabled={disabled} active={editor.isActive('underline')} onClick={() => editor.chain().focus().toggleUnderline().run()} />
      </div>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Bulleted list" icon="•" disabled={disabled} active={editor.isActive('bulletList')} onClick={() => editor.chain().focus().toggleBulletList().run()} />
        <ToolbarButton label="Numbered list" icon="1." disabled={disabled} active={editor.isActive('orderedList')} onClick={() => editor.chain().focus().toggleOrderedList().run()} />
        <ToolbarButton label="Block quote" icon="❝" disabled={disabled} active={editor.isActive('blockquote')} onClick={() => editor.chain().focus().toggleBlockquote().run()} />
      </div>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Align left" icon="⇤" disabled={disabled} active={editor.isActive({ textAlign: 'left' })} onClick={() => editor.chain().focus().setTextAlign('left').run()} />
        <ToolbarButton label="Align center" icon="↔" disabled={disabled} active={editor.isActive({ textAlign: 'center' })} onClick={() => editor.chain().focus().setTextAlign('center').run()} />
        <ToolbarButton label="Align right" icon="⇥" disabled={disabled} active={editor.isActive({ textAlign: 'right' })} onClick={() => editor.chain().focus().setTextAlign('right').run()} />
      </div>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Insert or edit link" icon="🔗" disabled={disabled} active={editor.isActive('link')} onClick={setLink} />
        <ToolbarButton label="Remove link" icon="⛓️‍💥" disabled={disabled || !editor.isActive('link')} onClick={() => editor.chain().focus().unsetLink().run()} />
        <ToolbarButton label="Insert or edit image" icon="🖼️" disabled={disabled} active={editor.isActive('image')} onClick={onImageClick} />
        <ToolbarButton label="Insert video" icon="🎬" disabled={disabled} active={editor.isActive('youtubeEmbed')} onClick={onEmbedClick} />
      </div>
      <div className={styles.toolbar__group}>
        <ToolbarButton label="Undo" icon="↩" disabled={disabled || !editor.can().undo()} onClick={() => editor.chain().focus().undo().run()} />
        <ToolbarButton label="Redo" icon="↪" disabled={disabled || !editor.can().redo()} onClick={() => editor.chain().focus().redo().run()} />
        <ToolbarButton label="Clear formatting" icon="⌫" disabled={disabled} onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()} />
      </div>
    </div>
  );
}

async function uploadImageFile(file, onProgress) {
  const form = new FormData();
  form.append('file', file);
  const res = await api.upload(ASSETS.UPLOAD, form, onProgress);
  return res.url;
}

export default function UnifiedBlockEditor({ content = '', onChange, disabled = false, onAssetUploaded }) {
  const [mode, setMode] = useState('preview');
  const [imageDialog, setImageDialog] = useState({ open: false, editing: false, initial: null });
  const [embedOpen, setEmbedOpen] = useState(false);

  // Markdown the editor last emitted — lets us tell user edits apart from
  // external content changes (regenerate / restore / draft recovery) so we
  // never fire onChange for a change we didn't originate.
  const lastEmitted = useRef(content);

  const editor = useEditor({
    extensions: EXTENSIONS,
    content: mdToHtml(content),
    editable: !disabled,
    editorProps: {
      attributes: {
        class: styles.prose,
        'aria-label': 'Block content editor',
        role: 'textbox',
        'aria-multiline': 'true',
      },
    },
    onUpdate: ({ editor: ed }) => {
      // Only user edits should propagate. TipTap emits an update on initial load
      // and on programmatic setContent — both happen while the editor is NOT
      // focused, so ignoring unfocused updates prevents phantom autosaves / diffs
      // (real typing, pasting, and toolbar commands all run with focus).
      if (!ed.isFocused) return;
      const md = htmlToMd(ed.getHTML());
      lastEmitted.current = md;
      onChange?.(md);
    },
  });

  // External content change → refresh the editor without emitting onChange.
  useEffect(() => {
    if (!editor) return;
    if (content !== lastEmitted.current) {
      lastEmitted.current = content;
      editor.commands.setContent(mdToHtml(content), false);
    }
  }, [content, editor]);

  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [disabled, editor]);

  const previewHtml = useMemo(() => mdToHtml(content), [content]);

  function openImageDialog() {
    const editing = editor?.isActive('image');
    setImageDialog({
      open: true,
      editing: Boolean(editing),
      initial: editing ? editor.getAttributes('image') : null,
    });
  }

  function closeImageDialog() {
    setImageDialog({ open: false, editing: false, initial: null });
  }

  function submitImage(attrs) {
    if (!editor) return;
    if (imageDialog.editing) {
      editor.chain().focus().updateAttributes('image', attrs).run();
    } else {
      editor.chain().focus().setImage(attrs).run();
    }
    closeImageDialog();
  }

  function removeImage() {
    editor?.chain().focus().deleteSelection().run();
    closeImageDialog();
  }

  function submitEmbed({ src }) {
    editor?.chain().focus().setYoutubeEmbed({ src }).run();
    setEmbedOpen(false);
  }

  async function handleImageUpload(file, onProgress) {
    const url = await uploadImageFile(file, onProgress);
    // Report the upload so the parent can clean it up if it never gets saved.
    if (url) onAssetUploaded?.(url);
    return url;
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.modeBar}>
        <div className={styles.segmented} role="group" aria-label="Editor mode">
          <button
            type="button"
            className={`${styles.segmented__btn} ${mode === 'preview' ? styles['segmented__btn--active'] : ''}`}
            onClick={() => setMode('preview')}
            aria-pressed={mode === 'preview'}
          >
            👁 Preview
          </button>
          <button
            type="button"
            className={`${styles.segmented__btn} ${mode === 'edit' ? styles['segmented__btn--active'] : ''}`}
            onClick={() => setMode('edit')}
            aria-pressed={mode === 'edit'}
          >
            ✏️ Edit
          </button>
        </div>
        <span className={styles.srOnly} role="status" aria-live="polite">
          {mode === 'edit' ? 'Edit mode' : 'Preview mode'}
        </span>
      </div>

      {mode === 'edit' && (
        <Toolbar
          editor={editor}
          disabled={disabled}
          onImageClick={openImageDialog}
          onEmbedClick={() => setEmbedOpen(true)}
        />
      )}

      {/* The editor instance stays mounted in both modes so switching never
          reloads or loses unsaved content; we only toggle visibility. */}
      <div className={mode === 'edit' ? styles.editArea : styles.hidden}>
        <EditorContent editor={editor} />
      </div>

      {mode === 'preview' && (
        <div
          className={`${styles.preview} markdown-content`}
          // Content is sanitized by mdToHtml before it reaches the DOM.
          dangerouslySetInnerHTML={{ __html: previewHtml || '<p class="empty">(empty)</p>' }}
        />
      )}

      <ImageDialog
        open={imageDialog.open}
        editing={imageDialog.editing}
        initial={imageDialog.initial}
        onUpload={handleImageUpload}
        onSubmit={submitImage}
        onRemove={removeImage}
        onClose={closeImageDialog}
      />

      <EmbedDialog
        open={embedOpen}
        onSubmit={submitEmbed}
        onClose={() => setEmbedOpen(false)}
      />
    </div>
  );
}
