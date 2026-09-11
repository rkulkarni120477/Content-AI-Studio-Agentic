/**
 * PDF Export ticket — the Author must get a meaningful message, not a constant.
 *
 * Both export thunks used to swallow the server's explanation and show a bare
 * `toast.error('Export failed.')`, which is the toast in the bug report. And
 * because downloads are requested with responseType:'blob', the JSON error
 * envelope arrived as a Blob, so even reading it would have yielded axios's
 * "Request failed with status code 400" — a technical string, which the ticket
 * explicitly forbids showing to the Author.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('../services/editorService', () => ({
  editorService: { exportGeneration: vi.fn(), exportCourse: vi.fn() },
}));
vi.mock('@utils/helpers', async (importOriginal) => ({
  ...(await importOriginal()),
  downloadBlob: vi.fn(),
}));

import toast from 'react-hot-toast';
import { editorService } from '../services/editorService';
import { exportGenerationThunk, exportCourseThunk } from '../editorThunks';
import { unwrapBlobError } from '@services/apiClient';

const SERVER_MSG =
  'PDF export is not available on this server right now. Please export as DOCX '
  + 'or HTML instead, or ask your administrator to check the server configuration.';

const store = () => configureStore({ reducer: { noop: (s = {}) => s } });

describe('export failure messaging', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the server message instead of a generic "Export failed."', async () => {
    editorService.exportGeneration.mockRejectedValueOnce(new Error(SERVER_MSG));

    await store().dispatch(exportGenerationThunk({
      generationId: 1, format: 'pdf', template: 'default', filename: 'x.pdf',
    }));

    expect(toast.error).toHaveBeenCalledWith(SERVER_MSG);
    expect(toast.error).not.toHaveBeenCalledWith('Export failed.');
  });

  it('applies to the course export too', async () => {
    editorService.exportCourse.mockRejectedValueOnce(new Error(SERVER_MSG));

    await store().dispatch(exportCourseThunk({
      courseId: 1, format: 'pdf', template: 'default', filename: 'x.pdf',
    }));

    expect(toast.error).toHaveBeenCalledWith(SERVER_MSG);
  });

  it('still shows something human when the server said nothing', async () => {
    editorService.exportGeneration.mockRejectedValueOnce(new Error(''));

    await store().dispatch(exportGenerationThunk({
      generationId: 1, format: 'pdf', template: 'default', filename: 'x.pdf',
    }));

    const [msg] = toast.error.mock.calls[0];
    expect(msg).toBeTruthy();
    // Never the raw axios string — the ticket forbids technical detail here.
    expect(msg).not.toMatch(/status code|Request failed|undefined/i);
  });
});

describe('unwrapBlobError', () => {
  it('parses a JSON error envelope delivered as a Blob', async () => {
    const envelope = { error: { code: 'WORKFLOW_ERROR', message: SERVER_MSG } };
    const err = {
      response: {
        status: 400,
        data: new Blob([JSON.stringify(envelope)], { type: 'application/json' }),
      },
    };

    await unwrapBlobError(err);

    expect(err.response.data).toEqual(envelope);
  });

  it('leaves a real binary body alone', async () => {
    const pdf = new Blob(['%PDF-1.4'], { type: 'application/pdf' });
    const err = { response: { status: 200, data: pdf } };

    await unwrapBlobError(err);

    expect(err.response.data).toBe(pdf);
  });

  it('leaves an already-parsed JSON body alone', async () => {
    const err = { response: { status: 400, data: { detail: 'nope' } } };

    await unwrapBlobError(err);

    expect(err.response.data).toEqual({ detail: 'nope' });
  });
});
