import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { C } from '../../constants/colors';

export function Spinner({ size = 'large' }: { size?: 'small' | 'large' }) {
  return <View style={s.wrap}><ActivityIndicator size={size} color={C.accent} /></View>;
}

const s = StyleSheet.create({
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center' },
});
