export const ALL_STATUSES = [
  { key: 'new',          label: 'New',          color: '#6699cc' },
  { key: 'alerted',      label: 'Alerted',      color: '#d4a843' },
  { key: 'approved',     label: 'Approved',     color: '#4caf78' },
  { key: 'applied',      label: 'Applied',      color: '#44aacc' },
  { key: 'skipped',      label: 'Skipped',      color: '#666666' },
  { key: 'interviewing', label: 'Interviewing', color: '#aa77ff' },
  { key: 'rejected',     label: 'Rejected',     color: '#e05c5c' },
  { key: 'offer',        label: 'Offer',        color: '#44dd88' },
  { key: 'interesting',  label: 'Interesting',  color: '#ffaa33' },
] as const;

export type StatusKey = typeof ALL_STATUSES[number]['key'];

export const LOCATION_BUCKETS = [
  { key: 'new_york', label: 'New York', pattern: /new york|nyc/i },
  { key: 'remote',   label: 'Remote',   pattern: /remote/i },
  { key: 'sf',       label: 'SF',       pattern: /san francisco|sf,?\s*ca/i },
  { key: 'seattle',  label: 'Seattle',  pattern: /seattle/i },
  { key: 'austin',   label: 'Austin',   pattern: /austin/i },
  { key: 'chicago',  label: 'Chicago',  pattern: /chicago/i },
] as const;

export const ATS_LABELS: Record<string, string> = {
  greenhouse: 'GH', ashby: 'AS', lever: 'LV', google: 'GO', apple: 'AP',
  meta: 'ME', microsoft: 'MS', uber: 'UB', walmart: 'WM', netflix: 'NF',
  zillow: 'ZI', amazon: 'AZ', linkedin: 'LI',
};
