import { configureStore } from '@reduxjs/toolkit';
import authReducer       from '@features/auth/authSlice';
import dashboardReducer  from '@features/dashboard/dashboardSlice';
import cddReducer        from '@features/cdd/cddSlice';
import blueprintReducer  from '@features/blueprint/blueprintSlice';
import generateReducer   from '@features/generate/generateSlice';
import editorReducer     from '@features/editor/editorSlice';
import workflowReducer   from '@features/workflow/workflowSlice';
import styleReducer      from '@features/style/styleSlice';
import promptsReducer    from '@features/prompts/promptsSlice';
import analyticsReducer  from '@features/analytics/analyticsSlice';

const store = configureStore({
  reducer: {
    auth:      authReducer,
    dashboard: dashboardReducer,
    cdd:       cddReducer,
    blueprint: blueprintReducer,
    generate:  generateReducer,
    editor:    editorReducer,
    workflow:  workflowReducer,
    style:     styleReducer,
    prompts:   promptsReducer,
    analytics: analyticsReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        // Ignore non-serializable values in these paths (file blobs, dates)
        ignoredActionPaths: ['payload.file', 'payload.blob'],
        ignoredPaths:       ['editor.exportBlob'],
      },
    }),
  devTools: import.meta.env.DEV,
});

export default store;
