import { Alert, Platform, StyleSheet, Text, View } from 'react-native';
import { Stack } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { C } from '../../../constants/colors';
import { useResumeInfo, useDeleteResume } from '../../../api/profiles';
import { apiUpload } from '../../../api/client';
import { useQueryClient } from '@tanstack/react-query';
import { Btn } from '../../../components/ui/Btn';
import { Spinner } from '../../../components/ui/Spinner';
import { useState } from 'react';

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export default function ResumeScreen() {
  const { data: info, isLoading, refetch } = useResumeInfo();
  const deleteResume = useDeleteResume();
  const qc = useQueryClient();
  const [uploading, setUploading] = useState(false);

  async function handleUpload() {
    const result = await DocumentPicker.getDocumentAsync({ type: ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'] });
    if (result.canceled || !result.assets?.[0]) return;
    const file = result.assets[0];
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('resume', { uri: file.uri, type: file.mimeType ?? 'application/octet-stream', name: file.name } as any);
      await apiUpload('/api/resume', fd);
      qc.invalidateQueries({ queryKey: ['resume'] });
      Alert.alert('Uploaded', file.name);
    } catch (e: any) {
      Alert.alert('Upload failed', e.message);
    } finally {
      setUploading(false);
    }
  }

  if (isLoading) return <Spinner />;

  return (
    <View style={s.root}>
      <Stack.Screen options={{ title: 'Resume' }} />
      <View style={s.card}>
        {info ? (
          <>
            <Text style={s.filename}>{info.filename}</Text>
            <Text style={s.meta}>{info.extension.toUpperCase()}  ·  {fmtBytes(info.size_bytes)}</Text>
            <View style={s.btnRow}>
              <Btn label="Replace" onPress={handleUpload} loading={uploading} style={s.btn} />
              <Btn label="Delete" onPress={() => deleteResume.mutate()} loading={deleteResume.isPending} variant="danger" style={s.btn} />
            </View>
          </>
        ) : (
          <>
            <Text style={s.empty}>No resume uploaded</Text>
            <Btn label="Upload Resume" onPress={handleUpload} loading={uploading} variant="primary" />
          </>
        )}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root:     { flex: 1, backgroundColor: C.bg, padding: 16 },
  card:     { backgroundColor: C.surface, borderRadius: C.radius, padding: 16, borderWidth: 1, borderColor: C.border },
  filename: { fontSize: 15, color: C.text, fontWeight: '500', marginBottom: 4 },
  meta:     { fontSize: 12, color: C.muted, marginBottom: 16 },
  empty:    { fontSize: 14, color: C.muted, marginBottom: 16 },
  btnRow:   { flexDirection: 'row', gap: 8 },
  btn:      { flex: 1 },
});
