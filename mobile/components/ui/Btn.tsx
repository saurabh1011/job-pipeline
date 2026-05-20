import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, ViewStyle } from 'react-native';
import { C } from '../../constants/colors';

type Variant = 'primary' | 'ghost' | 'danger' | 'success' | 'warn';

interface Props {
  label: string; onPress: () => void;
  variant?: Variant; disabled?: boolean; loading?: boolean;
  style?: ViewStyle; small?: boolean;
}

const BG: Record<Variant, string> = {
  primary: C.accent, ghost: C.surface2, danger: C.red, success: C.green, warn: '#3a2800',
};
const FG: Record<Variant, string> = {
  primary: '#fff', ghost: C.text, danger: '#fff', success: '#fff', warn: C.yellow,
};

export function Btn({ label, onPress, variant = 'ghost', disabled, loading, style, small }: Props) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled || loading}
      style={[s.base, { backgroundColor: BG[variant], opacity: disabled || loading ? 0.4 : 1 },
              small && s.small, style]}
    >
      {loading
        ? <ActivityIndicator size="small" color={FG[variant]} />
        : <Text style={[s.label, { color: FG[variant] }, small && s.labelSm]}>{label}</Text>}
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  base:    { paddingVertical: 9, paddingHorizontal: 16, borderRadius: C.radius, alignItems: 'center', justifyContent: 'center' },
  small:   { paddingVertical: 5, paddingHorizontal: 12 },
  label:   { fontSize: 13, fontWeight: '500' },
  labelSm: { fontSize: 12 },
});
