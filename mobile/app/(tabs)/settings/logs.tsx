import { useState } from 'react';
import { FlatList, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';
import { useLogs, useLogFile } from '../../../api/settings';
import { Spinner } from '../../../components/ui/Spinner';

function LogDetail({ filename }: { filename: string }) {
  const { data, isLoading } = useLogFile(filename);
  if (isLoading) return <Spinner />;
  return (
    <ScrollView style={s.logScroll}>
      <Text style={s.logText}>{data?.content ?? ''}</Text>
    </ScrollView>
  );
}

export default function LogsScreen() {
  const { data: files, isLoading } = useLogs();
  const [selected, setSelected] = useState<string | null>(null);

  if (isLoading) return <Spinner />;

  return (
    <View style={s.root}>
      <Stack.Screen options={{ title: selected ?? 'Logs' }} />
      {selected ? (
        <>
          <TouchableOpacity onPress={() => setSelected(null)} style={s.back}>
            <Text style={s.backLabel}>← Back to files</Text>
          </TouchableOpacity>
          <LogDetail filename={selected} />
        </>
      ) : (
        <FlatList
          data={files}
          keyExtractor={(f: any) => f.filename ?? f}
          contentContainerStyle={s.list}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.row} onPress={() => setSelected(item.filename ?? item)} activeOpacity={0.7}>
              <Text style={s.filename}>{item.filename ?? item}</Text>
              {item.size_bytes != null && <Text style={s.size}>{(item.size_bytes / 1024).toFixed(1)} KB</Text>}
            </TouchableOpacity>
          )}
        />
      )}
    </View>
  );
}

const s = StyleSheet.create({
  root:     { flex: 1, backgroundColor: C.bg },
  list:     { padding: 16 },
  row:      { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: C.surface, borderRadius: C.radius, padding: 12, marginBottom: 8, borderWidth: 1, borderColor: C.border },
  filename: { fontSize: 13, color: C.text, flex: 1 },
  size:     { fontSize: 11, color: C.muted },
  back:     { padding: 16 },
  backLabel:{ fontSize: 13, color: C.accent },
  logScroll:{ flex: 1, padding: 16 },
  logText:  { fontSize: 11, color: C.muted, fontFamily: 'monospace' },
});
