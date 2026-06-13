import type { Step } from "../types";
import { StatusBadge } from "./StatusBadge";

function preview(text: string | null, limit = 200): string {
  if (!text) return "";
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length <= limit ? collapsed : collapsed.slice(0, limit - 1) + "…";
}

export function StepList({ steps }: { steps: Step[] }) {
  if (steps.length === 0) {
    return <p className="muted">No steps yet.</p>;
  }
  return (
    <div className="steplist">
      {steps.map((step) => (
        <div className="step" key={step.id}>
          <div className="step-head">
            <span>step #{step.loop_index}</span>
            <StatusBadge status={step.status} />
            <span className="muted">exit {step.exit_code ?? "-"}</span>
          </div>
          <div className="step-times muted">
            started {step.started_at ?? "-"} · finished {step.finished_at ?? "-"}
          </div>
          <dl className="kv compact">
            <dt>prompt</dt>
            <dd>{preview(step.prompt)}</dd>
            {step.stdout && (
              <>
                <dt>stdout</dt>
                <dd className="mono">{preview(step.stdout)}</dd>
              </>
            )}
            {step.stderr && (
              <>
                <dt>stderr</dt>
                <dd className="mono">{preview(step.stderr)}</dd>
              </>
            )}
          </dl>
        </div>
      ))}
    </div>
  );
}
