import { StyleSheet, Text, View } from 'react-native';

export function ScoreBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) return null;
  const color = score >= 8 ? '#4caf78' : score >= 6 ? '#d4a843' : '#888888';
  return (
    <View style={[s.badge, { backgroundColor: color + '22', borderColor: color }]}>
      <Text style={[s.text, { color }]}>{score.toFixed(1)}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  badge: { borderRadius: 4, borderWidth: 1, paddingHorizontal: 6, paddingVertical: 2 },
  text:  { fontSize: 11, fontWeight: '700' },
});
