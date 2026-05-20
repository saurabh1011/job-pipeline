import { useCallback, useMemo, useState } from 'react';
import { FlatList, RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { C } from '../../constants/colors';
import { ALL_STATUSES } from '../../constants/statuses';
import { useJobs } from '../../api/jobs';
import { JobCard } from '../../components/jobs/JobCard';
import { EmptyState } from '../../components/ui/EmptyState';
import { Spinner } from '../../components/ui/Spinner';

const STATUS_FILTERS = [{ key: '', label: 'All' }, ...ALL_STATUSES.map(s => ({ key: s.key, label: s.label }))];

export default function JobsScreen() {
  const [activeStatus, setActiveStatus] = useState('');
  const { data: jobs, isLoading, refetch, isRefetching } = useJobs();

  const filtered = useMemo(() => {
    if (!jobs) return [];
    if (!activeStatus) return jobs;
    return jobs.filter(j => j.status === activeStatus);
  }, [jobs, activeStatus]);

  const renderItem = useCallback(({ item }: any) => <JobCard job={item} />, []);

  if (isLoading) return <Spinner />;

  return (
    <View style={s.root}>
      <View style={s.header}>
        <Text style={s.heading}>Jobs</Text>
        <Text style={s.count}>{filtered.length}</Text>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={s.filterBar}
        contentContainerStyle={s.filterContent}
      >
        {STATUS_FILTERS.map(f => (
          <TouchableOpacity
            key={f.key}
            style={[s.chip, activeStatus === f.key && s.chipActive]}
            onPress={() => setActiveStatus(f.key)}
          >
            <Text style={[s.chipLabel, activeStatus === f.key && s.chipLabelActive]}>{f.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <FlatList
        data={filtered}
        keyExtractor={i => `${i.company}-${i.job_id}`}
        renderItem={renderItem}
        contentContainerStyle={s.list}
        ListEmptyComponent={<EmptyState text="No jobs match this filter." />}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={C.accent} />}
      />
    </View>
  );
}

const s = StyleSheet.create({
  root:            { flex: 1, backgroundColor: C.bg },
  header:          { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingTop: 60, paddingBottom: 8 },
  heading:         { fontSize: 24, fontWeight: '700', color: C.text },
  count:           { fontSize: 14, color: C.muted },
  filterBar:       { maxHeight: 44, flexGrow: 0 },
  filterContent:   { paddingHorizontal: 12, gap: 6, alignItems: 'center' },
  chip:            { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 16, backgroundColor: C.surface2, borderWidth: 1, borderColor: C.border },
  chipActive:      { backgroundColor: C.accent + '22', borderColor: C.accent },
  chipLabel:       { fontSize: 12, color: C.muted, fontWeight: '500' },
  chipLabelActive: { color: C.accent },
  list:            { padding: 12, paddingTop: 8 },
});
