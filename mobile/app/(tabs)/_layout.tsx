import { Tabs } from 'expo-router';
import { C } from '../../constants/colors';

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: C.surface, borderTopColor: C.border },
        tabBarActiveTintColor: C.accent,
        tabBarInactiveTintColor: C.muted,
        tabBarLabelStyle: { fontSize: 11 },
      }}
    >
      <Tabs.Screen name="index"    options={{ title: 'Jobs' }} />
      <Tabs.Screen name="pipeline" options={{ title: 'Run' }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings', href: '/settings' }} />
    </Tabs>
  );
}
