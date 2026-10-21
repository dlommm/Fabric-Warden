"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { ASSET_KIND_WORD, ASSET_STATUS_WORD, AssetStatusLabel, DaysLabel, shortDate } from "@/components/infra";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Asset, AssetKind, AssetStatus, DeviceSummary, Location, RackItem } from "@/lib/types";

const DATE_FIELDS = [
  ["purchase_date", "Purchased"],
  ["warranty_until", "Warranty until"],
  ["support_until", "Support until"],
  ["expires_on", "Expires (licence or subscription)"],
  ["end_of_sale", "End of sale"],
  ["end_of_life", "End of life"],
] as const;

type Draft = {
  name: string;
  kind: AssetKind;
  status: AssetStatus;
  asset_tag: string;
  vendor: string;
  model: string;
  serial: string;
  quantity: string;
  min_quantity: string;
  supplier: string;
  order_ref: string;
  purchase_cost: string;
  currency: string;
  seats: string;
  auto_renew: boolean;
  device_id: string;
  rack_item_id: string;
  location_id: string;
  labels: string;
  notes: string;
} & Record<(typeof DATE_FIELDS)[number][0], string>;

function draftOf(a?: Asset): Draft {
  return {
    name: a?.name ?? "",
    kind: a?.kind ?? "hardware",
    status: a?.status ?? "in_service",
    asset_tag: a?.asset_tag ?? "",
    vendor: a?.vendor ?? "",
    model: a?.model ?? "",
    serial: a?.serial ?? "",
    quantity: String(a?.quantity ?? 1),
    min_quantity: a?.min_quantity != null ? String(a.min_quantity) : "",
    supplier: a?.supplier ?? "",
    order_ref: a?.order_ref ?? "",
    purchase_cost: a?.purchase_cost ?? "",
    currency: a?.currency ?? "",
    seats: a?.seats != null ? String(a.seats) : "",
    auto_renew: a?.auto_renew ?? false,
    device_id: a?.device_id ?? "",
    rack_item_id: a?.rack_item_id ?? "",
    location_id: a?.location_id ?? "",
    labels: (a?.labels ?? []).join(", "),
    notes: a?.notes ?? "",
    purchase_date: a?.purchase_date ?? "",
    warranty_until: a?.warranty_until ?? "",
    support_until: a?.support_until ?? "",
    expires_on: a?.expires_on ?? "",
    end_of_sale: a?.end_of_sale ?? "",
    end_of_life: a?.end_of_life ?? "",
  };
}

export default function AssetPage() {
  const { id } = useParams<{ id: string }>();
  const isNew = id === "new";
  const q = useQuery({ queryKey: ["assets", id], queryFn: () => get<Asset>(`/api/assets/${id}`), enabled: !isNew });
  if (!isNew && q.error) return <Notice tone="crit">{(q.error as Error).message}</Notice>;
  if (!isNew && !q.data) {
    return (
      <>
        <Skeleton className="mb-6 h-8 w-64" />
        <Skeleton className="h-96" />
      </>
    );
  }
  return <AssetEditor key={q.data?.id ?? "new"} asset={q.data} />;
}

function AssetEditor({ asset }: { asset?: Asset }) {
  const { can } = useSession();
  const editable = can("dcim:edit");
  const qc = useQueryClient();
  const router = useRouter();
  const [f, setF] = useState<Draft>(() => draftOf(asset));
  const [errors, setErrors] = useState<{ name?: string; currency?: string; form?: string }>({});
  const [confirm, setConfirm] = useState(false);
  const devices = useQuery({ queryKey: ["inventory", "devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const items = useQuery({ queryKey: ["dcim", "items"], queryFn: () => get<{ items: (RackItem & { rack: string | null })[] }>("/api/dcim/items"), retry: false });
  const locations = useQuery({ queryKey: ["dcim", "locations"], queryFn: () => get<{ items: Location[] }>("/api/dcim/locations"), retry: false });

  const body = () => {
    const n = (v: string) => (v === "" ? null : Number(v));
    const d = (v: string) => v || null;
    return {
      name: f.name.trim(),
      kind: f.kind,
      status: f.status,
      asset_tag: f.asset_tag.trim(),
      vendor: f.vendor.trim(),
      model: f.model.trim(),
      serial: f.serial.trim(),
      quantity: Number(f.quantity) || 0,
      min_quantity: n(f.min_quantity),
      supplier: f.supplier.trim(),
      order_ref: f.order_ref.trim(),
      purchase_cost: f.purchase_cost === "" ? null : f.purchase_cost,
      currency: f.currency.trim().toUpperCase(),
      seats: n(f.seats),
      auto_renew: f.auto_renew,
      device_id: d(f.device_id),
      rack_item_id: d(f.rack_item_id),
      location_id: d(f.location_id),
      labels: f.labels.split(",").map((x) => x.trim()).filter(Boolean),
      notes: f.notes,
      ...Object.fromEntries(DATE_FIELDS.map(([k]) => [k, d(f[k])])),
    };
  };
  const save = useMutation({
    mutationFn: () => (asset ? patch<Asset>(`/api/assets/${asset.id}`, body()) : post<Asset>("/api/assets", body())),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      if (!asset) router.replace(`/assets/${a.id}`);
      else setErrors({});
    },
    onError: (e) => setErrors({ form: e instanceof ApiError ? e.message : "Couldn't save." }),
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/assets/${asset!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      router.push("/assets");
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!f.name.trim()) next.name = "Name the asset.";
    if (f.currency && !/^[A-Za-z]{3}$/.test(f.currency.trim())) next.currency = "Use a three-letter code such as USD or EUR.";
    setErrors(next);
    if (!Object.keys(next).length) save.mutate();
  };
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const licence = f.kind === "licence" || f.kind === "subscription";
  const stock = f.kind === "spare" || f.kind === "consumable";

  return (
    <>
      <PageHeader
        title={asset ? asset.name : "Add an asset"}
        description={asset ? [ASSET_KIND_WORD[asset.kind], [asset.vendor, asset.model].filter(Boolean).join(" ")].filter(Boolean).join(" · ") : "Hardware, a spare part, a licence or a subscription."}
        meta={
          asset && (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              {asset.is_demo && <DemoTag />}
              <AssetStatusLabel s={asset.status} />
              {asset.next_date && (
                <span className="inline-flex items-center gap-2 text-ink-2">
                  {asset.next_date.label} {shortDate(asset.next_date.date)} <DaysLabel days={asset.next_date.days} autoRenew={asset.auto_renew} />
                </span>
              )}
              {asset.device && <Link href={`/inventory/devices/${asset.device_id}`} className="font-semibold text-accent hover:underline">{asset.device}</Link>}
            </span>
          )
        }
        actions={asset && editable && <Button variant="ghost" icon={<Trash2 />} onClick={() => setConfirm(true)}>Delete</Button>}
      />
      <form onSubmit={submit} noValidate className="flex flex-col gap-[var(--gap)]">
        <fieldset disabled={!editable} className="contents">
          <Panel title="What it is">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Name" error={errors.name} className="sm:col-span-2">{(p) => <Input {...p} autoFocus={!asset} value={f.name} maxLength={128} onChange={set("name")} />}</Field>
              <Field label="Kind">
                {(p) => (
                  <Select {...p} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as AssetKind, status: e.target.value === "spare" ? "spare" : f.status })}>
                    {Object.entries(ASSET_KIND_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Status">
                {(p) => (
                  <Select {...p} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as AssetStatus })}>
                    {Object.entries(ASSET_STATUS_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Vendor">{(p) => <Input {...p} value={f.vendor} maxLength={64} onChange={set("vendor")} />}</Field>
              <Field label="Model">{(p) => <Input {...p} value={f.model} maxLength={64} onChange={set("model")} />}</Field>
              <Field label="Asset tag">{(p) => <Input {...p} className="font-mono" value={f.asset_tag} maxLength={64} onChange={set("asset_tag")} />}</Field>
              <Field label="Serial">{(p) => <Input {...p} className="font-mono" value={f.serial} maxLength={64} onChange={set("serial")} />}</Field>
              {stock && (
                <>
                  <Field label="Quantity on hand">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value.replace(/\D/g, "") })} />}</Field>
                  <Field label="Keep at least" hint="Warns when stock drops below">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.min_quantity} onChange={(e) => setF({ ...f, min_quantity: e.target.value.replace(/\D/g, "") })} />}</Field>
                </>
              )}
              {licence && (
                <>
                  <Field label="Seats">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.seats} onChange={(e) => setF({ ...f, seats: e.target.value.replace(/\D/g, "") })} />}</Field>
                  <Field label="Renewal">
                    {(p) => (
                      <Select {...p} value={f.auto_renew ? "auto" : "manual"} onChange={(e) => setF({ ...f, auto_renew: e.target.value === "auto" })}>
                        <option value="manual">Renewed by hand</option>
                        <option value="auto">Renews automatically</option>
                      </Select>
                    )}
                  </Field>
                </>
              )}
            </div>
            {licence && <p className="mt-3 text-sm text-ink-3">Keep licence keys in the credential vault; reference them in the notes.</p>}
          </Panel>
          <Panel title="Dates">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {DATE_FIELDS.map(([k, label]) => (
                <Field key={k} label={label}>{(p) => <Input {...p} type="date" className="tabular" value={f[k]} onChange={set(k)} />}</Field>
              ))}
            </div>
          </Panel>
          <Panel title="Purchase and placement">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Supplier">{(p) => <Input {...p} value={f.supplier} maxLength={128} onChange={set("supplier")} />}</Field>
              <Field label="Order reference">{(p) => <Input {...p} className="font-mono" value={f.order_ref} maxLength={64} onChange={set("order_ref")} />}</Field>
              <Field label="Unit cost">{(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.purchase_cost} onChange={(e) => setF({ ...f, purchase_cost: e.target.value.replace(/[^\d.]/g, "") })} />}</Field>
              <Field label="Currency" error={errors.currency}>{(p) => <Input {...p} className="font-mono uppercase" value={f.currency} maxLength={3} onChange={set("currency")} placeholder="USD" />}</Field>
              <Field label="Linked device">
                {(p) => (
                  <Select {...p} value={f.device_id} onChange={set("device_id")}>
                    <option value="">None</option>
                    {devices.data?.items.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Rack item">
                {(p) => (
                  <Select {...p} value={f.rack_item_id} onChange={set("rack_item_id")} disabled={!items.data}>
                    <option value="">None</option>
                    {items.data?.items.map((i) => <option key={i.id} value={i.id}>{i.name}{i.rack ? ` · ${i.rack}` : ""}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Stored at">
                {(p) => (
                  <Select {...p} value={f.location_id} onChange={set("location_id")} disabled={!locations.data}>
                    <option value="">None</option>
                    {locations.data?.items.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Labels" hint="Comma separated">{(p) => <Input {...p} value={f.labels} onChange={set("labels")} />}</Field>
              <Field label="Notes" className="sm:col-span-2 lg:col-span-4">{(p) => <Textarea {...p} value={f.notes} maxLength={20000} onChange={set("notes")} />}</Field>
            </div>
          </Panel>
        </fieldset>
        {editable && (
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" loading={save.isPending}>{asset ? "Save changes" : "Add asset"}</Button>
            <Link href="/assets" className="inline-flex h-9 items-center px-3.5 font-semibold text-ink-2 hover:text-ink">Back to assets</Link>
            {save.isSuccess && asset && <span className="text-sm text-ink-2" role="status">Saved.</span>}
            {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
          </div>
        )}
      </form>
      {asset && (
        <Confirm open={confirm} onOpenChange={setConfirm} title={`Delete ${asset.name}?`} confirmLabel="Delete asset" onConfirm={() => remove.mutate()}>
          The record and its dates are removed. To keep the history, set the status to Retired or Disposed instead.
        </Confirm>
      )}
    </>
  );
}
