import { StyleSheet, Text, View } from 'react-native';
import { ALL_STATUSES, StatusKey } from '../../constants/statuses';

export function StatusChip({ status }: { status: StatusKey }) {
  const def = ALL_STATUSES.find(s => s.key === status);
  const color = def?.color ?? '#888888';
  return (
    <View style={[s.chip, { backgroundColor: color + '22', borderColor: color }]}>
      <Text style={[s.label, { color }]}>{def?.label ?? status}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  chip:  { borderRadius: 4, borderWidth: 1, paddingHorizontal: 6, paddingVertical: 2 },
  label: { fontSize: 11, fontWeight: '600' },
});
