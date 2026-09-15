import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { selectSelectedCourse } from '@features/dashboard/dashboardSlice';
import { resumeJobsThunk, invalidateJobsPoll } from './jobsThunks';

/**
 * Layout-level job reattachment. Call once from WorkspaceLayout / SelectionLayout
 * so leaving Style/CDD/Blueprint/Generate does not stop progress tracking.
 */
export default function JobTracker({ courseId: courseIdProp } = {}) {
  const { courseId: routeCourseId } = useParams();
  const selected = useAppSelector(selectSelectedCourse);
  const dispatch = useAppDispatch();

  const courseId = Number(
    courseIdProp || routeCourseId || selected?.id || 0,
  ) || null;

  useEffect(() => {
    if (!courseId) return undefined;
    invalidateJobsPoll();
    dispatch(resumeJobsThunk({ courseId }));
    return () => {
      invalidateJobsPoll();
    };
  }, [courseId, dispatch]);

  return null;
}
