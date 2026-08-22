/**
 * Shared fixtures for the binding-grid's test files.
 *
 * The specs split along a real seam: `binding-fields.test.tsx` covers what the
 * grid SHOWS (capability states, labels, the derived provider, the picker
 * trigger, dirty/warning/disabled), and `binding-fields-numeric.test.tsx`
 * covers what a KEYSTROKE MEANS (which raw strings become a patch and which are
 * held as text). One fixture graph serves both, so the two can never quietly
 * diverge into testing two different catalogs.
 *
 * Every export is a FACTORY returning a fresh object, matching
 * `model-tab-fixtures.tsx`. A shared const would be true-by-convention only: the
 * first test that mutated it would leak into every other file importing it, and
 * nothing would say so.
 */

import { render } from "@testing-library/react";
import { useState } from "react";
import { vi } from "vitest";

import type { CatalogModel, CatalogProvider, ModelBinding } from "@/lib/types";
import { BindingFields, type BindingPatch } from "./binding-fields";

export function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    display_name: "Claude Opus 4.5",
    thinking_style: "adaptive",
    accepts_temperature: true,
    suggested_tier: "reasoning",
    context_window: 200000,
    input_cost_per_1k: 0.005,
    output_cost_per_1k: 0.025,
    supports_prompt_cache: true,
    ...over,
  };
}

export function anthropic(): CatalogProvider {
  return {
    provider: "anthropic",
    display_name: "Anthropic",
    auth_kind: "api_key",
    credential_fields: [],
    model_count: 4,
    docs_url: null,
  };
}

export function binding(over: Partial<ModelBinding> = {}): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: "reasoning",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    effort: "high",
    max_tokens: 8192,
    temperature: 0.7,
    ...over,
  };
}

export interface RenderFieldsOptions {
  binding?: Partial<ModelBinding>;
  model?: Partial<CatalogModel>;
  dirty?: boolean;
  disabled?: boolean;
  warning?: boolean;
}

/** Renders the grid with a spy parent. Returns the binding object it was given
 *  so a test can assert it was never mutated. */
export function renderFields(over: RenderFieldsOptions = {}) {
  const value = binding(over.binding);
  const onChange = vi.fn();
  const onOpenPicker = vi.fn();
  render(
    <BindingFields
      binding={value}
      models={[model(over.model)]}
      providers={[anthropic()]}
      onChange={onChange}
      onOpenPicker={onOpenPicker}
      dirty={over.dirty}
      disabled={over.disabled}
      warning={over.warning}
    />,
  );
  return { value, onChange, onOpenPicker };
}

/**
 * The grid wired to a real controlled parent.
 *
 * A `vi.fn()` alone cannot see the defect class these numeric fields are most
 * prone to: an emitted patch merges into the draft and comes straight back down
 * as the displayed value, so a handler that maps an intermediate keystroke onto
 * a "safe" value rewrites the field under the founder's cursor. That round trip
 * only exists when something actually feeds the patch back.
 */
export function Harness({
  initial,
  onChange,
  models = [model()],
}: {
  initial: ModelBinding;
  onChange: (patch: BindingPatch) => void;
  models?: CatalogModel[];
}) {
  const [current, setCurrent] = useState(initial);
  return (
    <BindingFields
      binding={current}
      models={models}
      providers={[anthropic()]}
      onChange={(patch) => {
        onChange(patch);
        setCurrent((prev) => ({ ...prev, ...patch }));
      }}
      onOpenPicker={vi.fn()}
    />
  );
}
