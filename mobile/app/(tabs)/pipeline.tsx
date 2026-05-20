import { useState } from 'react';
import { Platform, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { C } from '../../constants/colors';
import { useTriggerRun, useTask, useRuns } from '../../api/pipeline';
import { Btn } from '../../components/ui/Btn';

function TaskStatusView({ taskId }: { taskId: string }) {
  const { data } = useTask(taskId);
  if (!data) return null;
  const done = data.status === 'done' || data.status === 'error';
  const color = data.status === 'error' ? C.red : data.status === 'done' ? C.green : C.yellow;
  return (
    <View style={s.taskBox}>
      <View style={s.taskHeader}>
        <Text style={[s.taskStatus, { color }]}>{data.status.toUpperCase()}</Text>
      </View>
      <ScrollView style={s.logScroll} nestedScrollEnabled>
        <Text style={s.logText}>{(data.logs ?? []).join('\n')}</Text>
      </ScrollView>
    </View>
  );
}

export default function PipelineScreen() {
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const trigger = useTriggerRun();
  const { data: runs, refetch, isRefetching } = useRuns();

  async function runPipeline(mode: string | null) {
    const res = await trigger.mutateAsync(mode ? { action: mode } : {});
    setActiveTaskId(res.task_id);
  }

  return (
    <ScrollView
      style={s.root}
      contentContainerStyle={s.content}
      refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={C.accent} />}
    >
      <Text style={s.heading}>Pipeline</Text>

      <View style={s.section}>
        <Text style={s.sectionTitle}>Run</Text>
        <View style={s.btnRow}>
          <Btn label="HTTP only" onPress={() => runPipeline('http')} loading={trigger.isPending} style={s.runBtn} />
          <Btn label="Playwright" onPress={() => runPipeline('playwright')} loading={trigger.isPending} style={s.runBtn} />
          <Btn label="All" onPress={() => runPipeline(null)} loading={trigger.isPending} variant="primary" style={s.runBtn} />
        </View>
      </View>

      {activeTaskId && <TaskStatusView taskId={activeTaskId} />}

      {runs && runs.length > 0 && (
        <View style={s.section}>
          <Text style={s.sectionTitle}>Recent Runs</Text>
          {runs.slice(0, 10).map((r: any) => (
            <View key={r.id} style={s.runRow}>
              <Text style={s.runDate}>{new Date(r.created_at).toLocaleString()}</Text>
              <Text style={[s.runStatus, { color: r.status === 'done' ? C.green : r.status === 'error' ? C.red : C.yellow }]}>
                {r.status}
              </Text>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:        { flex: 1, backgroundColor: C.bg },
  content:     { padding: 16, paddingTop: 60 },
  heading:     { fontSize: 24, fontWeight: '700', color: C.text, marginBottom: 20 },
  section:     { marginBottom: 20 },
  sectionTitle:{ fontSize: 12, fontWeight: '700', color: C.muted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 },
  btnRow:      { flexDirection: 'row', gap: 8 },
  runBtn:      { flex: 1 },
  taskBox:     { backgroundColor: C.surface, borderRadius: C.radius, borderWidth: 1, borderColor: C.border, marginBottom: 20, overflow: 'hidden' },
  taskHeader:  { padding: 10, borderBottomWidth: 1, borderBottomColor: C.border },
  taskStatus:  { fontSize: 12, fontWeight: '700', letterSpacing: 0.8 },
  logScroll:   { maxHeight: 300, padding: 10 },
  logText:     { fontSize: 11, color: C.muted, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' } as any,
  runRow:      { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: C.border },
  runDate:     { fontSize: 13, color: C.text },
  runStatus:   { fontSize: 12, fontWeight: '600' },
});
