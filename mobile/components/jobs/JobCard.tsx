import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { C } from '../../constants/colors';
import { Job } from '../../api/jobs';
import { ScoreBadge } from './ScoreBadge';
import { StatusChip } from './StatusChip';

export function JobCard({ job }: { job: Job }) {
  const router = useRouter();
  return (
    <TouchableOpacity
      style={s.card}
      onPress={() => router.push(`/job/${encodeURIComponent(job.company)}/${job.job_id}`)}
      activeOpacity={0.75}
    >
      <View style={s.row}>
        <Text style={s.company} numberOfLines={1}>{job.company}</Text>
        <ScoreBadge score={job.match_score} />
      </View>
      <Text style={s.title} numberOfLines={2}>{job.title}</Text>
      <View style={s.meta}>
        <StatusChip status={job.status as any} />
        {job.location ? <Text style={s.loc} numberOfLines={1}>{job.location}</Text> : null}
      </View>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  card:    { backgroundColor: C.surface, borderRadius: C.radius, padding: 14, marginBottom: 8, borderWidth: 1, borderColor: C.border },
  row:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  company: { fontSize: 12, color: C.muted, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.4, flex: 1, marginRight: 8 },
  title:   { fontSize: 15, color: C.text, fontWeight: '500', marginBottom: 8 },
  meta:    { flexDirection: 'row', alignItems: 'center', gap: 8 },
  loc:     { fontSize: 11, color: C.muted, flex: 1 },
});
