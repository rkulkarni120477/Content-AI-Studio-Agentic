// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import UnifiedBlockEditor from '../UnifiedBlockEditor';

afterEach(cleanup);

const SAMPLE = '# Lesson Title\n\nAn intro paragraph with **bold** text.\n\n- Point one\n- Point two';

describe('UnifiedBlockEditor', () => {
  it('mounts and defaults to Preview mode showing rendered content', () => {
    render(<UnifiedBlockEditor content={SAMPLE} onChange={() => {}} />);

    const preview = screen.getByRole('button', { name: /Preview/i });
    expect(preview.getAttribute('aria-pressed')).toBe('true');

    // Rendered (not raw) markdown is visible. The editor stays mounted (hidden)
    // in preview mode, so the heading legitimately appears in both trees.
    expect(screen.getAllByText('Lesson Title').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('bold').length).toBeGreaterThanOrEqual(1);
  });

  it('switches to Edit mode and reveals the formatting toolbar without crashing', () => {
    render(<UnifiedBlockEditor content={SAMPLE} onChange={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: /Edit/i }));

    expect(screen.getByRole('toolbar', { name: /Text formatting/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Bold' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Insert or edit link' })).toBeTruthy();
  });

  it('does not fire onChange on initial load (no phantom autosave)', () => {
    const onChange = vi.fn();
    render(<UnifiedBlockEditor content={SAMPLE} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders an empty-state placeholder for blank content in Preview', () => {
    render(<UnifiedBlockEditor content="" onChange={() => {}} />);
    expect(screen.getByText('(empty)')).toBeTruthy();
  });

  it('opens the image dialog from the toolbar in Edit mode', () => {
    render(<UnifiedBlockEditor content={SAMPLE} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Edit/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Insert or edit image' }));
    expect(screen.getByRole('dialog', { name: /Insert image/i })).toBeTruthy();
    expect(screen.getByLabelText(/Alternative text/i)).toBeTruthy();
  });

  it('propagates an inserted image to onChange (not dependent on editor focus)', () => {
    const onChange = vi.fn();
    render(<UnifiedBlockEditor content="Hello world." onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /Edit/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Insert or edit image' }));
    fireEvent.change(screen.getByLabelText(/image URL/i), {
      target: { value: 'https://cdn.example.com/pic.png' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Insert' }));

    const emitted = onChange.mock.calls.map((c) => c[0]).join('\n');
    expect(emitted).toContain('cdn.example.com/pic.png');
  });

  it('opens the video embed dialog and validates YouTube URLs', () => {
    render(<UnifiedBlockEditor content={SAMPLE} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Edit/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Insert video' }));
    const dialog = screen.getByRole('dialog', { name: /Insert video/i });
    expect(dialog).toBeTruthy();

    const url = screen.getByLabelText(/YouTube URL/i);
    // A non-YouTube URL keeps Insert disabled.
    fireEvent.change(url, { target: { value: 'https://vimeo.com/123' } });
    expect(screen.getByRole('button', { name: 'Insert' }).disabled).toBe(true);
    // A valid YouTube URL enables it.
    fireEvent.change(url, { target: { value: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' } });
    expect(screen.getByRole('button', { name: 'Insert' }).disabled).toBe(false);
  });
});
