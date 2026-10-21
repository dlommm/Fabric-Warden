"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

/** Subscribes to the server event stream and refreshes affected queries. */
export function useLiveEvents(enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const keys = new Set<string>();
    const flush = () => {
      keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      keys.clear();
      timer = null;
    };
    const es = new EventSource("/api/events/stream", { withCredentials: true });
    const onEvent = (ev: MessageEvent) => {
      const type = (ev.type === "message" ? JSON.parse(ev.data).type : ev.type) as string;
      if (type.startsWith("inventory.") || type.startsWith("controller.")) ["summary", "devices", "device", "clients", "networks", "controllers", "events"].forEach((k) => keys.add(k));
      if (type.startsWith("module.")) keys.add("modules");
      timer ??= setTimeout(flush, 800);
    };
    const types = [
      "inventory.device.discovered", "inventory.device.state_changed", "inventory.device.firmware_changed", "inventory.device.missing",
      "inventory.client.connected", "inventory.client.disconnected", "controller.status_changed", "controller.sync.completed",
      "module.enabled", "module.disabled",
    ];
    types.forEach((t) => es.addEventListener(t, onEvent));
    return () => {
      types.forEach((t) => es.removeEventListener(t, onEvent));
      es.close();
      if (timer) clearTimeout(timer);
    };
  }, [enabled, qc]);
}
