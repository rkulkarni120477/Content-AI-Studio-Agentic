// @vitest-environment jsdom
/**
 * Phase 0: Source Library taxonomy filters come from uiConfig, not hardcoded
 * client controls, and selected values reach the list API (with module →
 * module_name). Server-side filtering owns the result set — the page must not
 * re-filter only the currently loaded page.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';

const listDocuments = vi.fn();
const uiConfig = vi.fn();
const profile = vi.fn();
const getUploadPolicy = vi.fn();

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
    uploadDocument: () => Promise.resolve({}),
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

const CENGAGE_UI = {
  client_id: 'cengage',
  client_aliases: { cengage: 'cengage' },
  purpose_labels: { style: 'Style Reference Documents' },
  source_library: {
    taxonomy_filters: [
      { key: 'course_name', label: 'Course / Product', type: 'select' },
      { key: 'chapter', label: 'Chapter', type: 'select' },
      { key: 'module', label: 'Module / Section', type: 'select' },
      { key: 'learning_objective', label: 'Learning Objective', type: 'text' },
    ],
  },
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

function renderPage() {
  const store = configureStore({
    reducer: { auth: authReducer, dashboard: dashboardReducer },
    preloadedState: {
      auth: { user: { role: 'admin', client_id: 'cengage' }, token: 't', isAuthenticated: true },
      dashboard: {
        selectedProject: { id: 1, name: 'Cengage', client_name: 'cengage' },
        selectedCourse: null,
        selectedCluster: null,
      },
    },
  });
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/workspace/10/sources']}>
        <Routes>
          <Route path="/workspace/:courseId/sources" element={<><SourceLibraryPage /><LocationProbe /></>} />
          <Route path="/workspace/:courseId/sources/:jobId/view" element={<LocationProbe />} />
          <Route path="/workspace/:courseId/sources/:jobId/metadata" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  listDocuments.mockReset();
  uiConfig.mockReset();
  profile.mockReset();
  getUploadPolicy.mockReset();
  profile.mockResolvedValue({ access: { client_id: 'cengage', role: 'admin' } });
  uiConfig.mockResolvedValue(CENGAGE_UI);
  getUploadPolicy.mockResolvedValue({ supported_extensions: ['.pdf'], blocked_extensions: [] });
  listDocuments.mockResolvedValue({
    documents: [
      { job_id: 'j1', title: 'Ch 1 Guide', purpose: 'cdd', document_type: 'syllabus', status: 'processed', module_name: 'Section 3' },
      { job_id: 'j2', title: 'Other Doc', purpose: 'style', document_type: 'style_guide', status: 'processed', module_name: 'Section 1' },
    ],
    filter_options: {
      document_types: ['syllabus', 'style_guide'],
      status: ['processed'],
      course_name: ['Algebra'],
      chapter: ['1', '2'],
      module: ['Section 1', 'Section 3'],
      learning_objective: [],
    },
    ui_config: CENGAGE_UI,
  });
});

afterEach(cleanup);

describe('SourceLibraryPage taxonomy filters', () => {
  it('renders configured taxonomy filter labels and keys from uiConfig', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('Course / Product')).toBeTruthy());
    expect(screen.getByText('Chapter')).toBeTruthy();
    expect(screen.getByText('Module / Section')).toBeTruthy();
    expect(screen.getByText('Learning Objective')).toBeTruthy();
  });

  it('maps module to module_name when a taxonomy filter is selected', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('Module / Section')).toBeTruthy());
    const moduleSelect = screen.getByText('Module / Section').parentElement.querySelector('select');
    expect(moduleSelect).toBeTruthy();
    listDocuments.mockClear();
    fireEvent.change(moduleSelect, { target: { value: 'Section 3' } });
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    const params = listDocuments.mock.calls[0][0];
    expect(params.module_name).toBe('Section 3');
    expect(params).not.toHaveProperty('module');
  });

  it('still sends existing common filters to the API', async () => {
    renderPage();
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    listDocuments.mockClear();
    const purposeLabel = screen.getAllByText('Purpose').find((el) => el.tagName === 'LABEL');
    const purposeSelect = purposeLabel?.parentElement?.querySelector('select');
    expect(purposeSelect).toBeTruthy();
    fireEvent.change(purposeSelect, { target: { value: 'cdd' } });
    await waitFor(() => expect(listDocuments).toHaveBeenCalled());
    expect(listDocuments.mock.calls[0][0].purpose).toBe('cdd');
  });

  it('does not hide server-returned documents with client-side re-filtering', async () => {
    // Server already applied filters; both docs in the response must remain visible
    // even when a purpose filter is active in UI state (pagination correctness).
    listDocuments.mockResolvedValue({
      documents: [
        { job_id: 'j1', title: 'Kept By Server', purpose: 'style', document_type: 'syllabus', status: 'processed' },
        { job_id: 'j2', title: 'Also Kept', purpose: 'cdd', document_type: 'syllabus', status: 'processed' },
      ],
      filter_options: { document_types: ['syllabus'], status: ['processed'] },
      ui_config: CENGAGE_UI,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Kept By Server')).toBeTruthy());
    expect(screen.getByText('Also Kept')).toBeTruthy();
  });

  it('navigates to view page when View is clicked', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('Ch 1 Guide')).toBeTruthy());
    fireEvent.click(screen.getAllByRole('button', { name: 'View' })[0]);
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toContain('/workspace/10/sources/j1/view');
    });
    expect(screen.getByTestId('location').textContent).toContain('tab=ai');
  });
});
