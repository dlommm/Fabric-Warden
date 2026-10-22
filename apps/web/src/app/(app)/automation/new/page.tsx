"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { EMPTY_RULE, RuleEditor } from "@/components/automation";
import { PageHeader } from "@/components/ui/layout-bits";
import { post } from "@/lib/api";
import type { AutomationRule, AutomationRuleInput } from "@/lib/types";

export default function NewRulePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (r: AutomationRuleInput) => post<AutomationRule>("/api/automation/rules", r),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["automation"] });
      router.push(`/automation/${r.id}`);
    },
  });
  return (
    <>
      <Link href="/automation" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
        <ChevronLeft className="size-4" aria-hidden /> Automation
      </Link>
      <PageHeader title="New rule" description="A new rule is saved turned off. Try it with a dry run, then turn it on; it acts only on events from that moment." />
      <RuleEditor initial={EMPTY_RULE} saving={save.isPending} error={save.error} onSave={(r) => save.mutate(r)} saveLabel="Save rule" />
    </>
  );
}
