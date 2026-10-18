"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import type { DashboardData, Market } from "@/types/dashboard"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil } from "@/lib/currency"

interface AIDecisionsPageProps {
  data: DashboardData
  market?: Market
}

const CARD = "gap-0 py-0 rounded-lg shadow-none"
const META_BADGE = "text-[10px] font-normal text-muted-foreground"
const AMBER = "text-amber-600 dark:text-amber-400"

export function AIDecisionsPage({ data, market = "KR" }: AIDecisionsPageProps) {
  const { t, language } = useLanguage()

  const formatCurrency = (value: number) => {
    return formatCurrencyUtil(value, market, language as "ko" | "en")
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    })
  }

  const formatTime = (timeString: string) => {
    const [hours, minutes] = timeString.split(":")
    return `${hours}:${minutes}`
  }

  // holding_decisions를 날짜별로 그룹화
  const decisionsByDate = data.holding_decisions?.reduce((acc, decision) => {
    if (!acc[decision.decision_date]) {
      acc[decision.decision_date] = []
    }
    acc[decision.decision_date].push(decision)
    return acc
  }, {} as Record<string, typeof data.holding_decisions>) || {}

  const sortedDates = Object.keys(decisionsByDate).sort((a, b) => b.localeCompare(a))

  // holdings와 매칭하여 종목 정보 가져오기
  const getStockInfo = (ticker: string) => {
    return data.holdings?.find(h => h.ticker === ticker)
  }

  const adjustmentCount = data.holding_decisions?.filter(d => d.portfolio_adjustment_needed === 1).length || 0

  const kpis = [
    {
      label: t("aiDecisions.totalAnalysisRecords"),
      value: `${data.holding_decisions?.length || 0}${t("aiDecisions.records")}`,
      sub: t("aiDecisions.allHoldDecisions"),
      tone: "text-foreground",
    },
    {
      label: t("aiDecisions.adjustmentNeeded"),
      value: `${adjustmentCount}${t("aiDecisions.records")}`,
      sub: t("aiDecisions.portfolioRebalanceRecommended"),
      tone: adjustmentCount > 0 ? AMBER : "text-foreground",
    },
    {
      label: t("aiDecisions.analyzedStocksCount"),
      value: `${new Set(data.holding_decisions?.map(d => d.ticker)).size || 0}${t("aiDecisions.stocksCount")}`,
      sub: t("aiDecisions.cumulativeBasis"),
      tone: "text-foreground",
    },
  ]

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-foreground">{t("aiDecisions.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("aiDecisions.pageDescription")}</p>
        </div>
        <span className="text-xs text-muted-foreground font-mono tabular-nums">
          {t("aiDecisions.totalAnalysis")} {data.holding_decisions?.length || 0}{t("aiDecisions.recordsCount")}
        </span>
      </div>

      {/* 통계 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {kpis.map((kpi) => (
          <Card key={kpi.label} className={CARD}>
            <CardContent className="p-4 sm:p-5 space-y-2.5 sm:space-y-3">
              <span className="text-[13px] text-muted-foreground block">{kpi.label}</span>
              <p className={`font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums ${kpi.tone}`}>
                {kpi.value}
              </p>
              <p className="text-xs text-muted-foreground">{kpi.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* 일별 분석 내역 */}
      <div className="space-y-4">
        {sortedDates.map(date => (
          <Card key={date} className={CARD}>
            <CardHeader className="px-5 pt-4 pb-3 border-b">
              <div className="flex items-baseline justify-between gap-2">
                <CardTitle className="text-sm font-medium">{formatDate(date)}</CardTitle>
                <span className="text-xs text-muted-foreground font-mono tabular-nums">
                  {decisionsByDate[date].length}{t("aiDecisions.analysisCount")}
                </span>
              </div>
            </CardHeader>
            <CardContent className="p-0 divide-y">
              {decisionsByDate[date].map((decision) => {
                const stock = getStockInfo(decision.ticker)
                // company_name은 decision에서 먼저 가져오고, 없으면 stock에서 가져오고, 둘 다 없으면 ticker 표시
                const companyName = decision.company_name || stock?.company_name || decision.ticker
                const needsAdjustment = decision.portfolio_adjustment_needed === 1
                return (
                  <div key={decision.id} className="p-4 sm:p-5 space-y-4">
                    {/* 종목 헤더 */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <h3 className="text-base font-semibold text-foreground">{companyName}</h3>
                          <Badge variant="outline" className={META_BADGE}>{decision.ticker}</Badge>
                          <Badge variant="outline" className={META_BADGE}>{t("aiDecisions.holdMaintain")}</Badge>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground font-mono tabular-nums">
                          {t("aiDecisions.analysisTime")} {formatTime(decision.decision_time)}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="text-xs text-muted-foreground">{t("aiDecisions.confidence")}</p>
                        <div className="mt-1 flex items-center justify-end gap-2">
                          <div className="w-12 h-1.5 bg-muted rounded-full overflow-hidden" aria-hidden="true">
                            <div
                              className="h-full bg-foreground/60 rounded-full"
                              style={{ width: `${(decision.confidence / 10) * 100}%` }}
                            />
                          </div>
                          <span className="font-mono tabular-nums text-[13px] font-medium text-foreground">
                            {decision.confidence}/10
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* 현재 가격 정보 */}
                    {stock && (
                      <dl className="grid grid-cols-2 sm:grid-cols-5 gap-x-4 gap-y-3 rounded-md border px-4 py-3">
                        {[
                          { label: t("aiDecisions.analysisPricePoint"), value: formatCurrency(decision.current_price), tone: "text-foreground" },
                          { label: t("table.buyPrice"), value: formatCurrency(stock.buy_price || 0), tone: "text-foreground" },
                          { label: t("table.targetPrice"), value: formatCurrency(stock.target_price || 0), tone: "text-success" },
                          { label: t("table.stopLoss"), value: formatCurrency(stock.stop_loss || 0), tone: "text-destructive" },
                          {
                            label: t("table.profitRate"),
                            value: formatPercent(stock.profit_rate || 0),
                            tone: (stock.profit_rate || 0) >= 0 ? "text-success" : "text-destructive",
                          },
                        ].map((item) => (
                          <div key={item.label} className="min-w-0">
                            <dt className="text-xs text-muted-foreground">{item.label}</dt>
                            <dd className={`mt-0.5 font-mono tabular-nums text-[13px] font-medium ${item.tone}`}>{item.value}</dd>
                          </div>
                        ))}
                      </dl>
                    )}

                    {/* AI 판단 근거 */}
                    <div className="max-w-[70ch]">
                      <p className="text-xs text-muted-foreground mb-1">{t("aiDecisions.aiJudgementBasis")}</p>
                      <p className="text-sm text-foreground leading-relaxed">{decision.sell_reason}</p>
                    </div>

                    {/* 분석 요약 */}
                    <div className="grid md:grid-cols-2 gap-x-6 gap-y-3">
                      {[
                        { label: t("aiDecisions.technicalTrend"), text: decision.technical_trend },
                        { label: t("aiDecisions.volumeAnalysis"), text: decision.volume_analysis },
                        { label: t("aiDecisions.marketImpact"), text: decision.market_condition_impact },
                        { label: t("aiDecisions.timeFactor2"), text: decision.time_factor },
                      ].map((item) => (
                        <div key={item.label} className="max-w-[70ch]">
                          <p className="text-xs text-muted-foreground mb-1">{item.label}</p>
                          <p className="text-[13px] text-foreground/90 leading-relaxed">{item.text}</p>
                        </div>
                      ))}
                    </div>

                    {/* 포트폴리오 조정 */}
                    <div className={`border-l-2 pl-3 ${needsAdjustment ? "border-amber-500/60" : "border-border"}`}>
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <p className={`text-[13px] font-medium ${needsAdjustment ? AMBER : "text-foreground"}`}>
                          {needsAdjustment
                            ? t("aiDecisions.portfolioAdjustmentNeeded")
                            : t("aiDecisions.maintainStrategy")}
                        </p>
                        {decision.adjustment_urgency && (
                          <Badge
                            variant="outline"
                            className={`text-[10px] font-normal ${needsAdjustment ? AMBER : "text-muted-foreground"}`}
                          >
                            {t("aiDecisions.urgency")}: {decision.adjustment_urgency}
                          </Badge>
                        )}
                      </div>
                      <p className="text-[13px] text-foreground/90 leading-relaxed max-w-[70ch]">{decision.adjustment_reason}</p>

                      {/* 목표가/손절가 변경 정보 */}
                      {(decision.new_target_price > 0 || decision.new_stop_loss > 0) && (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 pt-3 border-t">
                          {decision.new_target_price > 0 && (
                            <div>
                              <p className="text-xs text-muted-foreground mb-0.5">{t("aiDecisions.newTargetPriceHistory")}</p>
                              <p className="font-mono tabular-nums text-[13px]">
                                <span className="line-through text-muted-foreground">
                                  {formatCurrency(stock?.scenario?.target_price || 0)}
                                </span>
                                <span className="mx-1.5 text-muted-foreground">→</span>
                                <span className="font-medium text-success">{formatCurrency(decision.new_target_price)}</span>
                              </p>
                            </div>
                          )}
                          {decision.new_stop_loss > 0 && (
                            <div>
                              <p className="text-xs text-muted-foreground mb-0.5">{t("aiDecisions.newStopLossHistory")}</p>
                              <p className="font-mono tabular-nums text-[13px]">
                                <span className="line-through text-muted-foreground">
                                  {formatCurrency(stock?.scenario?.stop_loss || 0)}
                                </span>
                                <span className="mx-1.5 text-muted-foreground">→</span>
                                <span className="font-medium text-destructive">{formatCurrency(decision.new_stop_loss)}</span>
                              </p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>
        ))}
      </div>

      {(!data.holding_decisions || data.holding_decisions.length === 0) && (
        <Card className={CARD}>
          <CardContent className="p-4 sm:p-5">
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t("aiDecisions.noAnalysisDataYet")}</span>{" "}
              {language === "ko"
                ? "보유 종목이 생기면 AI의 일일 보유 판단이 여기에 쌓입니다."
                : "Once positions are open, the AI's daily holding reviews appear here."}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
