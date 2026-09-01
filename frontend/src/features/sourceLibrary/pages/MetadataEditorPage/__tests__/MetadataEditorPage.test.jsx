// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';

const getMetadata = vi.fn();
const patchMetadata = vi.fn();
const revertMetadata = vi.fn();
const listDocuments = vi.fn();

vi.mock('@features/sourceLibrary/services/sourceLibraryApi', () => ({
  default: {
    getMetadata: (...a) => getMetadata(...a),
    patchMetadata: (...a) => patchMetadata(...a),
    revertMetadata: (...a) => revertMetadata(...a),
    listDocuments: (...a) => listDocuments(...a),
  },
}));

vi.mock('@components/layout/PageContainer/PageContainer', () => ({
  default: ({ children }) => <div>{children}</div>,
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const { default: authReducer } = await import('@features/auth/authSlice');
const { default: dashboardReducer } = await import('@features/dashboard/dashboardSlice');
const { default: MetadataEditorPage } = await import('../MetadataEditorPage');

const SAMPLE = {
  job_id: 'job-1',
  document_summary: {
    name: 'Marketing Handbook 2024',
    type: 'pdf',
    size: '2.0 KB',
    pages: 182,
    purpose: 'style',
  },
  ai_metadata: {
    title: 'Marketing Handbook 2024',
    author: 'Cengage Marketing Team',
    subject: 'Digital Marketing',
    language: 'en-US',
    description: 'Comprehensive internal reference',
    keywords: ['marketing', 'brand'],
  },
  taxonomy_standards: {
    subject_area: 'Business & Management',
    domain: 'Marketing',
    subdomain: 'Digital Marketing',
    blooms_level: 'Apply',
    skill_level: 'Intermediate',
    learning_standards: ['AACSB Marketing Competencies'],
    skills_mapped: ['Campaign Planning'],
  },
  relationships: {
    series_collection: 'Marketing Reference Library 2024',
    related_documents: [{ job_id: 'job-2', label: 'Brand_Guidelines_V3.docx' }],
    prerequisites: [],
    cross_references: [{ job_id: '', label: 'Social Media Policy' }],
  },
  ai_baseline_available: true,
  provenance: { ai_extracted_at: '2024-01-15T00:00:00' },
  limits: { keywords_max: 12 },
};

function renderPage(tab = 'ai') {
  const store = configureStore({
    reducer: { auth: authReducer, dashboard: dashboardReducer },
    preloadedState: {
      auth: { user: { role: 'admin' }, token: 't', isAuthenticated: true },
      dashboard: { selectedProject: { id: 1 }, selectedCourse: { id: 10, name: 'Course' } },
    },
  });
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[`/workspace/10/sources/job-1/metadata?tab=${tab}`]}>
        <Routes>
          <Route path="/workspace/:courseId/sources/:jobId/metadata" element={<MetadataEditorPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

describe('MetadataEditorPage', () => {
  beforeEach(() => {
    getMetadata.mockResolvedValue(SAMPLE);
    patchMetadata.mockImplementation(async (_id, body) => ({ ...SAMPLE, ...body }));
    revertMetadata.mockResolvedValue(SAMPLE);
    listDocuments.mockResolvedValue({ documents: [] });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders three tabs', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: 'AI Metadata' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Taxonomy & Standards' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Relationships' })).toBeTruthy();
  });

  it('renders AI Metadata fields', async () => {
    renderPage('ai');
    expect(await screen.findByDisplayValue('Marketing Handbook 2024')).toBeTruthy();
    expect(screen.getByText('marketing')).toBeTruthy();
    expect(screen.getByText(/Extracted by AI on/)).toBeTruthy();
  });

  it('switches to taxonomy tab via query param', async () => {
    renderPage('taxonomy');
    expect(await screen.findByDisplayValue('Business & Management')).toBeTruthy();
    expect(screen.getByText('Learning standards')).toBeTruthy();
  });

  it('switches to relationships tab', async () => {
    renderPage('relationships');
    expect(await screen.findByDisplayValue('Marketing Reference Library 2024')).toBeTruthy();
    expect(screen.getByText('Brand_Guidelines_V3.docx')).toBeTruthy();
    expect(screen.getByText(/No prerequisites/)).toBeTruthy();
  });

  it('shows unsaved changes when editing', async () => {
    renderPage('ai');
    const title = await screen.findByDisplayValue('Marketing Handbook 2024');
    fireEvent.change(title, { target: { value: 'Changed Title' } });
    expect(screen.getByText(/Unsaved changes/)).toBeTruthy();
  });

  it('calls save API', async () => {
    renderPage('ai');
    const title = await screen.findByDisplayValue('Marketing Handbook 2024');
    fireEvent.change(title, { target: { value: 'Changed Title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save metadata' }));
    await waitFor(() => expect(patchMetadata).toHaveBeenCalled());
  });

  it('calls revert API', async () => {
    renderPage('ai');
    fireEvent.click(await screen.findByRole('button', { name: 'Revert to AI values' }));
    await waitFor(() => expect(revertMetadata).toHaveBeenCalledWith('job-1', expect.any(Object)));
  });
});
