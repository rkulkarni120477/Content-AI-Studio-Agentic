# Content AI Studio — React Frontend

Enterprise-grade React frontend for the Content AI Studio eLearning content generation platform, migrated from Python Streamlit.

---

## Setup

### Prerequisites

- Node.js 18+
- npm 9+

### Installation

```bash
cd frontend
cp .env.example .env
# Edit .env with your backend URL and settings
npm install
```

---

## Development Commands

```bash
# Start dev server (http://localhost:3000)
npm run dev

# Production build
npm run build

# Preview production build locally
npm run preview

# Lint
npm run lint
```

---

## Production Build

```bash
npm run build
# Output: frontend/dist/
# Serve dist/ with any static file server (Nginx, S3, etc.)
```

---

## Environment Configuration

Copy `.env.example` → `.env` and fill in:

| Variable | Description | Default |
|---|---|---|
| `VITE_API_BASE_URL` | Backend base URL. **Leave empty in dev** — requests go through the Vite proxy. Set to the backend origin only in a real deployment. | _(empty)_ |
| `VITE_TOKEN_KEY` | JWT localStorage key | `content_ai_jwt` |
| `VITE_ENABLE_ASYNC_GENERATION` | Use Celery background jobs | `true` |
| `VITE_ENABLE_PLAGIARISM_CHECK` | Enable Copyleaks integration | `true` |
| `VITE_JOB_POLL_INTERVAL_MS` | Background job polling interval | `3000` |
| `VITE_API_TIMEOUT_MS` | API request timeout | `120000` |

---

## Architecture

### Folder Structure

```
src/
  app/           — Store, hooks, routes
  services/      — Axios client + all API endpoint constants
  features/      — Feature-based modules (auth, dashboard, cdd, etc.)
    <feature>/
      components/   — Feature-specific components
      pages/        — Page components
      services/     — Service layer (API calls)
      <feature>Slice.js    — Redux Toolkit slice + selectors
      <feature>Thunks.js   — Async thunks
  components/
    common/       — Reusable UI: Button, Input, Select, Table, Modal, etc.
    layout/       — AppLayout, Sidebar, Header, PageContainer, ProtectedRoute
  hooks/          — Shared hooks: useAuth, useDebounce, usePagination
  utils/          — constants, helpers, storage, validation schemas
  styles/         — SCSS variables, mixins, global stylesheet
  assets/         — Static assets
```

### Data Flow (enforced for every feature)

```
Component
  → dispatch(thunk)
  → thunk calls service function
  → service calls api.get/post (Axios instance)
  → response → Redux slice update
  → component re-renders from selector
```

### State Strategy

| State Type | Location |
|---|---|
| Auth (user, token, role) | `authSlice` |
| Project/course selection | `dashboardSlice` |
| Active CDD / Blueprint IDs | `cddSlice` / `blueprintSlice` |
| Sidebar config (model, audience) | `dashboardSlice` |
| Workflow filters | `workflowSlice` |
| Per-page loading/error | feature slice |
| Form state | React Hook Form (local) |
| Transient UI state (modals, tabs) | `useState` (local) |

### API Client (`src/services/apiClient.js`)

- `baseURL` from `VITE_API_BASE_URL`
- Request interceptor: attaches `Authorization: Bearer <token>` from localStorage
- Response interceptor:
  - 401 → dispatches `auth:logout` event → clears token
  - Network error → normalized error message
  - All errors → normalized `{ message, status, isApiError }` shape
- `api.download()` for binary responses (DOCX, PDF, CSV)
- `api.upload()` for multipart/form-data

### Protected Routes

`ProtectedRoute` component wraps any route requiring authentication. It:
1. Waits for the boot-time `fetchMe` call to complete (`isAuthChecked`)
2. Redirects to `/login` if unauthenticated
3. Accepts `requiredRole` prop for role-based access control

---

## Migration Notes

### Streamlit → React mapping

| Streamlit | React equivalent |
|---|---|
| `st.session_state` (global) | Redux Toolkit slice |
| `st.session_state` (page-local) | `useState` |
| `st.form` | React Hook Form + Zod validation |
| `st.dataframe` | Reusable `Table` component |
| `st.status()` progress | Job polling via Redux thunk |
| `st.tabs()` | CSS-styled tab bar (local state) |
| `st.columns()` | CSS Grid / Flexbox |
| `st.expander()` | `Modal` or `<details>` |
| `st.download_button()` | `downloadBlob()` utility |
| `st.file_uploader()` | `FileUpload` component |
| `st.metric()` | KPI card components |
| `st.area_chart/bar_chart` | Recharts `AreaChart`/`BarChart` |
| `st.toast()` / deferred notifications | `react-hot-toast` |
| Celery job polling (sleep + rerun) | Redux thunk + `setTimeout` self-scheduling |
| Cookie JWT auth | localStorage JWT + Axios interceptor |

### Backend Contracts

The frontend assumes the backend exposes REST endpoints matching `src/services/endpoints.js`. If the backend is still Streamlit-only with no REST API, a FastAPI or Django REST Framework layer must be built first.

Endpoints marked with `// TODO` in `editorService.js` and `blueprintService.js` have unclear response shapes — verify with the backend team before wiring up.

### Workflow State Machine

The frontend enforces the same state transitions as the Streamlit backend:
- `draft` → `in_review` (Author, Admin)
- `in_review` → `approved` | `changes_requested` (Reviewer, Admin)
- `approved` → `published` (Reviewer, Admin)
- Any → `archived` (Admin)

Role enforcement is done both client-side (hides buttons) and must also be enforced server-side.

---

## Key Packages

| Package | Purpose |
|---|---|
| `@reduxjs/toolkit` | State management |
| `react-redux` | React-Redux bindings |
| `axios` | HTTP client |
| `react-router-dom` | Client-side routing |
| `react-hook-form` | Form state management |
| `zod` + `@hookform/resolvers` | Schema validation |
| `sass` | SCSS modules |
| `recharts` | Analytics charts |
| `react-hot-toast` | Toast notifications |
| `date-fns` | Date utilities (available, not required by default) |
