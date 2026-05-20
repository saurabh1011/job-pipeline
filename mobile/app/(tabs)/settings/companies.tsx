import { useState } from 'react';
import { Alert, FlatList, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { useCompanies, useRemoveCompany, useAddCompany, useDetectAts } from '../../../api/settings';
import { Input } from '../../../components/ui/Input';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

export default function CompaniesScreen() {
  const { data: companies, isLoading } = useCompanies();
  const remove = useRemoveCompany();
  const add = useAddCompany();
  const detect = useDetectAts();
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');

  async function handleAdd() {
    if (!newName.trim() || !newUrl.trim()) { Alert.alert('Name and URL are required'); return; }
    await add.mutateAsync({ name: newName.trim(), ats: 'unknown', board_slug: newUrl.trim() });
    setNewName(''); setNewUrl('');
  }

  async function handleDetect(name: string) {
    try {
      const r = await detect.mutateAsync(name);
      Alert.alert('ATS Detected', `${r.ats ?? 'unknown'}`);
    } catch { Alert.alert('Detection failed'); }
  }

  if (isLoading) return <Spinner />;

  return (
    <View style={s.root}>
      <Stack.Screen options={{ title: 'Companies' }} />
      <FlatList
        data={companies}
        keyExtractor={c => c.name}
        contentContainerStyle={s.list}
        ListHeaderComponent={
          <View style={s.addBox}>
            <Text style={s.sectionTitle}>Add Company</Text>
            <Input label="Name" value={newName} onChangeText={setNewName} placeholder="Acme Corp" />
            <Input label="Careers URL" value={newUrl} onChangeText={setNewUrl} placeholder="https://…" keyboardType="url" autoCapitalize="none" />
            <Btn label="Add" onPress={handleAdd} loading={add.isPending} variant="primary" />
          </View>
        }
        renderItem={({ item }) => (
          <View style={s.row}>
            <View style={s.rowText}>
              <Text style={s.name}>{item.name}</Text>
              {item.ats ? <Text style={s.ats}>{item.ats}</Text> : null}
            </View>
            <TouchableOpacity onPress={() => handleDetect(item.name)} style={s.actionBtn}>
              <Text style={s.actionLabel}>ATS?</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => remove.mutate(item.name)} style={[s.actionBtn, s.deleteBtn]}>
              <Text style={[s.actionLabel, { color: C.red }]}>Remove</Text>
            </TouchableOpacity>
          </View>
        )}
      />
    </View>
  );
}

const s = StyleSheet.create({
  root:        { flex: 1, backgroundColor: C.bg },
  list:        { padding: 16 },
  addBox:      { backgroundColor: C.surface, borderRadius: C.radius, padding: 14, marginBottom: 16, borderWidth: 1, borderColor: C.border },
  sectionTitle:{ fontSize: 12, fontWeight: '700', color: C.muted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 12 },
  row:         { flexDirection: 'row', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 12, marginBottom: 8, borderWidth: 1, borderColor: C.border },
  rowText:     { flex: 1 },
  name:        { fontSize: 14, color: C.text, fontWeight: '500' },
  ats:         { fontSize: 11, color: C.muted, marginTop: 2 },
  actionBtn:   { paddingHorizontal: 10, paddingVertical: 6 },
  deleteBtn:   {},
  actionLabel: { fontSize: 12, color: C.accent, fontWeight: '600' },
});
