"use client"

import { Server, ExternalLink, Heart } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { useLanguage } from "@/components/language-provider"
import Image from "next/image"

interface OperatingCost {
  name: string
  amount: number
  logo?: string
  icon?: any
}

// Monochrome share shades; hue is reserved for gains and losses
const SHARE_OPACITY = [0.85, 0.6, 0.42, 0.28, 0.16]

interface OperatingCostsCardProps {
  costs?: {
    server_hosting: number
    openai_api: number
    anthropic_api: number
    firecrawl_api: number
    perplexity_api: number
    month: string
  }
}

export function OperatingCostsCard({ costs }: OperatingCostsCardProps) {
  const { language, t } = useLanguage()

  // 기본값 설정 (2026년 1월)
  const defaultCosts = {
    server_hosting: 31.68,
    openai_api: 234.15,
    anthropic_api: 11.4,
    firecrawl_api: 19,
    perplexity_api: 16.5,
    month: "2026-01"
  }

  const actualCosts = costs || defaultCosts

  const costItems: OperatingCost[] = [
    {
      name: t("costs.serverHosting"),
      amount: actualCosts.server_hosting,
      icon: Server
    },
    {
      name: "OpenAI API",
      amount: actualCosts.openai_api,
      logo: "/openai_logo.svg"
    },
    {
      name: "Anthropic API",
      amount: actualCosts.anthropic_api,
      logo: "/claude_logo.svg"
    },
    {
      name: "Firecrawl API",
      amount: actualCosts.firecrawl_api,
      logo: "/firecrawl_logo.svg"
    },
    {
      name: "Perplexity API",
      amount: actualCosts.perplexity_api,
      logo: "/perplexity_logo.svg"
    }
  ]

  const totalCost = costItems.reduce((sum, item) => sum + item.amount, 0)

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value)
  }

  // 월 표시 포맷팅
  const formatMonth = (monthStr: string) => {
    const [year, month] = monthStr.split('-')
    if (language === "en") {
      const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
      return `${monthNames[parseInt(month) - 1]} ${year}`
    }
    return `${year}${t("date.year")} ${parseInt(month)}${t("date.month")}`
  }

  return (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      <CardContent className="p-5">
        <div className="flex flex-col lg:flex-row lg:items-center gap-5">
          {/* Header */}
          <div className="flex items-start justify-between gap-4 lg:w-[300px] lg:shrink-0">
            <div className="space-y-1 min-w-0">
              <h2 className="text-sm font-medium text-foreground">{t("costs.title")}</h2>
              <p className="text-xs text-muted-foreground">{t("costs.description")}</p>
            </div>
            <div className="text-right shrink-0">
              <div className="font-serif text-2xl leading-none tabular-nums">{formatCurrency(totalCost)}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                {formatMonth(actualCosts.month)} {t("costs.basis")} {t("costs.perMonth")}
              </div>
            </div>
          </div>

          {/* 비용 항목들: share bar + list */}
          <div className="flex-1 min-w-0 space-y-3">
            <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
              {costItems.map((item, index) => (
                <div
                  key={index}
                  className="h-full border-r border-card last:border-r-0"
                  style={{ width: `${(item.amount / totalCost) * 100}%`, background: "var(--foreground)", opacity: SHARE_OPACITY[index % 5] }}
                />
              ))}
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-2">
              {costItems.map((item, index) => {
                const Icon = item.icon
                return (
                  <div key={index} className="flex items-center gap-2 min-w-0">
                    {item.logo ? (
                      <Image src={item.logo} alt="" width={14} height={14} className="object-contain w-3.5 h-3.5 shrink-0" />
                    ) : (
                      Icon && <Icon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    )}
                    <div className="min-w-0">
                      <div className="text-[11px] text-muted-foreground truncate">{item.name}</div>
                      <div className="text-sm font-mono tabular-nums">{formatCurrency(item.amount)}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* CTA */}
          <div className="flex items-center justify-between gap-3 lg:flex-col lg:items-end lg:justify-center lg:w-[180px] lg:shrink-0">
            <p className="text-xs text-muted-foreground lg:text-right">{t("costs.helpQuestion")}</p>
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 shrink-0"
              onClick={() => window.open('https://github.com/sponsors/dragon1086', '_blank')}
            >
              <Heart className="w-3.5 h-3.5 text-destructive" />
              {t("costs.becomeSponsor")}
              <ExternalLink className="w-3 h-3 text-muted-foreground" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
