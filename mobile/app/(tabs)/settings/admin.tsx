import { useState } from 'react';
import { Alert, FlatList, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { useAllowedEmails, useAdminUsers, useAddEmail, useRemoveEmail } from '../../../api/admin';
import { Input } from '../../../components/ui/Input';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';

export default function AdminScreen() {
  const { data: emails, isLoading: loadingEmails } = useAllowedEmails();
  const { data: users, isLoading: loadingUsers } = useAdminUsers();
  const addEmail = useAddEmail();
  const removeEmail = useRemoveEmail();
  const [newEmail, setNewEmail] = useState('');

  async function handleAdd() {
    if (!newEmail.trim()) return;
    await addEmail.mutateAsync(newEmail.trim());
    setNewEmail('');
  }

  if (loadingEmails || loadingUsers) return <Spinner />;

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Stack.Screen options={{ title: 'Admin' }} />

      <Text style={s.sectionTitle}>Allowed Emails</Text>
      <View style={s.addRow}>
        <Input style={s.emailInput} value={newEmail} onChangeText={setNewEmail} placeholder="user@example.com" keyboardType="email-address" autoCapitalize="none" />
        <Btn label="Add" onPress={handleAdd} loading={addEmail.isPending} variant="primary" small />
      </View>
      {(emails ?? []).map(e => (
        <View key={e.email} style={s.row}>
          <Text style={s.email}>{e.email}</Text>
          <TouchableOpacity onPress={() => removeEmail.mutate(e.email)}>
            <Text style={s.del}>Remove</Text>
          </TouchableOpacity>
        </View>
      ))}

      <Text style={[s.sectionTitle, { marginTop: 24 }]}>Users</Text>
      {(users ?? []).map((u: any) => (
        <View key={u.id ?? u.email} style={s.row}>
          <View>
            <Text style={s.email}>{u.email}</Text>
            <Text style={s.sub}>{u.role ?? 'user'}</Text>
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:        { flex: 1, backgroundColor: C.bg },
  content:     { padding: 16 },
  sectionTitle:{ fontSize: 12, fontWeight: '700', color: C.muted, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 },
  addRow:      { flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginBottom: 12 },
  emailInput:  { flex: 1, marginBottom: 0 },
  row:         { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 12, marginBottom: 8, borderWidth: 1, borderColor: C.border },
  email:       { fontSize: 14, color: C.text },
  sub:         { fontSize: 11, color: C.muted, marginTop: 2 },
  del:         { fontSize: 12, color: C.red, fontWeight: '600' },
});
