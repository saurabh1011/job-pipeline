import { useState } from 'react';
import { Alert, FlatList, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { useProfiles, useCreateProfile, useRenameProfile, useDeleteProfile, Profile } from '../../../api/profiles';
import { Input } from '../../../components/ui/Input';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

export default function ProfileScreen() {
  const { data: profiles, isLoading } = useProfiles();
  const create = useCreateProfile();
  const rename = useRenameProfile();
  const del = useDeleteProfile();
  const [newName, setNewName] = useState('');
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);

  async function handleCreate() {
    if (!newName.trim()) return;
    await create.mutateAsync(newName.trim());
    setNewName('');
  }

  async function handleRename() {
    if (!editing) return;
    await rename.mutateAsync({ id: editing.id, name: editing.name });
    setEditing(null);
  }

  function confirmDelete(p: Profile) {
    Alert.alert('Delete Profile', `Delete "${p.name}"? This cannot be undone.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => del.mutate(p.profile_id) },
    ]);
  }

  if (isLoading) return <Spinner />;

  return (
    <View style={s.root}>
      <Stack.Screen options={{ title: 'Profiles' }} />
      <FlatList
        data={profiles}
        keyExtractor={p => p.profile_id}
        contentContainerStyle={s.list}
        ListHeaderComponent={
          <View style={s.addBox}>
            <Text style={s.sectionTitle}>New Profile</Text>
            <Input value={newName} onChangeText={setNewName} placeholder="Profile name" />
            <Btn label="Create" onPress={handleCreate} loading={create.isPending} variant="primary" />
          </View>
        }
        renderItem={({ item }) => (
          editing?.id === item.profile_id ? (
            <View style={s.editRow}>
              <Input style={s.editInput} value={editing.name} onChangeText={n => setEditing(e => e ? { ...e, name: n } : e)} />
              <Btn label="Save" onPress={handleRename} loading={rename.isPending} variant="primary" small />
              <Btn label="Cancel" onPress={() => setEditing(null)} small />
            </View>
          ) : (
            <View style={s.row}>
              <Text style={s.name}>{item.name}</Text>
              {item.is_legacy && <Text style={s.badge}>legacy</Text>}
              <TouchableOpacity onPress={() => setEditing({ id: item.profile_id, name: item.name })} style={s.actionBtn}>
                <Text style={s.action}>Rename</Text>
              </TouchableOpacity>
              {!item.is_legacy && (
                <TouchableOpacity onPress={() => confirmDelete(item)} style={s.actionBtn}>
                  <Text style={[s.action, { color: C.red }]}>Delete</Text>
                </TouchableOpacity>
              )}
            </View>
          )
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
  name:        { flex: 1, fontSize: 14, color: C.text, fontWeight: '500' },
  badge:       { fontSize: 10, color: C.muted, backgroundColor: C.surface2, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, marginRight: 8 },
  actionBtn:   { paddingHorizontal: 8 },
  action:      { fontSize: 12, color: C.accent, fontWeight: '600' },
  editRow:     { flexDirection: 'row', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 8, marginBottom: 8, borderWidth: 1, borderColor: C.accent, gap: 8 },
  editInput:   { flex: 1, marginBottom: 0 },
});
