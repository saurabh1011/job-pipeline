import { useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { usePreferences, useSavePreferences, Prefs } from '../../../api/settings';
import { Input } from '../../../components/ui/Input';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

function TagEditor({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState('');
  return (
    <View style={s.tagSection}>
      <Text style={s.tagLabel}>{label}</Text>
      <View style={s.tagRow}>
        {value.map(t => (
          <View key={t} style={s.tag}>
            <Text style={s.tagText}>{t}</Text>
            <Text style={s.tagRemove} onPress={() => onChange(value.filter(x => x !== t))}> ×</Text>
          </View>
        ))}
      </View>
      <View style={s.tagInputRow}>
        <Input
          style={s.tagInput}
          value={text}
          onChangeText={setText}
          placeholder="Add…"
          onSubmitEditing={() => { if (text.trim()) { onChange([...value, text.trim()]); setText(''); } }}
          returnKeyType="done"
        />
      </View>
    </View>
  );
}

export default function PreferencesScreen() {
  const { data: prefs, isLoading } = usePreferences();
  const save = useSavePreferences();
  const [form, setForm] = useState<Prefs | null>(null);

  useEffect(() => { if (prefs && !form) setForm(prefs); }, [prefs]);

  async function handleSave() {
    if (!form) return;
    try { await save.mutateAsync(form); Alert.alert('Saved'); }
    catch { Alert.alert('Save failed'); }
  }

  if (isLoading || !form) return <Spinner />;

  const set = (k: keyof Prefs, v: any) => setForm(f => f ? { ...f, [k]: v } : f);

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Stack.Screen options={{ title: 'Preferences' }} />

      <Input label="Match Threshold" value={String(form.match_threshold)} onChangeText={v => set('match_threshold', parseFloat(v) || 0)} keyboardType="numeric" />
      <Input label="LLM Provider" value={form.llm_provider} onChangeText={v => set('llm_provider', v)} autoCapitalize="none" />

      <View style={s.row}>
        <Text style={s.switchLabel}>US Only</Text>
        <Switch value={form.us_only} onValueChange={v => set('us_only', v)} trackColor={{ true: C.accent }} />
      </View>

      <TagEditor label="Title Keywords"         value={form.title_keywords}              onChange={v => set('title_keywords', v)} />
      <TagEditor label="Exclude Title Keywords" value={form.title_exclude_keywords}      onChange={v => set('title_exclude_keywords', v)} />
      <TagEditor label="Preferred Locations"    value={form.preferred_locations}          onChange={v => set('preferred_locations', v)} />
      <TagEditor label="Acceptable Locations"   value={form.acceptable_locations}         onChange={v => set('acceptable_locations', v)} />
      <TagEditor label="Excluded Location Keywords" value={form.excluded_location_keywords} onChange={v => set('excluded_location_keywords', v)} />

      <Btn label="Save Preferences" onPress={handleSave} loading={save.isPending} variant="primary" style={s.saveBtn} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:        { flex: 1, backgroundColor: C.bg },
  content:     { padding: 16 },
  row:         { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: C.border },
  switchLabel: { fontSize: 14, color: C.text },
  tagSection:  { marginBottom: 16 },
  tagLabel:    { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6, color: C.muted, marginBottom: 6 },
  tagRow:      { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 6 },
  tag:         { flexDirection: 'row', backgroundColor: C.surface2, borderRadius: 4, borderWidth: 1, borderColor: C.border, paddingHorizontal: 8, paddingVertical: 4 },
  tagText:     { fontSize: 12, color: C.text },
  tagRemove:   { fontSize: 12, color: C.red },
  tagInputRow: {},
  tagInput:    {},
  saveBtn:     { marginTop: 8, marginBottom: 40 },
});
