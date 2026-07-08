import { describe, expect, it } from 'vitest';
import { clusterOptions } from '../clusters';

const group = (over = {}) => ({
  course_id: 1,
  course_name: 'C',
  cluster_id: 10,
  cluster_name: 'General',
  project_id: 100,
  project_name: 'Alpha',
  ...over,
});

describe('clusterOptions', () => {
  it('dedupes clusters by id, not by name', () => {
    const opts = clusterOptions([
      group({ course_id: 1, cluster_id: 10 }),
      group({ course_id: 2, cluster_id: 10 }),
    ]);
    expect(opts).toHaveLength(1);
    expect(opts[0][0]).toBe(10);
  });

  it('disambiguates same-named clusters across projects with the project name', () => {
    const opts = clusterOptions([
      group({ cluster_id: 10, project_id: 100, project_name: 'Alpha' }),
      group({ course_id: 2, cluster_id: 20, project_id: 200, project_name: 'Beta' }),
    ]);
    expect(opts.map(([, label]) => label).sort()).toEqual([
      'General — Alpha',
      'General — Beta',
    ]);
  });

  it('leaves unique names bare', () => {
    const opts = clusterOptions([
      group({ cluster_id: 10, cluster_name: 'Aviation' }),
      group({ course_id: 2, cluster_id: 20, cluster_name: 'Demo', project_name: 'Beta' }),
    ]);
    expect(opts.map(([, label]) => label)).toEqual(['Aviation', 'Demo']);
  });

  it('sorts by label and appends the "(No category)" bucket last', () => {
    const opts = clusterOptions([
      group({ cluster_id: 20, cluster_name: 'Zulu' }),
      group({ course_id: 2, cluster_id: 10, cluster_name: 'Alpha Cluster' }),
      group({ course_id: 3, cluster_id: null, cluster_name: null }),
    ]);
    expect(opts.map(([, label]) => label)).toEqual(['Alpha Cluster', 'Zulu', '(No category)']);
    expect(opts[2][0]).toBe('none');
  });

  it('falls back to id-based labels when names are missing', () => {
    const opts = clusterOptions([group({ cluster_name: null })]);
    expect(opts[0][1]).toBe('Category 10');
  });
});
