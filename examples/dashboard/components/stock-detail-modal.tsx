"use client"

import type { ReactNode } from "react"
import { X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil } from "@/lib/currency"
import type { Holding, Market } from "@/types/dashboard"

interface StockDetailModalProps {
  stock: Holding
  onClose: () => void
  isRealTrading?: boolean
  market?: Market
}

const META_BADGE = "text-[10px] font-normal text-muted-foreground"

export function StockDetailModal({ stock, onClose, isRealTrading = false, market = "KR" }: StockDetailModalProps) {
  const { t, language } = useLanguage()

  const formatCurrency = (value: number) => {
    return formatCurrencyUtil(value ?? 0, market, language as "ko" | "en")
  }

  const formatPercent = (value: number) => {
    const safeValue = value ?? 0
    return `${safeValue >= 0 ? "+" : ""}${safeValue.toFixed(2)}%`
  }

  const formatDate = (dateString?: string) => {
    if (!dateString) return "-"
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    })
  }

  const stockName = stock.company_name || stock.name || t("table.unknown")
  const scenario = stock.scenario

  const row = (label: string, value: ReactNode, valueClass = "text-foreground", last = false) => (
    <div className={`flex justify-between items-center gap-4 py-2.5 ${last ? "" : "border-b border-border"}`}>
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={`font-mono tabular-nums text-[13px] text-right ${valueClass}`}>{value}</span>
    </div>
  )

  const textBlock = (label: string, text: string) => (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-1">{label}</p>
      <p className="text-sm text-foreground leading-relaxed break-words">{text}</p>
    </div>
  )

  const rr = scenario?.risk_reward_ratio
  const rrTone = rr === undefined ? "" : rr >= 3 ? "text-success" : rr >= 2 ? "text-muted-foreground" : "text-destructive"

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm">
      <Card role="dialog" aria-modal="true" aria-label={stockName} className="w-full max-w-4xl max-h-[90vh] overflow-y-auto gap-0 py-0 rounded-lg shadow-md">
        <CardContent className="p-4 sm:p-5">
          {/* 헤더 */}
          <div className="flex items-start justify-between gap-3 mb-5">
            <div className="min-w-0 space-y-1.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h2 className="text-lg font-semibold tracking-tight text-foreground">{stockName}</h2>
                <span className="text-xs text-muted-foreground font-mono">{stock.ticker}</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {isRealTrading ? (
                  <>
                    <Badge variant="outline" className={META_BADGE}>{t("badge.realTrading")}</Badge>
                    <Badge variant="outline" className={META_BADGE}>{t("badge.koreanInvestment")}</Badge>
                  </>
                ) : (
                  <>
                    <Badge variant="outline" className={META_BADGE}>{t("badge.aiSimulation")}</Badge>
                    {scenario?.sector && <Badge variant="outline" className={META_BADGE}>{scenario.sector}</Badge>}
                    {scenario?.investment_period && <Badge variant="outline" className={META_BADGE}>{scenario.investment_period}</Badge>}
                    {scenario?.buy_score && (
                      <Badge variant="outline" className={`${META_BADGE} font-mono tabular-nums`}>
                        {t("modal.buyScore")} {scenario.buy_score}/10
                      </Badge>
                    )}
                  </>
                )}
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} className="size-8 shrink-0" aria-label={language === "ko" ? "닫기" : "Close"}>
              <X className="w-4 h-4" />
            </Button>
          </div>

          {/* 현재가 및 수익률 */}
          <div className="grid grid-cols-2 gap-3 mb-5">
            <div className="rounded-md border border-border p-3 sm:p-4 space-y-2">
              <p className="text-[13px] text-muted-foreground">{t("modal.currentPrice")}</p>
              <p className="font-serif text-[1.5rem] sm:text-[1.75rem] leading-none tracking-tight tabular-nums text-foreground">{formatCurrency(stock.current_price ?? 0)}</p>
            </div>
            <div className="rounded-md border border-border p-3 sm:p-4 space-y-2">
              <p className="text-[13px] text-muted-foreground">{t("modal.profitRate")}</p>
              <p className={`font-serif text-[1.5rem] sm:text-[1.75rem] leading-none tracking-tight tabular-nums ${(stock.profit_rate ?? 0) >= 0 ? "text-success" : "text-destructive"}`}>
                {formatPercent(stock.profit_rate ?? 0)}
              </p>
            </div>
          </div>

          {/* 실전투자 상세 정보 */}
          {isRealTrading && (
            <div className="mb-5">
              <h3 className="text-sm font-medium text-foreground mb-1">{t("modal.holdingInfo")}</h3>
              {row(t("modal.holdingQuantity"), `${(stock.quantity ?? 0).toLocaleString()}${t("common.shares")}`)}
              {row(t("modal.avgBuyPrice"), formatCurrency(stock.avg_price ?? 0))}
              {row(t("modal.evaluationAmount"), formatCurrency(stock.value ?? 0))}
              {row(
                t("modal.evaluationPL"),
                formatCurrency(stock.profit ?? 0),
                (stock.profit ?? 0) >= 0 ? "text-success" : "text-destructive",
                !(stock.weight !== undefined && stock.weight > 0),
              )}
              {stock.weight !== undefined && stock.weight > 0 &&
                row(t("modal.portfolioWeight"), `${(stock.weight ?? 0).toFixed(2)}%`, "text-foreground", true)}
            </div>
          )}

          {/* 시뮬레이터 상세 정보 */}
          {!isRealTrading && (
            <div className="mb-5">
              <h3 className="text-sm font-medium text-foreground mb-1">{t("modal.tradingInfo")}</h3>
              {row(t("modal.buyDate"), formatDate(stock.buy_date))}
              {row(t("modal.buyPrice"), formatCurrency(stock.buy_price ?? 0))}
              {row(t("modal.targetPrice"), formatCurrency(stock.target_price ?? 0), "text-success")}
              {row(t("modal.stopLossPrice"), formatCurrency(stock.stop_loss ?? 0), "text-destructive")}
              {row(t("modal.holdingDays"), `${stock.holding_days ?? 0}${t("common.days")}`)}
              {row(t("modal.lastUpdate"), formatDate(stock.last_updated), "text-foreground", true)}
            </div>
          )}

          {/* AI 매매 시나리오 (공통) */}
          {scenario && (
            <>
              <Separator className="my-5" />

              <div className="space-y-4">
                <h3 className="text-sm font-medium text-foreground">{t("modal.aiTradingScenario")}</h3>

                {/* 매수 결정 */}
                {scenario.decision && (
                  <div className="rounded-md border border-border p-3 sm:p-4">
                    <div className="flex items-center justify-between gap-3 mb-2">
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <p className="text-xs font-medium text-muted-foreground">{t("modal.buyDecision")}</p>
                        {scenario.entry_checklist_passed !== undefined && (
                          <span className="text-xs text-muted-foreground font-mono tabular-nums">
                            ({t("watchlist.entryChecklist")}: {scenario.entry_checklist_passed}/6)
                          </span>
                        )}
                      </div>
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-normal ${scenario.decision === t("watchlist.entry") ? "text-success" : "text-muted-foreground"}`}
                      >
                        {scenario.decision}
                      </Badge>
                    </div>
                    {scenario.rationale && (
                      <p className="text-sm text-foreground leading-relaxed break-words">{scenario.rationale}</p>
                    )}
                    {scenario.rejection_reason && scenario.decision !== t("watchlist.entry") && (
                      <p className="text-xs text-amber-600 dark:text-amber-400 mt-2 pt-2 border-t border-border">
                        {t("watchlist.rejectionReason")}: {scenario.rejection_reason}
                      </p>
                    )}
                  </div>
                )}

                {/* 목표가/손절가 */}
                {(scenario.target_price || scenario.stop_loss) && (
                  <div className="grid grid-cols-2 gap-3">
                    {scenario.target_price && (
                      <div className="rounded-md border border-border p-3">
                        <p className="text-xs text-muted-foreground mb-1">{t("modal.targetPrice")}</p>
                        <p className="font-mono tabular-nums text-sm text-success">{formatCurrency(scenario.target_price)}</p>
                      </div>
                    )}
                    {scenario.stop_loss && (
                      <div className="rounded-md border border-border p-3">
                        <p className="text-xs text-muted-foreground mb-1">{t("modal.stopLossPrice")}</p>
                        <p className="font-mono tabular-nums text-sm text-destructive">{formatCurrency(scenario.stop_loss)}</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Risk/Reward Ratio */}
                {scenario.risk_reward_ratio && (
                  <div className="rounded-md border border-border p-3 sm:p-4">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-medium text-muted-foreground">{t("modal.riskRewardRatio")}</p>
                      <Badge variant="outline" className={`text-[11px] font-mono tabular-nums ${rrTone}`}>
                        {scenario.risk_reward_ratio.toFixed(1)}
                      </Badge>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      {scenario.expected_return_pct && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("modal.expectedReturn")}</p>
                          <p className="font-mono tabular-nums text-sm text-success">+{scenario.expected_return_pct.toFixed(1)}%</p>
                        </div>
                      )}
                      {scenario.expected_loss_pct && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("modal.expectedLoss")}</p>
                          <p className="font-mono tabular-nums text-sm text-destructive">-{scenario.expected_loss_pct.toFixed(1)}%</p>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {scenario.portfolio_analysis && textBlock(t("modal.portfolioAnalysis"), scenario.portfolio_analysis)}
                {scenario.valuation_analysis && textBlock(t("modal.valuationAnalysis"), scenario.valuation_analysis)}
                {scenario.sector_outlook && textBlock(t("modal.sectorOutlook"), scenario.sector_outlook)}
                {scenario.market_condition && textBlock(t("modal.marketCondition"), scenario.market_condition)}
                {scenario.max_portfolio_size && textBlock(t("modal.maxPortfolioSize"), String(scenario.max_portfolio_size))}

                {/* 주요 가격대 */}
                {scenario.trading_scenarios?.key_levels && Object.keys(scenario.trading_scenarios.key_levels).length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.keyPriceLevels")}</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {Object.entries(scenario.trading_scenarios.key_levels).map(([key, value]) => {
                        // 한글 레이블 매핑
                        const labelMap: Record<string, string> = {
                          'primary_support': t("modal.primarySupport"),
                          'secondary_support': t("modal.secondarySupport"),
                          'primary_resistance': t("modal.primaryResistance"),
                          'secondary_resistance': t("modal.secondaryResistance"),
                          'volume_baseline': t("modal.volumeBaseline")
                        }
                        const label = labelMap[key] || key

                        return (
                          <div key={key} className="rounded-md bg-muted/50 px-3 py-2">
                            <p className="text-xs text-muted-foreground">{label}</p>
                            <p className="text-[13px] text-foreground tabular-nums">
                              {typeof value === 'number' ? formatCurrency(value) : String(value)}
                            </p>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* 매도 트리거 */}
                {scenario.trading_scenarios?.sell_triggers && scenario.trading_scenarios.sell_triggers.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.sellTriggers")}</p>
                    <ul className="space-y-1.5">
                      {scenario.trading_scenarios.sell_triggers.map((trigger, index) => (
                        <li key={index} className="text-sm text-foreground flex items-start gap-2">
                          <span className="text-destructive" aria-hidden="true">•</span>
                          <span className="flex-1 min-w-0 break-words">{trigger}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 보유 조건 */}
                {scenario.trading_scenarios?.hold_conditions && scenario.trading_scenarios.hold_conditions.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.holdConditions")}</p>
                    <ul className="space-y-1.5">
                      {scenario.trading_scenarios.hold_conditions.map((condition, index) => (
                        <li key={index} className="text-sm text-foreground flex items-start gap-2">
                          <span className="text-success" aria-hidden="true">•</span>
                          <span className="flex-1 min-w-0 break-words">{condition}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {scenario.trading_scenarios?.portfolio_context &&
                  textBlock(t("modal.portfolioContext"), scenario.trading_scenarios.portfolio_context)}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
