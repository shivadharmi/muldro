/**
 * Safe property accessors for A2UI component properties.
 *
 * All A2UI components receive `component.properties` as an untyped dict.
 * These helpers provide defensive defaults to prevent crashes when the
 * backend sends malformed or missing property values.
 */

export function safeProp<T>(
  props: Record<string, unknown> | undefined,
  key: string,
  fallback: T
): T {
  const val = props?.[key];
  return val !== undefined && val !== null ? (val as T) : fallback;
}

export function safeStringProp(
  props: Record<string, unknown> | undefined,
  key: string,
  fallback: string = ""
): string {
  return String(props?.[key] ?? fallback);
}

export function safeNumberProp(
  props: Record<string, unknown> | undefined,
  key: string,
  fallback: number = 0
): number {
  const val = props?.[key];
  if (typeof val === "number" && !Number.isNaN(val)) return val;
  return fallback;
}

export function safeArrayProp<T>(
  props: Record<string, unknown> | undefined,
  key: string,
  fallback: T[] = []
): T[] {
  const val = props?.[key];
  return Array.isArray(val) ? (val as T[]) : fallback;
}
