import { subDays, startOfMonth, endOfMonth, subMonths, format } from 'date-fns';
import { DATE_RANGE_PRESETS } from '@utils/constants';

/** Map dashboard date-range preset to API query params. */
export function buildAnalyticsParams(filters = {}, getState) {
  const projectId = filters.project_id ?? getState?.()?.dashboard?.selectedProject?.id;
  const params = { tz_offset_minutes: new Date().getTimezoneOffset() };
  if (projectId) params.project_id = projectId;

  const now = new Date();
  switch (filters.dateRange) {
    // subDays(now, 7) spans 8 calendar days (today plus 7 days back) --
    // subtracting 6 gives exactly 7 days inclusive of today, matching the label.
    case DATE_RANGE_PRESETS.LAST_7_DAYS:
      params.date_from = format(subDays(now, 6), 'yyyy-MM-dd');
      break;
    case DATE_RANGE_PRESETS.LAST_30_DAYS:
      params.date_from = format(subDays(now, 29), 'yyyy-MM-dd');
      break;
    case DATE_RANGE_PRESETS.LAST_90_DAYS:
      params.date_from = format(subDays(now, 89), 'yyyy-MM-dd');
      break;
    case DATE_RANGE_PRESETS.THIS_MONTH:
      params.date_from = format(startOfMonth(now), 'yyyy-MM-dd');
      params.date_to = format(now, 'yyyy-MM-dd');
      break;
    case DATE_RANGE_PRESETS.LAST_MONTH: {
      const prev = subMonths(now, 1);
      params.date_from = format(startOfMonth(prev), 'yyyy-MM-dd');
      params.date_to = format(endOfMonth(prev), 'yyyy-MM-dd');
      break;
    }
    default:
      break;
  }
  return params;
}
