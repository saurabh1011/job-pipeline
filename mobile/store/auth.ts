import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const TOKEN_KEY = 'auth_token';
const URL_KEY   = 'api_base_url';
export const DEFAULT_URL = 'https://job-pipeline.fly.dev';

// SecureStore is native-only; fall back to localStorage on web
async function save(key: string, value: string) {
  if (Platform.OS === 'web') { localStorage.setItem(key, value); return; }
  await SecureStore.setItemAsync(key, value);
}
async function load(key: string): Promise<string | null> {
  if (Platform.OS === 'web') return localStorage.getItem(key);
  return SecureStore.getItemAsync(key);
}
async function remove(key: string) {
  if (Platform.OS === 'web') { localStorage.removeItem(key); return; }
  await SecureStore.deleteItemAsync(key);
}

export const authStore = {
  async getToken(): Promise<string | null> { return load(TOKEN_KEY); },
  async setToken(t: string)               { await save(TOKEN_KEY, t); },
  async clearToken()                       { await remove(TOKEN_KEY); },

  async getBaseUrl(): Promise<string> { return (await load(URL_KEY)) ?? DEFAULT_URL; },
  async setBaseUrl(u: string)         { await save(URL_KEY, u); },
};
