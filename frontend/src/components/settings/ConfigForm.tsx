// Schema-driven config form (Phase 15, SPEC §17).
//
// Renders RagConfig / OrchestrationConfig from the metadata the backend serves,
// so adding a field to the Pydantic model surfaces here with no frontend change.
// Each field is tri-state: inherit (absent from `value`) / override / override-
// equal-to-inherited — kept distinct because they diverge when the parent tier
// changes later.

import { ChevronDown, DollarSign, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, ConfigSchema, ConfigFieldMeta } from "../../api/client";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { checkboxCls, selectCls } from "./formStyles";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Props = {
  which: "rag" | "orchestration";
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  // only show fields whose group is in this list (e.g. one pattern's group + "shared")
  onlyGroups?: string[];
  agents?: { id: string; name: string }[];
  // tiers BELOW the one being edited, lowest first — used to show which tier a
  // value is inherited FROM (SPEC §17.4). env is always the implicit lowest tier.
  parentLayers?: { label: string; raw: Record<string, unknown> }[];
};

/** Resolve a field's inherited value + source label from the tiers below. */
function inheritedInfo(
  name: string,
  parentLayers: { label: string; raw: Record<string, unknown> }[],
  envDefaults: Record<string, unknown>,
): { value: unknown; source: string } {
  for (let i = parentLayers.length - 1; i >= 0; i--) {
    if (name in parentLayers[i].raw) {
      return { value: parentLayers[i].raw[name], source: parentLayers[i].label };
    }
  }
  return { value: envDefaults[name], source: "default (env)" };
}

function widgetFor(meta: ConfigFieldMeta, prop: ConfigSchema["json_schema"]["properties"][string]): string {
  if (meta.widget) return meta.widget;
  if (prop?.enum) return "select";
  if (prop?.type === "boolean") return "switch";
  if ((prop?.type === "integer" || prop?.type === "number") && prop.minimum != null && prop.maximum != null)
    return "slider";
  if (prop?.type === "integer" || prop?.type === "number") return "number";
  if (prop?.type === "array" || prop?.type === "object") return "json";
  return "text";
}

function dependsSatisfied(
  meta: ConfigFieldMeta,
  effective: Record<string, unknown>,
): boolean {
  if (!meta.depends_on) return true;
  return Object.entries(meta.depends_on).every(([k, v]) => effective[k] === v);
}

export function ConfigForm({
  which,
  value,
  onChange,
  onlyGroups,
  agents,
  parentLayers = [],
}: Props) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [raw, setRaw] = useState(false);
  const [rawText, setRawText] = useState("");

  useEffect(() => {
    api
      .configSchema(which)
      .then(setSchema)
      .catch((e) => setErr(String(e.message || e)));
  }, [which]);

  useEffect(() => {
    if (raw) setRawText(JSON.stringify(value, null, 2));
  }, [raw, value]);

  const inheritedResolved = useMemo(() => {
    const out: Record<string, unknown> = { ...(schema?.defaults ?? {}) };
    for (const layer of parentLayers) Object.assign(out, layer.raw);
    return out;
  }, [schema, parentLayers]);

  const effective = useMemo(
    () => ({ ...inheritedResolved, ...value }),
    [inheritedResolved, value],
  );

  if (err) return <ErrorBanner message={err} />;
  if (!schema) return <div className="text-xs text-muted">loading schema…</div>;

  const groups = schema.groups.filter(
    (g) => !onlyGroups || onlyGroups.includes(g.id),
  );

  const setField = (name: string, v: unknown) => {
    onChange({ ...value, [name]: v });
  };
  const clearField = (name: string) => {
    const next = { ...value };
    delete next[name];
    onChange(next);
  };

  if (raw) {
    return (
      <div className="settings-form flex flex-col gap-3 rounded-lg border border-border bg-bg-elev p-4">
        <label className="flex flex-col gap-1 text-2xs text-muted">
          Raw JSON overrides
          <Textarea
            rows={10}
            className="font-mono text-xs"
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
          />
        </label>
        <div className="form-actions flex gap-2">
          <Button
            onClick={() => {
              try {
                onChange(JSON.parse(rawText || "{}"));
                setErr(null);
              } catch {
                setErr("not valid JSON");
              }
            }}
          >
            Apply JSON
          </Button>
          <Button
            variant="link"
            className="linkish h-auto px-1 py-0 text-2xs underline"
            onClick={() => setRaw(false)}
          >
            back to form
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-form config-form flex flex-col gap-2">
      <div className="form-actions flex gap-2">
        <Button
          variant="link"
          size="sm"
          className="linkish h-auto px-1 py-0 text-2xs underline"
          onClick={() => setRaw(true)}
        >
          edit as raw JSON
        </Button>
      </div>
      {groups
        .sort((a, b) => a.order - b.order)
        .map((g) => {
          const fields = Object.entries(schema.fields)
            .filter(([, m]) => m.group === g.id)
            .sort((a, b) => (a[1].order ?? 0) - (b[1].order ?? 0));
          if (fields.length === 0) return null;
          return (
            <Collapsible
              key={g.id}
              defaultOpen
              className="config-group rounded-lg border border-border px-2.5 py-1.5"
            >
              <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-left text-sm font-semibold [&[data-state=open]_svg]:rotate-180">
                <ChevronDown className="size-3.5 shrink-0 text-muted transition-transform" />
                {g.label}
                {g.note && <span className="text-xs font-normal text-muted"> — {g.note}</span>}
              </CollapsibleTrigger>
              <CollapsibleContent>
                {fields.map(([name, meta]) => {
                  const prop = schema.json_schema.properties[name] ?? {};
                  const overridden = name in value;
                  const inh = inheritedInfo(name, parentLayers, schema.defaults);
                  const inheritedVal = inh.value;
                  const disabled = !dependsSatisfied(meta, effective);
                  const w = widgetFor(meta, prop);
                  const cur = overridden ? value[name] : inheritedVal;
                  return (
                    <div
                      className={cn(
                        "config-field border-t border-border py-1.5 first:border-t-0",
                        disabled && "is-disabled opacity-45",
                      )}
                      key={name}
                    >
                      <div className="config-field-head flex flex-wrap items-center gap-2.5">
                        <label className="flex flex-row items-center gap-1.5 text-sm text-text">
                          <input
                            type="checkbox"
                            className={checkboxCls}
                            checked={overridden}
                            onChange={(e) =>
                              e.target.checked
                                ? setField(name, inheritedVal ?? "")
                                : clearField(name)
                            }
                          />
                          {meta.label}
                          {meta.requires_reingest && (
                            <span title="only takes effect on next ingest">
                              <RefreshCw className="size-3 text-muted" />
                            </span>
                          )}
                        </label>
                        {!overridden && (
                          <span className="text-xs text-muted">
                            inherited: {String(inheritedVal ?? "—")}{" "}
                            <span className="config-source italic opacity-70">from {inh.source}</span>
                          </span>
                        )}
                        {overridden && (
                          <Button
                            variant="link"
                            size="sm"
                            className="linkish h-auto px-1 py-0 text-2xs underline"
                            onClick={() => clearField(name)}
                          >
                            reset
                          </Button>
                        )}
                      </div>
                      {meta.help && <div className="text-xs text-muted">{meta.help}</div>}
                      {meta.cost_hint && (
                        <div className="flex items-center gap-1 text-xs text-muted">
                          <DollarSign className="size-3" /> {meta.cost_hint}
                        </div>
                      )}
                      {overridden && !disabled && (
                        <FieldInput
                          widget={w}
                          prop={prop}
                          value={cur}
                          agents={agents}
                          onChange={(v) => setField(name, v)}
                        />
                      )}
                    </div>
                  );
                })}
              </CollapsibleContent>
            </Collapsible>
          );
        })}
    </div>
  );
}

function FieldInput({
  widget,
  prop,
  value,
  agents,
  onChange,
}: {
  widget: string;
  prop: ConfigSchema["json_schema"]["properties"][string];
  value: unknown;
  agents?: { id: string; name: string }[];
  onChange: (v: unknown) => void;
}) {
  if (widget === "switch") {
    return (
      <input
        type="checkbox"
        className={checkboxCls}
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (widget === "select") {
    return (
      <select className={selectCls} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
        {(prop.enum ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  if (widget === "slider") {
    return (
      <span className="config-slider flex items-center gap-2">
        <input
          type="range"
          min={prop.minimum}
          max={prop.maximum}
          step={prop.type === "integer" ? 1 : 0.05}
          value={Number(value ?? prop.minimum ?? 0)}
          onChange={(e) => onChange(Number(e.target.value))}
          className="accent-accent"
        />
        <input
          type="number"
          min={prop.minimum}
          max={prop.maximum}
          step={prop.type === "integer" ? 1 : 0.05}
          value={Number(value ?? 0)}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-20 rounded-md border border-border bg-bg px-1.5 py-0.5 text-sm"
        />
      </span>
    );
  }
  if (widget === "number") {
    return (
      <input
        type="number"
        value={Number(value ?? 0)}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-md border border-border bg-bg px-1.5 py-0.5 text-sm"
      />
    );
  }
  if (widget === "model-picker") {
    return (
      <input
        list="model-suggestions"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder="model id (blank = default)"
        className="w-full rounded-md border border-border bg-bg px-2 py-1 text-sm"
      />
    );
  }
  if (widget === "agent-picker") {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="agent-picker flex flex-wrap gap-2">
        {(agents ?? []).map((a) => (
          <label
            key={a.id}
            className="chip-check flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs"
          >
            <input
              type="checkbox"
              className={checkboxCls}
              checked={arr.includes(a.id)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...arr, a.id]
                    : arr.filter((x) => x !== a.id),
                )
              }
            />
            {a.name}
          </label>
        ))}
        {(agents ?? []).length === 0 && (
          <span className="text-xs text-muted">no delegatable agents yet</span>
        )}
      </div>
    );
  }
  if (widget === "json") {
    return (
      <Textarea
        rows={4}
        className="font-mono text-xs"
        value={
          typeof value === "string" ? value : JSON.stringify(value ?? null, null, 2)
        }
        onChange={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            onChange(e.target.value);
          }
        }}
      />
    );
  }
  return (
    <input
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-border bg-bg px-2 py-1 text-sm"
    />
  );
}
