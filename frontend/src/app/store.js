import { configureStore } from '@reduxjs/toolkit';
import toast from 'react-hot-toast';
import { applyTerminology, labelsFromState } from '@config/tenantLabels';
import authReducer       from '@features/auth/authSlice';
import dashboardReducer  from '@features/dashboard/dashboardSlice';
import cddReducer        from '@features/cdd/cddSlice';
import blueprintReducer  from '@features/blueprint/blueprintSlice';
import generateReducer   from '@features/generate/generateSlice';
import editorReducer     from '@features/editor/editorSlice';
import feedbackReducer   from '@features/feedback/feedbackSlice';
import workflowReducer   from '@features/workflow/workflowSlice';
import styleReducer      from '@features/style/styleSlice';
import promptsReducer    from '@features/prompts/promptsSlice';
import analyticsReducer  from '@features/analytics/analyticsSlice';
import importReducer     from '@features/import/importSlice';
import jobsReducer       from '@features/jobs/jobsSlice';

const store = configureStore({
  reducer: {
    auth:      authReducer,
    dashboard: dashboardReducer,
    cdd:       cddReducer,
    blueprint: blueprintReducer,
    generate:  generateReducer,
    editor:    editorReducer,
    feedback:  feedbackReducer,
    workflow:  workflowReducer,
    style:     styleReducer,
    prompts:   promptsReducer,
    analytics: analyticsReducer,
    import:    importReducer,
    jobs:      jobsReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        // Ignore non-serializable values in these paths (file blobs, dates).
        // 'meta.arg.file' covers the import wizard's validate/start thunks,
        // whose arg carries the raw File object.
        ignoredActionPaths: ['payload.file', 'payload.blob', 'meta.arg.file'],
        ignoredPaths:       ['editor.exportBlob'],
      },
    }),
  devTools: import.meta.env.DEV,
});

// Rewrite default product terms in every toast using the current tenant labels,
// so notifications stay in sync with the rest of the UI even when a thunk
// still has a hardcoded "Style"/"CDD"/"Blueprint"/"Title" string.
// A message already built from the tenant labels passes { localized: true } to
// skip this: rewriting it again renames a label that is itself a default term
// (a tenant whose CDD is called "Blueprint" would see its Blueprint label).
function wrapToastMethod(method) {
  const original = toast[method].bind(toast);
  toast[method] = (message, opts) => {
    if (opts?.localized) {
      const rest = { ...opts };
      delete rest.localized;
      return original(message, rest);
    }
    if (typeof message === 'string') {
      return original(applyTerminology(message, labelsFromState(store.getState)), opts);
    }
    return original(message, opts);
  };
}
wrapToastMethod('success');
wrapToastMethod('error');

export default store;
