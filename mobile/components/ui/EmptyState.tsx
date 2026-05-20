import { StyleSheet, Text, View } from 'react-native';
import { C } from '../../constants/colors';

export function EmptyState({ text }: { text: string }) {
  return <View style={s.wrap}><Text style={s.text}>{text}</Text></View>;
}

const s = StyleSheet.create({
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 40 },
  text: { color: C.muted, fontSize: 14, textAlign: 'center' },
});
