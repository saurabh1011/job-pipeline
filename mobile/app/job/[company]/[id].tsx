import { useState } from 'react';
import { Alert, Linking, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { C } from '../../../constants/colors';
import { ALL_STATUSES } from '../../../constants/statuses';
import { useJob, usePatchJob, useSaveCoverLetter, useRescoreJob, useGenerateCoverLetter } from '../../../api/jobs';
import { useTask } from '../../../api/pipeline';
import { ScoreBadge } from '../../../components/jobs/ScoreBadge';
import { StatusChip } from '../../../components/jobs/StatusChip';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

function TaskProgress({ taskId }: { taskId: string }) {
  const { data } = useTask(taskId);
  if (!data) return null;
  const done = data.status === 'done' || data.status === 'error';
  return (
    <View style={s.taskBox}>
      <Text style={[s.taskStatus, { color: done ? (data.status === 'error' ? C.red : C.green) : C.yellow }]}>
        {data.status}
      </Text>
    </View>
  );
}

export default function JobDetailScreen() {
  const { company, id } = useLocalSearchParams<{ company: string; id: string }>();
  const router = useRouter();
  const { data: job, isLoading, refetch } = useJob(company!, id!);
  const patch = usePatchJob();
  const saveClLetter = useSaveCoverLetter();
  const rescore = useRescoreJob();
  const genCL = useGenerateCoverLetter();

  const [editingCL, setEditingCL] = useState(false);
  const [clText, setClText] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);

  if (isLoading || !job) return <Spinner />;

  async function handleRescore() {
    const r = await rescore.mutateAsync({ company: company!, jobId: id! });
    setTaskId(r.task_id);
  }

  async function handleGenCL() {
    const r = await genCL.mutateAsync({ company: company!, jobId: id! });
    setTaskId(r.task_id);
    setTimeout(() => refetch(), 5000);
  }

  async function handleSaveCL() {
    await saveClLetter.mutateAsync({ company: company!, jobId: id!, content: clText });
    setEditingCL(false);
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Stack.Screen options={{ title: job.company, headerShown: true }} />

      <View style={s.titleRow}>
        <Text style={s.title}>{job.title}</Text>
        <ScoreBadge score={job.match_score} />
      </View>
      <View style={s.metaRow}>
        <StatusChip status={job.status as any} />
        {job.location ? <Text style={s.meta}>{job.location}</Text> : null}
      </View>

      <View style={s.statusRow}>
        <Text style={s.sectionTitle}>Set Status</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={s.statusChips}>
            {ALL_STATUSES.map(st => (
              <TouchableOpacity
                key={st.key}
                style={[s.statusBtn, { borderColor: st.color, backgroundColor: job.status === st.key ? st.color + '33' : 'transparent' }]}
                onPress={() => patch.mutate({ company: company!, jobId: id!, status: st.key })}
              >
                <Text style={[s.statusBtnLabel, { color: st.color }]}>{st.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </ScrollView>
      </View>

      <View style={s.btnRow}>
        {job.apply_url ? (
          <Btn label="Apply" onPress={() => Linking.openURL(job.apply_url!)} variant="primary" small style={s.btn} />
        ) : null}
        <Btn label="Rescore" onPress={handleRescore} loading={rescore.isPending} small style={s.btn} />
        <Btn label="Gen CL" onPress={handleGenCL} loading={genCL.isPending} small style={s.btn} />
      </View>

      {taskId && <TaskProgress taskId={taskId} />}

      {job.match_summary ? (
        <View style={s.section}>
          <Text style={s.sectionTitle}>Match Summary</Text>
          <Text style={s.bodyText}>{job.match_summary}</Text>
        </View>
      ) : null}

      {job.cover_letter ? (
        <View style={s.section}>
          <View style={s.sectionHeader}>
            <Text style={s.sectionTitle}>Cover Letter</Text>
            <TouchableOpacity onPress={() => { setClText(job.cover_letter!); setEditingCL(true); }}>
              <Text style={s.editLink}>Edit</Text>
            </TouchableOpacity>
          </View>
          {editingCL ? (
            <>
              <TextInput
                style={s.clInput}
                value={clText}
                onChangeText={setClText}
                multiline
                textAlignVertical="top"
              />
              <View style={s.btnRow}>
                <Btn label="Save" onPress={handleSaveCL} loading={saveClLetter.isPending} variant="primary" small style={s.btn} />
                <Btn label="Cancel" onPress={() => setEditingCL(false)} small style={s.btn} />
              </View>
            </>
          ) : (
            <Text style={s.bodyText}>{job.cover_letter}</Text>
          )}
        </View>
      ) : null}

      {job.description ? (
        <View style={s.section}>
          <Text style={s.sectionTitle}>Description</Text>
          <Text style={s.bodyText}>{job.description}</Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:         { flex: 1, backgroundColor: C.bg },
  content:      { padding: 16, paddingBottom: 60 },
  titleRow:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 },
  title:        { fontSize: 20, fontWeight: '700', color: C.text, flex: 1, marginRight: 8 },
  metaRow:      { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 },
  meta:         { fontSize: 12, color: C.muted },
  statusRow:    { marginBottom: 16 },
  statusChips:  { flexDirection: 'row', gap: 6 },
  statusBtn:    { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 4, borderWidth: 1 },
  statusBtnLabel:{ fontSize: 11, fontWeight: '600' },
  btnRow:       { flexDirection: 'row', gap: 8, marginBottom: 16 },
  btn:          { flex: 1 },
  section:      { marginBottom: 20 },
  sectionHeader:{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  sectionTitle: { fontSize: 12, fontWeight: '700', color: C.muted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 },
  bodyText:     { fontSize: 13, color: C.text, lineHeight: 20 },
  editLink:     { fontSize: 12, color: C.accent },
  clInput:      { backgroundColor: C.surface2, borderWidth: 1, borderColor: C.border, borderRadius: C.radius, color: C.text, padding: 10, fontSize: 13, minHeight: 200, marginBottom: 8 },
  taskBox:      { backgroundColor: C.surface, borderRadius: C.radius, padding: 10, marginBottom: 12 },
  taskStatus:   { fontSize: 12, fontWeight: '700', letterSpacing: 0.8 },
});
