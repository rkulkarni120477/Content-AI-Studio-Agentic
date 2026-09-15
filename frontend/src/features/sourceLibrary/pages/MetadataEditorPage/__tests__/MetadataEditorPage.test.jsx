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
const getOverview = vi.fn();
const getUnits = vi.fn();
const getUnitDetail = vi.fn();
const patchUnitMetadata = vi.fn();
const retagContent = vi.fn();

vi.mock('@features/sourceLibrary/services/sourceLibraryApi', () => ({
  default: {
    getMetadata: (...a) => getMetadata(...a),
    patchMetadata: (...a) => patchMetadata(...a),
    revertMetadata: (...a) => revertMetadata(...a),
    listDocuments: (...a) => listDocuments(...a),
    getOverview: (...a) => getOverview(...a),
    getUnits: (...a) => getUnits(...a),
    getUnitDetail: (...a) => getUnitDetail(...a),
    patchUnitMetadata: (...a) => patchUnitMetadata(...a),
    retagContent: (...a) => retagContent(...a),
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

function renderPage(tab = 'ai', { view = false, section = 'document' } = {}) {
  const store = configureStore({
    reducer: { auth: authReducer, dashboard: dashboardReducer },
    preloadedState: {
      auth: { user: { role: 'admin' }, token: 't', isAuthenticated: true },
      dashboard: { selectedProject: { id: 1 }, selectedCourse: { id: 10, name: 'Course' } },
    },
  });
  const path = view
    ? `/workspace/10/sources/job-1/view?tab=${tab}&section=${section}`
    : `/workspace/10/sources/job-1/metadata?tab=${tab}&section=${section}`;
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/workspace/:courseId/sources/:jobId/view" element={<MetadataEditorPage />} />
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
    getOverview.mockResolvedValue({ preview: 'doc preview', tagging_failed_count: 0 });
    getUnits.mockResolvedValue({
      units: [{
        unit_id: 'u1',
        page_number: '1-1',
        tagging_status: 'ok',
        title: 'Landing gear',
        topics: ['oleo strut'],
        acs_codes: ['AM.I.D.K1'],
        summary: 'Oleo strut basics.',
      }],
    });
    getUnitDetail.mockResolvedValue({
      unit: {
        unit_id: 'u1',
        title: 'Landing gear',
        text: 'Page one raw content',
        topics: ['oleo strut'],
        metadata: {
          page_number: '1-1',
          summary: 'Oleo strut basics.',
          acs_codes: ['AM.I.D.K1'],
          topics: ['oleo strut'],
        },
      },
    });
    patchUnitMetadata.mockResolvedValue({
      unit: {
        unit_id: 'u1',
        title: 'Landing gear updated',
        text: 'Page one raw content',
        topics: ['oleo strut', 'brakes'],
        metadata: {
          page_number: '1-1',
          summary: 'Updated summary',
          acs_codes: ['AM.I.D.K1'],
          topics: ['oleo strut', 'brakes'],
        },
      },
    });
    retagContent.mockResolvedValue({ retagged: 0, tagging_failed_count: 0 });
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
    expect(screen.queryByRole('button', { name: 'Content' })).toBeNull();
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

  it('view mode shows Content tab and read-only fields without save footer', async () => {
    renderPage('ai', { view: true });
    expect(await screen.findByRole('heading', { name: 'View document' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Content' })).toBeTruthy();
    const title = screen.getByDisplayValue('Marketing Handbook 2024');
    expect(title.readOnly).toBe(true);
    expect(screen.queryByRole('button', { name: 'Save metadata' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Revert to AI values' })).toBeNull();
    expect(screen.queryByText(/Unsaved changes/)).toBeNull();
  });

  it('shows left Sections dropdown on view and edit', async () => {
    renderPage('ai', { view: true });
    expect(await screen.findByLabelText('Sections')).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Whole document' })).toBeTruthy();
    expect(screen.getByRole('option', { name: /p\. 1-1/ })).toBeTruthy();

    cleanup();
    renderPage('ai', { view: false });
    expect(await screen.findByLabelText('Sections')).toBeTruthy();
  });

  it('selecting a section shows section tags instead of document fields', async () => {
    renderPage('ai', { view: true, section: 'u1' });
    expect(await screen.findByDisplayValue('Landing gear')).toBeTruthy();
    expect(screen.getByDisplayValue('Oleo strut basics.')).toBeTruthy();
    expect(screen.getByText('oleo strut')).toBeTruthy();
    expect(screen.queryByDisplayValue('Cengage Marketing Team')).toBeNull();
    expect(screen.getByText(/Section tags from page content tagging/)).toBeTruthy();
  });

  it('section taxonomy tab shows ACS codes only', async () => {
    renderPage('taxonomy', { view: true, section: 'u1' });
    expect(await screen.findByText('AM.I.D.K1')).toBeTruthy();
    expect(screen.queryByDisplayValue('Business & Management')).toBeNull();
    expect(screen.getByText(/ACS codes tagged for this section/)).toBeTruthy();
  });

  it('section relationships tab shows document-level empty state', async () => {
    renderPage('relationships', { view: true, section: 'u1' });
    expect(await screen.findByText(/Relationships are document-level/)).toBeTruthy();
  });

  it('view Content tab has no Sections select and shows unit text for a section', async () => {
    renderPage('content', { view: true, section: 'u1' });
    expect(await screen.findByDisplayValue('Page one raw content')).toBeTruthy();
    // One Sections control lives on the left; Content tab itself has none.
    expect(screen.getAllByLabelText('Sections')).toHaveLength(1);
    expect(screen.queryByText('Background processing is still running')).toBeNull();
  });

  it('view Content tab shows a processing banner while ingest is still running', async () => {
    getOverview.mockResolvedValue({
      preview: 'doc preview',
      status: 'processing',
      tagging_failed_count: 0,
    });
    renderPage('content', { view: true, section: 'u1' });
    expect(await screen.findByText(/Background processing is still running/)).toBeTruthy();
  });

  it('edit mode saves section metadata via patchUnitMetadata', async () => {
    renderPage('ai', { view: false, section: 'u1' });
    const title = await screen.findByDisplayValue('Landing gear');
    fireEvent.change(title, { target: { value: 'Landing gear updated' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save metadata' }));
    await waitFor(() => expect(patchUnitMetadata).toHaveBeenCalledWith(
      'job-1',
      'u1',
      expect.objectContaining({
        title: 'Landing gear updated',
        summary: 'Oleo strut basics.',
        topics: ['oleo strut'],
        acs_codes: ['AM.I.D.K1'],
      }),
      expect.any(Object),
    ));
    expect(patchMetadata).not.toHaveBeenCalled();
  });
});
