import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { C } from '../../../constants/colors';
import { authStore } from '../../../store/auth';
import { useQueryClient } from '@tanstack/react-query';

const ITEMS = [
  { label: 'Companies',   route: '/settings/companies',   desc: 'Manage which companies to search' },
  { label: 'Preferences', route: '/settings/preferences', desc: 'Score weights, filters, keywords' },
  { label: 'Resume',      route: '/settings/resume',      desc: 'Upload or replace your resume' },
  { label: 'Schedule',    route: '/settings/schedule',    desc: 'Daily run schedule' },
  { label: 'Profiles',    route: '/settings/profile',     desc: 'Manage search profiles' },
  { label: 'Admin',       route: '/settings/admin',       desc: 'Allowed emails and users' },
  { label: 'Logs',        route: '/settings/logs',        desc: 'View server logs' },
];

export default function SettingsIndex() {
  const router = useRouter();
  const qc = useQueryClient();

  async function handleSignOut() {
    await authStore.clearToken();
    qc.clear();
    router.replace('/login');
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Text style={s.heading}>Settings</Text>

      <View style={s.group}>
        {ITEMS.map((item, i) => (
          <TouchableOpacity
            key={item.route}
            style={[s.row, i < ITEMS.length - 1 && s.rowBorder]}
            onPress={() => router.push(item.route as any)}
            activeOpacity={0.7}
          >
            <View style={s.rowText}>
              <Text style={s.rowLabel}>{item.label}</Text>
              <Text style={s.rowDesc}>{item.desc}</Text>
            </View>
            <Text style={s.chevron}>›</Text>
          </TouchableOpacity>
        ))}
      </View>

      <View style={[s.group, { marginTop: 24 }]}>
        <TouchableOpacity style={s.row} onPress={handleSignOut} activeOpacity={0.7}>
          <Text style={[s.rowLabel, { color: C.red }]}>Sign Out</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  root:      { flex: 1, backgroundColor: C.bg },
  content:   { padding: 16, paddingTop: 60 },
  heading:   { fontSize: 24, fontWeight: '700', color: C.text, marginBottom: 20 },
  group:     { backgroundColor: C.surface, borderRadius: C.radius, borderWidth: 1, borderColor: C.border, overflow: 'hidden' },
  row:       { flexDirection: 'row', alignItems: 'center', padding: 14 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: C.border },
  rowText:   { flex: 1 },
  rowLabel:  { fontSize: 15, color: C.text, fontWeight: '500' },
  rowDesc:   { fontSize: 12, color: C.muted, marginTop: 2 },
  chevron:   { fontSize: 20, color: C.muted },
});
