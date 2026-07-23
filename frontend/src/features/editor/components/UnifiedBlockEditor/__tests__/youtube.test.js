// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { toYouTubeEmbedUrl } from '../youtube';

describe('toYouTubeEmbedUrl', () => {
  it('normalizes a standard watch URL', () => {
    expect(toYouTubeEmbedUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ'))
      .toBe('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('normalizes a youtu.be short URL', () => {
    expect(toYouTubeEmbedUrl('https://youtu.be/dQw4w9WgXcQ'))
      .toBe('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('normalizes a shorts URL', () => {
    expect(toYouTubeEmbedUrl('https://www.youtube.com/shorts/dQw4w9WgXcQ'))
      .toBe('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('passes through an existing embed URL', () => {
    expect(toYouTubeEmbedUrl('https://www.youtube.com/embed/dQw4w9WgXcQ'))
      .toBe('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('strips extra query params', () => {
    expect(toYouTubeEmbedUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s'))
      .toBe('https://www.youtube.com/embed/dQw4w9WgXcQ');
  });

  it('rejects non-YouTube URLs', () => {
    expect(toYouTubeEmbedUrl('https://vimeo.com/12345')).toBeNull();
    expect(toYouTubeEmbedUrl('https://evil.example.com/embed/x')).toBeNull();
    expect(toYouTubeEmbedUrl('not a url')).toBeNull();
    expect(toYouTubeEmbedUrl('')).toBeNull();
  });
});
