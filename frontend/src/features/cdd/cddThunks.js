import { createAsyncThunk } from '@reduxjs/toolkit';
import { cddService } from './services/cddService';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';
import { resolveProjectId } from '@utils/workspaceContext';
import toast from 'react-hot-toast';

export const fetchCddsThunk = createAsyncThunk(
  'cdd/fetch',
  async (courseId, { getState, rejectWithValue }) => {
    try {
      const cid = Number(courseId);
      let projectId = resolveProjectId(getState);
      if (!projectId && cid) {
        try {
          const course = await dashboardService.getCourse(cid);
          projectId = course?.project_id ?? null;
        } catch {
          /* course lookup failed — list without project filter */
        }
      }
      const items = await cddService.listCdds(cid, {
        project_id: projectId ?? undefined,
        course_id: cid,
      });
      let activeCdd = null;
      if (cid) {
        try {
          activeCdd = await cddService.getActiveCddForCourse(cid);
        } catch {
          const course = getState()?.dashboard?.selectedCourse;
          const activeId = course?.id === cid ? course.active_cdd_id : null;
          if (activeId) {
            try {
              activeCdd = await cddService.getCdd(activeId);
            } catch {
              activeCdd = items.find((c) => c.id === activeId) || null;
            }
          }
        }
      }
      return { items, activeCdd };
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const generateCddThunk = createAsyncThunk(
  'cdd/generate',
  async (payload, { rejectWithValue }) => {
    try {
      if (!payload?.project_id) {
        return rejectWithValue('Select a project before generating a CDD.');
      }
      const result = await cddService.generateCdd(payload);
      toast.success('CDD generated and set as active.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const setActiveCddThunk = createAsyncThunk(
  'cdd/setActive',
  async ({ cddId, courseId }, { rejectWithValue }) => {
    try {
      const result = await cddService.setActiveCdd(cddId, courseId);
      toast.success('Active CDD updated.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const fetchCddVersionsThunk = createAsyncThunk(
  'cdd/fetchVersions',
  async (cddId, { rejectWithValue }) => {
    try { return await cddService.getVersions(cddId); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const commitCddVersionThunk = createAsyncThunk(
  'cdd/commitVersion',
  async ({ cddId, data }, { rejectWithValue }) => {
    try {
      const result = await cddService.commitVersion(cddId, data);
      toast.success('New version saved.');
      return result;
    } catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);

export const exportCddThunk = createAsyncThunk(
  'cdd/export',
  async ({ cddId, format }, { rejectWithValue }) => {
    try { return await cddService.exportCdd(cddId, format); }
    catch (e) { return rejectWithValue(extractErrorMessage(e)); }
  },
);
