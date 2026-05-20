import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { C } from '../constants/colors';
import { authStore } from '../store/auth';
import { apiFetch } from '../api/client';
import { Input } from '../components/ui/Input';
import { Btn } from '../components/ui/Btn';

export default function LoginScreen() {
  const router = useRouter();
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://job-pipeline.fly.dev');
  const [loading, setLoading] = useState(false);

  async function handleLogin() {
    if (!apiKey.trim()) { Alert.alert('Enter an API key'); return; }
    setLoading(true);
    try {
      await authStore.setBaseUrl(baseUrl.trim());
      await authStore.setToken(apiKey.trim());
      await apiFetch('GET', '/api/me');
      router.replace('/(tabs)');
    } catch (e: any) {
      await authStore.clearToken();
      Alert.alert('Login failed', e.message ?? 'Could not connect');
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={s.root}>
      <View style={s.card}>
        <Text style={s.title}>Job Pipeline</Text>
        <Text style={s.sub}>Enter your API key to continue</Text>
        <Input
          label="Server URL"
          value={baseUrl}
          onChangeText={setBaseUrl}
          autoCapitalize="none"
          keyboardType="url"
          autoCorrect={false}
        />
        <Input
          label="API Key"
          value={apiKey}
          onChangeText={setApiKey}
          secureTextEntry
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="sk-…"
        />
        <Btn label="Sign In" onPress={handleLogin} loading={loading} variant="primary" />
      </View>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  root:  { flex: 1, backgroundColor: C.bg, justifyContent: 'center', padding: 24 },
  card:  { backgroundColor: C.surface, borderRadius: C.radius, padding: 24, borderWidth: 1, borderColor: C.border },
  title: { fontSize: 22, fontWeight: '700', color: C.text, marginBottom: 4 },
  sub:   { fontSize: 13, color: C.muted, marginBottom: 24 },
});
