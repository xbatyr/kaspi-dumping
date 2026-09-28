"use client";

import { useRouter } from "next/navigation";

import { BulkRuleDialog } from "@/components/BulkRuleDialog";

export function BulkRuleLauncher({ ruleIds }: { ruleIds: number[] }) {
  const router = useRouter();
  if (ruleIds.length === 0) return null;

  // The panel reports what it changed itself, so closing just leaves the page.
  return <BulkRuleDialog ruleIds={ruleIds} onClose={() => router.replace("/strategies")} />;
}
