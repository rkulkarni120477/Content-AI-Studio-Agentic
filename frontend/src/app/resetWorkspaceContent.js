import { resetEditor } from '@features/editor/editorSlice';
import { resetBlueprintContext } from '@features/blueprint/blueprintSlice';
import { resetCddContext } from '@features/cdd/cddSlice';

/**
 * Clear the per-title (per-course) transient content that lives in Redux, so
 * switching titles can never render one title's Blueprint components, CDD, or
 * editor lessons under another. Each workspace page refetches its own data on
 * mount, so this only drops stale state — it never removes anything the newly
 * opened title legitimately owns.
 *
 * The generate slice is deliberately NOT reset here: a generation job (and its
 * results) is tagged with its own title via `jobCourseId` and gated on the
 * viewed course in GeneratePage, so it stays put and reappears when the user
 * returns to the title it ran on — clearing it would destroy a result the user
 * should still see.
 *
 * Dispatched from WorkspaceLayout whenever the active course id changes.
 */
export function resetWorkspaceContent() {
  return (dispatch) => {
    dispatch(resetEditor());
    dispatch(resetBlueprintContext());
    dispatch(resetCddContext());
  };
}
