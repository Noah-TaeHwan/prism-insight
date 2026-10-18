"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { HelpCircle } from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { TriggerReliabilityData, Market } from "@/types/dashboard"

interface TriggerReliabilityCardProps {
  data: TriggerReliabilityData
  market?: Market
}

const GRADE_CLASS: Record<"A" | "B" | "C" | "D", string> = {
  A: "border-success/40 text-success",
  B: "border-foreground/30 text-foreground",
  C: "border-amber-500/40 text-amber-600 dark:text-amber-400",
  D: "text-muted-foreground",
}

export function TriggerReliabilityCard({ data, market = "KR" }: TriggerReliabilityCardProps) {
  const { t, language } = useLanguage()

  const formatPercent = (value: number | null) => {
    if (value === null || value === undefined) return "-"
    return `${(value * 100).toFixed(0)}%`
  }

  const header = (
    <CardHeader className="px-5 pt-4 pb-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <CardTitle className="text-sm font-medium">{t("insights.triggerReliability.title")}</CardTitle>
        {data.best_trigger && (
          <span className="text-xs text-muted-foreground">
            {t("insights.triggerReliability.bestTrigger")}:{" "}
            <span className="text-foreground">{data.best_trigger}</span>{" "}
            <span className="font-mono">
              ({data.trigger_reliability?.find(t => t.trigger_type === data.best_trigger)?.grade || "?"})
            </span>
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{t("insights.triggerReliability.description")}</p>
    </CardHeader>
  )

  if (!data.trigger_reliability || data.trigger_reliability.length === 0) {
    return (
      <Card className="gap-0 py-0 rounded-lg shadow-none">
        {header}
        <CardContent className="p-4 sm:p-5 pt-0 sm:pt-0">
          <p className="py-8 text-center text-sm text-muted-foreground">{t("insights.triggerReliability.noData")}</p>
        </CardContent>
      </Card>
    )
  }

  const th = "h-9 px-2 text-xs font-normal text-muted-foreground whitespace-nowrap"
  const help = (text: string) => (
    <Tooltip>
      <TooltipTrigger aria-label={text}>
        <HelpCircle className="w-3 h-3" />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  )

  return (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      {header}
      <CardContent className="px-0 pb-1">
        <TooltipProvider>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b">
                  <th className={`${th} pl-5 text-left`}>{t("insights.triggerReliability.grade")}</th>
                  <th className={`${th} text-left`}>{language === "ko" ? "트리거" : "Trigger"}</th>
                  <th className={`${th} text-right`}>
                    <div className="flex items-center justify-end gap-1">
                      {t("insights.triggerReliability.analysisAccuracy")}
                      {help(language === "ko"
                        ? "분석 후 관망한 종목의 30일 추적 성과. 완료/전체, 승률"
                        : "30-day tracking of watched stocks. Completed/Total, Win rate")}
                    </div>
                  </th>
                  <th className={`${th} text-right`}>
                    <div className="flex items-center justify-end gap-1">
                      {t("insights.triggerReliability.actualTrading")}
                      {help(language === "ko"
                        ? "실제 매매 건수, 승률, Profit Factor"
                        : "Actual trades count, win rate, profit factor")}
                    </div>
                  </th>
                  <th className={`${th} text-right`}>{t("insights.triggerReliability.principles")}</th>
                  <th className={`${th} pr-5 text-left`}>{t("insights.triggerReliability.recommendation")}</th>
                </tr>
              </thead>
              <tbody>
                {data.trigger_reliability.map((item, idx) => (
                  <tr key={idx} className="border-b last:border-b-0 hover:bg-muted/50 transition-colors align-top">
                    <td className="pl-5 pr-2 py-2.5">
                      <Badge variant="outline" className={`font-mono text-[11px] font-medium ${GRADE_CLASS[item.grade] ?? "text-muted-foreground"}`}>
                        {item.grade}
                      </Badge>
                    </td>
                    <td className="px-2 py-2.5 font-medium min-w-[140px]">{item.trigger_type}</td>
                    <td className="px-2 py-2.5 text-right font-mono tabular-nums text-[13px]">
                      <div className={
                        item.analysis_accuracy.win_rate_30d !== null && item.analysis_accuracy.win_rate_30d >= 0.5
                          ? "text-success"
                          : "text-muted-foreground"
                      }>
                        {t("insights.triggerReliability.winRate")} {formatPercent(item.analysis_accuracy.win_rate_30d)}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {item.analysis_accuracy.completed}/{item.analysis_accuracy.total_tracked} {t("insights.triggerReliability.completed")}
                      </div>
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono tabular-nums text-[13px]">
                      <div>
                        {item.actual_trading.count} {t("insights.triggerReliability.trades")}
                      </div>
                      <div className="flex items-center justify-end gap-2 text-[11px]">
                        <span className={
                          item.actual_trading.win_rate !== null && item.actual_trading.win_rate >= 0.5
                            ? "text-success"
                            : "text-muted-foreground"
                        }>
                          {t("insights.triggerReliability.winRate")} {formatPercent(item.actual_trading.win_rate)}
                        </span>
                        <Tooltip>
                          <TooltipTrigger>
                            <span className={
                              item.actual_trading.profit_factor === null
                                ? "text-muted-foreground"
                                : item.actual_trading.profit_factor >= 1
                                  ? "text-success"
                                  : "text-destructive"
                            }>
                              PF {item.actual_trading.profit_factor !== null
                                ? item.actual_trading.profit_factor.toFixed(2)
                                : "-"}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>Profit Factor: {language === "ko" ? "총수익÷총손실" : "Total Profit ÷ Total Loss"}</p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </td>
                    <td className="px-2 py-2.5 text-right">
                      <Tooltip>
                        <TooltipTrigger className="font-mono tabular-nums text-[13px] underline decoration-dotted decoration-muted-foreground/50 underline-offset-4">
                          {item.related_principles.length}
                        </TooltipTrigger>
                        <TooltipContent className="max-w-sm">
                          {item.related_principles.length === 0 ? (
                            <p>{language === "ko" ? "관련 원칙 없음" : "No related principles"}</p>
                          ) : (
                            <div className="space-y-2 max-h-64 overflow-y-auto">
                              {item.related_principles.map((principle, i) => (
                                <div key={i} className="text-xs border-b pb-2 last:border-b-0">
                                  <p className="font-medium">{principle.action}</p>
                                  <p className="text-muted-foreground mt-1">
                                    {language === "ko" ? "신뢰도" : "Confidence"}: {(principle.confidence * 100).toFixed(0)}%
                                    ({principle.supporting_trades} {language === "ko" ? "거래" : "trades"})
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </TooltipContent>
                      </Tooltip>
                    </td>
                    <td className="pl-2 pr-5 py-2.5 min-w-[220px]">
                      <p className="text-xs text-muted-foreground line-clamp-2">
                        {item.recommendation}
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TooltipProvider>
      </CardContent>
    </Card>
  )
}
