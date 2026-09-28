import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { format, subDays } from 'date-fns';
import { buildAnalyticsParams } from '../analyticsParams';
import { DATE_RANGE_PRESETS } from '@utils/constants';

describe('buildAnalyticsParams', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-15T12:00:00Z'));
  });
  afterEach(() => { vi.useRealTimers(); });

  it('spans exactly 7 calendar days for Last 7 Days, not 8', () => {
    const now = new Date();
    const params = buildAnalyticsParams({ dateRange: DATE_RANGE_PRESETS.LAST_7_DAYS });
    expect(params.date_from).toBe(format(subDays(now, 6), 'yyyy-MM-dd'));
  });

  it('spans exactly 30 calendar days for Last 30 Days', () => {
    const now = new Date();
    const params = buildAnalyticsParams({ dateRange: DATE_RANGE_PRESETS.LAST_30_DAYS });
    expect(params.date_from).toBe(format(subDays(now, 29), 'yyyy-MM-dd'));
  });

  it('always sends the browser tz offset so the server can align to local calendar days', () => {
    const params = buildAnalyticsParams({ dateRange: DATE_RANGE_PRESETS.ALL_TIME });
    expect(params.tz_offset_minutes).toBe(new Date().getTimezoneOffset());
  });
});
