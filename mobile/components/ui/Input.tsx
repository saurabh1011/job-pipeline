import { StyleSheet, Text, TextInput, TextInputProps, View } from 'react-native';
import { C } from '../../constants/colors';

interface Props extends TextInputProps { label?: string; }

export function Input({ label, style, ...rest }: Props) {
  return (
    <View style={s.wrap}>
      {label ? <Text style={s.label}>{label}</Text> : null}
      <TextInput
        placeholderTextColor={C.muted}
        style={[s.input, style]}
        {...rest}
      />
    </View>
  );
}

const s = StyleSheet.create({
  wrap:  { marginBottom: 12 },
  label: { fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6, color: C.muted, marginBottom: 6 },
  input: { backgroundColor: C.surface2, borderWidth: 1, borderColor: C.border, borderRadius: C.radius, color: C.text, padding: 10, fontSize: 14 },
});
