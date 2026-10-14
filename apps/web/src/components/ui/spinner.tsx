"use client"

import { cn } from "cn"
import { Loader2Icon } from "lucide-react"

import { useI18n } from "@/i18n/provider"

function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  const { t } = useI18n()
  return (
    <Loader2Icon data-slot="spinner" role="status" aria-label={t.common.loading} className={cn("size-4 animate-spin", className)} {...props} />
  )
}

export { Spinner }
