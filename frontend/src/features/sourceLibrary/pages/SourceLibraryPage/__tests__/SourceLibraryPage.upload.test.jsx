// @vitest-environment jsdom
/**
 * Phase 5: Source Library upload metadata from uiConfig.upload_metadata.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';

const listDocuments = vi.fn();
const uiConfig = vi.fn();
const profile = vi.fn();
const getUploadPolicy = vi.fn();
const uploadDocument = vi.fn();

vi.mock('@features/sourceLibrary/services/sourceLibraryApi', () => ({
  default: {
    listDocuments: (...a) => listDocuments(...a),
    uiConfig: (...a) => uiConfig(...a),
    profile: (...a) => profile(...a),
    getUploadPolicy: (...a) => getUploadPolicy(...a),
    getOverview: () => Promise.resolve({}),
    getPages: () => Promise.resolve({ pages: [] }),
    getUnits: () => Promise.resolve({ units: [] }),
    getUnitDetail: () => Promise.resolve({ unit: null }),
    searchSource: () => Promise.resolve({}),
    deleteDocument: () => Promise.resolve({}),
    uploadDocument: (...a) => uploadDocument(...a),
    scanFolder: () => Promise.resolve({}),
  },
}));

vi.mock('@hooks/useLabels', () => ({
  useLabels: () => ({
    style: 'Style',
    cdd: 'CDD',
    blueprint: 'Blueprint',
    title: 'Course',
  }),
}));

vi.mock('@components/layout/PageContainer/PageContainer', () => ({
  default: ({ children }) => <div>{children}</div>,
}));

vi.mock('@features/sourceLibrary/components/RetrievalStatus/RetrievalStatus', () => ({
  default: () => <span>status</span>,
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const { default: authReducer } = await import('@features/auth/authSlice');
const { default: dashboardReducer } = await import('@features/dashboard/dashboardSlice');
const { default: SourceLibraryPage } = await import('../SourceLibraryPage');

const LEGACY_UI = {
  client_id: 'demo',
  client_aliases: { demo: 'demo' },
  purpose_labels: { general_reference: 'General Reference' },
  source_library: { taxonomy_filters: [] },
  upload_metadata: {
    fields: [],
    document_type: {
      key: 'document_type',
      label: 'Document Type',
      control: 'text',
      placeholder: 'Optional, auto-detect if blank',
    },
  },
};

const AIM_UI = {
  ...LEGACY_UI,
  client_id: 'aim',
  upload_metadata: {
    document_type: {
      key: 'document_type',
      label: 'Document Type',
      control: 'select',
      options: ['syllabus', 'quiz_exam', 'other'],
    },
    fields: [
      { key: 'chapter', label: 'Chapter', control: 'text', order: 1 },
      { key: 'module_name', label: 'Module', control: 'text', order: 2 },
    ],
  },
};

function renderPage() {
  const store = configureStore({
    reducer: { auth: authReducer, dashboard: dashboardReducer },
    preloadedState: {
      auth: { user: { role: 'admin', client_id: 'aim' }, token: 't', isAuthenticated: true },
      dashboard: {
        selectedProject: { id: 1, name: 'AIM', client_name: 'aim' },
        selectedCourse: { id: 10, project_id: 1 },
        selectedCluster: null,
      },
    },
  });
  return render(
    <Provider store={store}>
      <MemoryRouter>
        <SourceLibraryPage />
      </MemoryRouter>
    </Provider>,
  );
}

async function openUploadForm() {
  renderPage();
  await waitFor(() => expect(uiConfig).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByText('+ Upload Sources')).toBeTruthy());
  fireEvent.click(screen.getByText('+ Upload Sources'));
  await waitFor(() => expect(screen.getByText('Upload source documents')).toBeTruthy());
}

function uploadForm() {
  return screen.getByText('Upload source documents').closest('form');
}

beforeEach(() => {
  listDocuments.mockReset();
  uiConfig.mockReset();
  profile.mockReset();
  getUploadPolicy.mockReset();
  uploadDocument.mockReset();
  profile.mockResolvedValue({ access: { client_id: 'aim', role: 'admin' } });
  getUploadPolicy.mockResolvedValue({ supported_extensions: ['.pdf'], blocked_extensions: [] });
  listDocuments.mockResolvedValue({
    documents: [],
    filter_options: { document_types: [], status: [] },
    ui_config: LEGACY_UI,
  });
  uploadDocument.mockResolvedValue({ ok: true });
});

afterEach(cleanup);

describe('SourceLibraryPage upload metadata', () => {
  it('preserves free-text document_type when no controlled values are configured', async () => {
    uiConfig.mockResolvedValue(LEGACY_UI);
    await openUploadForm();
    const form = uploadForm();
    const input = form.querySelector('input[name="document_type"]');
    expect(input).toBeTruthy();
    expect(form.querySelector('select[name="document_type"]')).toBeFalsy();
  });

  it('renders document_type select when schema-controlled values are provided', async () => {
    uiConfig.mockResolvedValue(AIM_UI);
    listDocuments.mockResolvedValue({
      documents: [],
      filter_options: { document_types: [], status: [] },
      ui_config: AIM_UI,
    });
    await openUploadForm();
    const form = uploadForm();
    await waitFor(() => expect(form.querySelector('select[name="document_type"]')).toBeTruthy());
    const select = form.querySelector('select[name="document_type"]');
    expect(select.querySelector('option[value="syllabus"]')).toBeTruthy();
    expect(form.querySelector('input[name="document_type"]')).toBeFalsy();
  });

  it('renders configured upload metadata fields', async () => {
    uiConfig.mockResolvedValue(AIM_UI);
    listDocuments.mockResolvedValue({
      documents: [],
      filter_options: { document_types: [], status: [] },
      ui_config: AIM_UI,
    });
    await openUploadForm();
    const form = uploadForm();
    await waitFor(() => expect(form.querySelector('input[name="chapter"]')).toBeTruthy());
    expect(form.querySelector('input[name="module_name"]')).toBeTruthy();
    expect(screen.queryByText('file_sha256')).toBeFalsy();
  });

  it('submits configured metadata values through the upload payload', async () => {
    uiConfig.mockResolvedValue(AIM_UI);
    listDocuments.mockResolvedValue({
      documents: [],
      filter_options: { document_types: [], status: [] },
      ui_config: AIM_UI,
    });
    await openUploadForm();
    const form = uploadForm();
    await waitFor(() => expect(form.querySelector('select[name="document_type"]')).toBeTruthy());
    const fileInput = form.querySelector('input[name="files"]');
    const file = new File(['hello'], 'notes.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.change(form.querySelector('select[name="purpose"]'), { target: { value: 'general_reference' } });
    fireEvent.change(form.querySelector('select[name="document_type"]'), { target: { value: 'syllabus' } });
    fireEvent.change(form.querySelector('input[name="chapter"]'), { target: { value: 'Ch 4' } });
    fireEvent.change(form.querySelector('input[name="module_name"]'), { target: { value: 'Mod A' } });
    fireEvent.click(screen.getByText('Upload and Ingest'));
    await waitFor(() => expect(uploadDocument).toHaveBeenCalled());
    const formData = uploadDocument.mock.calls[0][0];
    expect(formData.get('purpose')).toBe('general_reference');
    expect(formData.get('document_type')).toBe('syllabus');
    expect(formData.get('chapter')).toBe('Ch 4');
    expect(formData.get('module_name')).toBe('Mod A');
    expect(formData.get('course_id')).toBe('10');
  });

  it('keeps purpose as a structural upload field', async () => {
    uiConfig.mockResolvedValue(AIM_UI);
    await openUploadForm();
    expect(uploadForm().querySelector('select[name="purpose"]')).toBeTruthy();
  });
});
