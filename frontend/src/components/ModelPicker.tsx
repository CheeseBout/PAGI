import type { Agent } from "../api/client";
import { cn } from "@/lib/utils";

export default function ModelPicker({
  agents,
  value,
  onChange,
  disabled,
}: {
  agents: Agent[];
  value: string;
  onChange: (agentId: string) => void;
  disabled?: boolean;
}) {
  return (
    <select
      className={cn(
        "model-picker h-8 min-w-0 max-w-[340px] flex-1 rounded-lg border border-border bg-bg px-2 text-sm text-text",
        "focus-visible:outline-none focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-50",
      )}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {agents.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name} · {a.provider}/{a.model}
        </option>
      ))}
    </select>
  );
}
