"use client";

// The rack elevation: front and rear faces drawn unit by unit between ink rails.
// Items are plates on the road surface: plain for documented hardware, yellow
// when the linked device needs attention, a red ring when it has vanished or
// overlaps, dashed accent when only planned. Drag moves an item; Alt+Arrow
// moves the focused item one unit; the detail panel offers the same moves as
// buttons, so dragging is never the only way.
import { clsx } from "clsx";
import { CircleCheck, CircleDashed, OctagonAlert, TriangleAlert } from "lucide-react";
import { type DragEvent, type KeyboardEvent, useState } from "react";
import { KindIcon } from "@/components/infra";
import { deviceTone } from "@/components/ui/signs";
import type { RackDetail, RackItem } from "@/lib/types";

type Face = "front" | "rear";

function itemState(item: RackItem, conflicted: boolean): "planned" | "attention" | "failed" | "ok" | "plain" {
  if (conflicted) return "failed";
  if (item.status === "planned") return "planned";
  if (item.device) {
    if (!item.device.present) return "failed";
    const tone = deviceTone(item.device.state);
    if (tone === "crit") return "failed";
    if (tone === "warn") return "attention";
    return "ok";
  }
  return "plain";
}

const STATE_ICON = { ok: CircleCheck, attention: TriangleAlert, failed: OctagonAlert, planned: CircleDashed, plain: null };
const STATE_WORD = { ok: "Online", attention: "Needs attention", failed: "Problem", planned: "Planned", plain: "" };

function faces(i: RackItem): Face[] {
  return i.full_depth ? ["front", "rear"] : [i.face];
}

export function RackElevation({
  rack,
  selected,
  onSelect,
  editable,
  onMove,
}: {
  rack: RackDetail;
  selected: string | null;
  onSelect: (id: string) => void;
  editable: boolean;
  onMove: (item: RackItem, position: number, face?: Face) => void;
}) {
  const [drag, setDrag] = useState<{ id: string; top: number; face: Face } | null>(null);
  // Phones show one face at a time; from md both faces sit side by side.
  const [shown, setShown] = useState<Face>("front");
  const placed = rack.items.filter((i) => i.position != null && i.status !== "removed");
  const conflicted = new Set(rack.conflicts.flatMap((c) => c.items));
  const label = new Map(rack.units.map((u) => [u.unit, u.label]));
  const dragging = drag ? rack.items.find((i) => i.id === drag.id) : undefined;

  // Where a drop at `top` would land: the item's top unit sits on the row it is dropped on.
  const landing = (item: RackItem, top: number) => Math.max(1, top - item.height_u + 1);
  const blocked = (item: RackItem, pos: number, face: Face) => {
    const want = item.full_depth ? ["front", "rear"] : [face];
    if (pos + item.height_u - 1 > rack.u_height) return true;
    return placed.some(
      (o) => o.id !== item.id && o.position! <= pos + item.height_u - 1 && pos <= o.position! + o.height_u - 1 && faces(o).some((f) => want.includes(f)),
    );
  };

  const onKey = (e: KeyboardEvent, item: RackItem) => {
    if (!editable || !e.altKey || item.position == null) return;
    if (e.key === "ArrowUp") {
      e.preventDefault();
      onMove(item, item.position + 1);
    } else if (e.key === "ArrowDown" && item.position > 1) {
      e.preventDefault();
      onMove(item, item.position - 1);
    }
  };

  const column = (face: Face) => {
    const preview =
      dragging && drag && drag.face === face
        ? { pos: landing(dragging, drag.top), bad: blocked(dragging, landing(dragging, drag.top), face) }
        : null;
    return (
      <div role="group" aria-label={`${face === "front" ? "Front" : "Rear"} of ${rack.name}`} className={clsx("min-w-0 flex-1", shown !== face && "hidden md:block")}>
        <p className="mb-2 text-center text-xs font-bold tracking-wide text-ink-2 uppercase">{face === "front" ? "Front" : "Rear"}</p>
        <div className="flex">
          <ol aria-hidden className="w-7 shrink-0 pr-1.5 text-right font-mono text-[11px] leading-[var(--u-h)] text-ink-3 tabular">
            {rack.units.map((u) => (
              <li key={u.unit} style={{ height: "var(--u-h)" }}>{u.label}</li>
            ))}
          </ol>
          <div
            className="relative min-w-0 flex-1 border-x-[3px] border-ink bg-surface-2"
            style={{ height: `calc(var(--u-h) * ${rack.u_height})` }}
          >
            {rack.units.map((u, row) => (
              <div
                key={u.unit}
                className="absolute inset-x-0 border-b border-dashed border-rule"
                style={{ top: `calc(var(--u-h) * ${row})`, height: "var(--u-h)" }}
                onDragOver={
                  editable
                    ? (e: DragEvent) => {
                        e.preventDefault();
                        if (drag && (drag.top !== u.unit || drag.face !== face)) setDrag({ ...drag, top: u.unit, face });
                      }
                    : undefined
                }
                onDrop={
                  editable
                    ? (e: DragEvent) => {
                        e.preventDefault();
                        if (dragging) {
                          const pos = landing(dragging, u.unit);
                          if (!blocked(dragging, pos, face)) onMove(dragging, pos, dragging.full_depth ? undefined : face);
                        }
                        setDrag(null);
                      }
                    : undefined
                }
              />
            ))}
            {preview && (
              <div
                aria-hidden
                className={clsx("pointer-events-none absolute inset-x-1 rounded-[3px] border-2 border-dashed", preview.bad ? "border-crit bg-crit-soft" : "border-accent bg-accent-soft")}
                style={{ top: `calc(var(--u-h) * ${rack.u_height - (preview.pos + dragging!.height_u - 1)})`, height: `calc(var(--u-h) * ${dragging!.height_u})` }}
              />
            )}
            {placed.map((item) => {
              if (!faces(item).includes(face)) return null;
              const top = rack.u_height - (item.position! + item.height_u - 1);
              const style = { top: `calc(var(--u-h) * ${top} + 1px)`, height: `calc(var(--u-h) * ${item.height_u} - 2px)` };
              const range = item.height_u > 1 ? `U${label.get(item.position!)}–U${label.get(item.position! + item.height_u - 1)}` : `U${label.get(item.position!)}`;
              if (item.face !== face) {
                // The back of a full-depth item: it blocks the unit but is mounted from the other side.
                return (
                  <button
                    key={item.id}
                    type="button"
                    tabIndex={-1}
                    onClick={() => onSelect(item.id)}
                    className="absolute inset-x-1 flex items-center rounded-[3px] bg-surface-3 px-2 text-left text-xs text-ink-3"
                    style={style}
                    title={`${item.name} (full depth, mounted at the ${item.face})`}
                  >
                    <span className="truncate">Back of {item.name}</span>
                  </button>
                );
              }
              const st = itemState(item, conflicted.has(item.id));
              const Icon = STATE_ICON[st];
              const isSel = selected === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  draggable={editable}
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", item.id);
                    e.dataTransfer.effectAllowed = "move";
                    setDrag({ id: item.id, top: item.position! + item.height_u - 1, face });
                  }}
                  onDragEnd={() => setDrag(null)}
                  onClick={() => onSelect(item.id)}
                  onKeyDown={(e) => onKey(e, item)}
                  aria-pressed={isSel}
                  aria-label={`${item.name}, ${range}${STATE_WORD[st] ? `, ${STATE_WORD[st]}` : ""}${item.is_demo ? ", demo data" : ""}${editable ? ". Alt plus arrow keys move it one unit." : ""}`}
                  className={clsx(
                    "group absolute inset-x-1 flex items-center gap-2 overflow-hidden rounded-[3px] px-2 text-left transition-colors duration-100",
                    editable && "cursor-grab active:cursor-grabbing",
                    drag?.id === item.id && "opacity-40",
                    st === "attention" && "sign bg-warn text-warn-ink",
                    st === "failed" && "border border-rule-strong bg-surface text-ink ring-[3px] ring-crit ring-inset",
                    st === "planned" && "border-2 border-dashed border-accent bg-accent-soft text-ink",
                    (st === "ok" || st === "plain") && "border border-rule-strong bg-surface text-ink hover:bg-surface-2",
                    isSel && st !== "attention" && "outline-2 -outline-offset-4 outline-accent",
                    isSel && st === "attention" && "outline-2 -outline-offset-4 outline-ink",
                  )}
                  style={style}
                >
                  <KindIcon kind={item.kind} className={st === "attention" ? "text-warn-ink" : "text-ink-3"} />
                  <span className="flex min-w-0 flex-1 flex-col leading-tight">
                    <span className={clsx("truncate font-bold", item.height_u === 1 ? "text-sm" : "text-base")}>{item.name}</span>
                    {item.height_u > 1 && (
                      <span className={clsx("truncate text-xs", st === "attention" ? "text-warn-ink" : "text-ink-3")}>
                        {[item.device?.model || item.model, item.kind === "patch_panel" ? `${item.port_count} ports` : null].filter(Boolean).join(" · ") || range}
                      </span>
                    )}
                  </span>
                  {Icon && (
                    <Icon
                      aria-hidden
                      strokeWidth={2.25}
                      className={clsx("size-4 shrink-0", st === "ok" && "text-ok", st === "failed" && "text-crit", st === "planned" && "text-accent")}
                    />
                  )}
                </button>
              );
            })}
          </div>
          <ol aria-hidden className="w-7 shrink-0 pl-1.5 font-mono text-[11px] leading-[var(--u-h)] text-ink-3 tabular">
            {rack.units.map((u) => (
              <li key={u.unit} style={{ height: "var(--u-h)" }}>{u.label}</li>
            ))}
          </ol>
        </div>
      </div>
    );
  };

  return (
    <>
      <div role="radiogroup" aria-label="Rack face" className="mb-3 flex rounded-[var(--radius)] border border-rule-strong p-0.5 md:hidden">
        {(["front", "rear"] as Face[]).map((f) => (
          <button
            key={f}
            type="button"
            role="radio"
            aria-checked={shown === f}
            onClick={() => setShown(f)}
            className={clsx("h-8 flex-1 rounded-[3px] text-sm font-semibold", shown === f ? "bg-accent text-accent-ink" : "text-ink-2")}
          >
            {f === "front" ? "Front" : "Rear"}
          </button>
        ))}
      </div>
      <div className="flex gap-6">
        {column("front")}
        {column("rear")}
      </div>
    </>
  );
}

/** What each plate style means, in words: the elevation never relies on colour alone. */
export function ElevationLegend() {
  const row = "inline-flex items-center gap-2 text-sm text-ink-2";
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2" aria-label="Elevation legend">
      <li className={row}><span className="inline-flex size-4 items-center justify-center rounded-[3px] border border-rule-strong bg-surface"><CircleCheck className="size-3 text-ok" aria-hidden /></span>Linked device online</li>
      <li className={row}><span className="sign inline-block size-4 rounded-[3px] bg-warn text-warn-ink" />Linked device needs attention</li>
      <li className={row}><span className="inline-block size-4 rounded-[3px] bg-surface ring-[3px] ring-crit ring-inset" />Missing, offline or overlapping</li>
      <li className={row}><span className="inline-block size-4 rounded-[3px] border-2 border-dashed border-accent bg-accent-soft" />Planned</li>
      <li className={row}><span className="inline-block size-4 rounded-[3px] bg-surface-3" />Back of a full-depth item</li>
    </ul>
  );
}
