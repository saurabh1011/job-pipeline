import { useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { useSchedule, useSaveSchedule, useClearSchedule } from '../../../api/profiles';
import { Input } from '../../../components/ui/Input';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

export default function ScheduleScreen() {
  const [profileId] = useState<string | null>(null); // uses active profile from cookie server-side
  const { data: schedule, isLoading } = useSchedule(profileId);
  const save = useSaveSchedule(profileId);
  const clear = useClearSchedule(profileId);

  const [enabled, setEnabled] = useState(false);
  const [time1, setTime1] = useState('08:00');
  const [time2, setTime2] = useState('');
  const [tz, setTz] = useState(TZ);

  useEffect(() => {
    if (schedule) {
      setEnabled(schedule.enabled ?? false);
      setTime1(schedule.time_1 ?? '08:00');
      setTime2(schedule.time_2 ?? '');
      setTz(schedule.timezone ?? TZ);
    }
  }, [schedule]);

  async function handleSave() {
    try {
      await save.mutateAsync({ time_1: time1 || null, time_2: time2 || null, timezone: tz, enabled });
      Alert.alert('Schedule saved');
    } catch { Alert.alert('Save failed'); }
  }

  async function handleClear() {
    try { await clear.mutateAsync(); Alert.alert('Schedule cleared'); }
    catch { Alert.alert('Clear failed'); }
  }

  if (isLoading) return <Spinner />;

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Stack.Screen options={{ title: 'Schedule' }} />

      {schedule === null && (
        <View style={s.notice}>
          <Text style={s.noticeText}>Schedule not available for this account type.</Text>
        </View>
      )}

      {schedule !== null && (
        <>
          <View style={s.row}>
            <Text style={s.switchLabel}>Enabled</Text>
            <Switch value={enabled} onValueChange={setEnabled} trackColor={{ true: C.accent }} />
          </View>
          <Input label="Time 1 (HH:MM)" value={time1} onChangeText={setTime1} placeholder="08:00" />
          <Input label="Time 2 (optional)" value={time2} onChangeText={setTime2} placeholder="18:00" />
          <Input label="Timezone" value={tz} onChangeText={setTz} autoCapitalize="none" />
          <View style={s.btnRow}>
            <Btn label="Save" onPress={handleSave} loading={save.isPending} variant="primary" style={s.btn} />
            <Btn label="Clear" onPress={handleClear} loading={clear.isPending} variant="danger" style={s.btn} />
          </View>
        </>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:        { flex: 1, backgroundColor: C.bg },
  content:     { padding: 16 },
  row:         { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: C.border },
  switchLabel: { fontSize: 14, color: C.text },
  btnRow:      { flexDirection: 'row', gap: 8, marginTop: 8 },
  btn:         { flex: 1 },
  notice:      { backgroundColor: C.surface2, borderRadius: C.radius, padding: 14, marginBottom: 16 },
  noticeText:  { fontSize: 13, color: C.muted },
});
