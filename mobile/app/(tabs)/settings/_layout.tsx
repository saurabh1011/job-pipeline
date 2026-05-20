import { Stack } from 'expo-router';
import { C } from '../../../constants/colors';

export default function SettingsLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: C.surface },
        headerTintColor: C.text,
        headerTitleStyle: { fontSize: 16, fontWeight: '600' },
        headerBackTitle: 'Back',
        contentStyle: { backgroundColor: C.bg },
      }}
    />
  );
}
