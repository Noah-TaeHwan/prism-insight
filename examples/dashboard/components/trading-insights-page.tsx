"use client"

import { useState } from "react"
import type React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { HelpCircle } from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { TradingInsightsData, SituationAnalysis, JudgmentEvaluation, Market, ObservabilityInsightsSnapshot } from "@/types/dashboard"
import { TriggerReliabilityCard } from "./trigger-reliability-card"
import { ObservabilityInsightsPanel } from "./observability-insights-panel"

type MarketFilter = "all" | "KR" | "US"

interface TradingInsightsPageProps {
  data: TradingInsightsData
  market?: Market
  observability?: ObservabilityInsightsSnapshot | null
}

// Helper to safely parse JSON
function tryParseJSON<T>(str: string | T): T | null {
  if (typeof str !== 'string') return str as T
  try {
    return JSON.parse(str) as T
  } catch {
    return null
  }
}

const CARD = "gap-0 py-0 rounded-lg shadow-none"
const META_BADGE = "text-[10px] font-normal text-muted-foreground"
const TH = "h-9 px-2 text-xs font-normal text-muted-foreground whitespace-nowrap text-right first:pl-5 first:text-left last:pr-5"
const TD_NUM = "px-2 py-2 text-right font-mono tabular-nums text-[13px] last:pr-5"
const TD_FIRST = "pl-5 pr-2 py-2 font-medium"
const ROW = "border-b last:border-b-0 hover:bg-muted/50 transition-colors"

// Sign-only coloring for returns
const signTone = (value: number | null | undefined) =>
  value === null || value === undefined || value === 0 ? "" : value > 0 ? "text-success" : "text-destructive"

function Help({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger aria-label={text}>
        <HelpCircle className="w-3 h-3 text-muted-foreground" />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function SubHeading({ children, help }: { children: React.ReactNode; help?: string }) {
  return (
    <h4 className="px-5 text-[13px] font-medium text-foreground flex items-center gap-1.5">
      {children}
      {help && <Help text={help} />}
    </h4>
  )
}

function Stat({ label, value, className, sub }: { label: React.ReactNode; value: React.ReactNode; className?: string; sub?: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <span className="text-[13px] text-muted-foreground flex items-center gap-1">{label}</span>
      <p className={`font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums ${className || ""}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-muted-foreground tabular-nums">{sub}</p>}
    </div>
  )
}

function MiniStat({ label, value, className }: { label: React.ReactNode; value: React.ReactNode; className?: string }) {
  return (
    <div className="bg-card p-3">
      <span className="text-xs text-muted-foreground flex items-center gap-1">{label}</span>
      <p className={`mt-1 font-mono tabular-nums text-[15px] ${className || ""}`}>{value}</p>
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <h3 className="text-base font-semibold tracking-tight">{children}</h3>
      <div className="flex-1 h-px bg-border" />
    </div>
  )
}

export function TradingInsightsPage({ data, market = "KR", observability }: TradingInsightsPageProps) {
  const { t, language } = useLanguage()
  const [marketFilter, setMarketFilter] = useState<MarketFilter>("all")

  // Filter data based on market filter
  // Note: Journal entries are common (not filtered), but principles and intuitions are market-specific
  const filteredPrinciples = marketFilter === "all"
    ? data.principles
    : data.principles.filter(p => p.market === marketFilter || !p.market)

  const filteredIntuitions = marketFilter === "all"
    ? data.intuitions
    : data.intuitions.filter(i => i.market === marketFilter || !i.market)

  // Journal entries are NOT filtered - they are common across markets
  const journalEntries = data.journal_entries

  const formatDate = (dateString: string) => {
    if (!dateString) return "-"
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  }

  const formatPercent = (value: number) => {
    if (value === null || value === undefined) return "-"
    // value is in decimal form (0.07 = 7%), multiply by 100 for display
    const percentage = value * 100
    const sign = percentage >= 0 ? "+" : ""
    return `${sign}${percentage.toFixed(2)}%`
  }

  // For values already in percent form (e.g., 35.57 = 35.57%)
  const formatPercentDirect = (value: number) => {
    if (value === null || value === undefined) return "-"
    const sign = value >= 0 ? "+" : ""
    return `${sign}${value.toFixed(2)}%`
  }

  // High priority stands out by weight, not hue
  const getPriorityColor = (priority: string) =>
    priority === "high" ? "border-foreground/30 text-foreground" : "text-muted-foreground"

  const getConfidenceBar = (confidence: number) => {
    const percentage = Math.round(confidence * 100)
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
          <div className="h-full rounded-full bg-foreground/60" style={{ width: `${percentage}%` }} />
        </div>
        <span className="text-xs text-muted-foreground font-mono tabular-nums w-9 text-right">{percentage}%</span>
      </div>
    )
  }

  const marketBadge = (m: string) => (
    <Badge variant="outline" className={`${META_BADGE} font-mono`}>{m}</Badge>
  )

  const filterButton = (value: MarketFilter, label: string) => (
    <button
      onClick={() => setMarketFilter(value)}
      aria-pressed={marketFilter === value}
      className={`px-3 h-7 rounded-md text-xs transition-colors ${
        marketFilter === value
          ? "bg-secondary text-foreground font-medium"
          : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {label}
    </button>
  )

  const pa = data.performance_analysis

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-foreground">{t("insights.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("insights.description")}</p>
        </div>

        {/* Market Filter */}
        <div className="inline-flex self-start sm:self-auto items-center rounded-lg border p-0.5 gap-0.5">
          {filterButton("all", language === "ko" ? "전체" : "All")}
          {filterButton("KR", language === "ko" ? "한국" : "Korea")}
          {filterButton("US", language === "ko" ? "미국" : "US")}
        </div>
      </div>

      {observability && (
        <ObservabilityInsightsPanel data={observability} market={market} />
      )}

      {/* Trigger Reliability Card */}
      {data.trigger_reliability && data.trigger_reliability.trigger_reliability.length > 0 && (
        <TriggerReliabilityCard data={data.trigger_reliability} market={market} />
      )}

      {/* Two Category Summary Boxes */}
      <TooltipProvider>
        <div className="grid md:grid-cols-2 gap-3">
          {/* Performance Analysis Summary */}
          <Card className={CARD}>
            <CardHeader className="px-5 pt-4 pb-3">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm font-medium">{t("insights.category.performance")}</CardTitle>
                <Badge variant="outline" className={META_BADGE}>
                  {language === "ko" ? "최근 1개월" : "Last 30d"}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="px-5 pb-5 pt-1">
              <div className="grid grid-cols-2 gap-x-4 gap-y-5">
                <Stat
                  label={language === "ko" ? "실제 매매" : "Actual Trades"}
                  value={`${pa?.actual_trading?.count || 0}${language === "ko" ? "건" : ""}`}
                  sub={pa?.actual_trading?.win_rate !== undefined
                    ? `${language === "ko" ? "승률" : "WR"} ${(pa.actual_trading.win_rate * 100).toFixed(0)}%`
                    : undefined}
                />
                <Stat
                  label={language === "ko" ? "평균 수익률" : "Avg Return"}
                  value={pa?.actual_trading?.avg_profit_rate !== undefined
                    ? formatPercent(pa.actual_trading.avg_profit_rate)
                    : "-"}
                  className={signTone(pa?.actual_trading?.avg_profit_rate)}
                />
                <Stat
                  label={<>
                    {language === "ko" ? "관망 종목" : "Watched Stocks"}
                    <Help text={language === "ko"
                      ? "분석 후 매수하지 않고 관망한 종목. 30일 추적 완료된 종목만 성과 분석에 포함."
                      : "Stocks analyzed but not purchased. Only 30-day completed stocks included in analysis."} />
                  </>}
                  value={`${pa?.overview.total || 0}${language === "ko" ? "건" : ""}`}
                  sub={`${t("insights.performance.completed")} ${pa?.overview.completed || 0}`}
                />
                <Stat
                  label={<>
                    {language === "ko" ? "관망 승률" : "Watched Win Rate"}
                    <Help text={language === "ko"
                      ? "관망 종목 중 30일 후 수익인 종목 비율 (30일 추적 완료 기준)"
                      : "Percentage of watched stocks profitable after 30 days"} />
                  </>}
                  value={(() => {
                    const triggers = pa?.trigger_performance || []
                    const totalWins = triggers.reduce((sum, t) => sum + ((t.win_rate_30d || 0) * t.count), 0)
                    const totalCount = triggers.reduce((sum, t) => sum + t.count, 0)
                    return totalCount > 0 ? `${((totalWins / totalCount) * 100).toFixed(0)}%` : "-"
                  })()}
                />
              </div>
            </CardContent>
          </Card>

          {/* Trading Wisdom Summary */}
          <Card className={CARD}>
            <CardHeader className="px-5 pt-4 pb-3">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm font-medium">{t("insights.category.wisdom")}</CardTitle>
                {marketFilter !== "all" && (
                  <Badge variant="outline" className={META_BADGE}>
                    {marketFilter} · {language === "ko" ? "필터 적용" : "Filtered"}
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="px-5 pb-5 pt-1">
              <div className="grid grid-cols-2 gap-x-4 gap-y-5">
                <Stat
                  label={t("insights.summary.totalPrinciples")}
                  value={`${filteredPrinciples.length}${language === "ko" ? "개" : ""}`}
                  sub={marketFilter !== "all" && filteredPrinciples.length !== data.principles.length
                    ? `/ ${data.principles.length}`
                    : undefined}
                />
                <Stat
                  label={t("insights.summary.highPriority")}
                  value={`${filteredPrinciples.filter(p => p.priority === "high").length}${language === "ko" ? "개" : ""}`}
                />
                <Stat
                  label={t("insights.summary.totalIntuitions")}
                  value={`${filteredIntuitions.length}${language === "ko" ? "개" : ""}`}
                  sub={marketFilter !== "all" && filteredIntuitions.length !== data.intuitions.length
                    ? `/ ${data.intuitions.length}`
                    : undefined}
                />
                <Stat
                  label={t("insights.summary.avgConfidence")}
                  value={`${filteredIntuitions.length > 0
                    ? ((filteredIntuitions.reduce((sum, i) => sum + i.confidence, 0) / filteredIntuitions.length) * 100).toFixed(0)
                    : 0}%`}
                />
              </div>
            </CardContent>
          </Card>
        </div>
      </TooltipProvider>

      {/* Performance Analysis Section */}
      <SectionHeading>{t("insights.category.performance")}</SectionHeading>

      {pa && (
        <Card className={CARD}>
          <CardHeader className="px-5 pt-4 pb-3">
            <p className="text-xs text-muted-foreground">{t("insights.performance.description")}</p>
          </CardHeader>
          <CardContent className="px-0 pb-5 space-y-7">
            {pa.overview.completed === 0 ? (
              <div className="px-5 py-10 text-center">
                <p className="text-sm text-muted-foreground">{t("insights.performance.noData")}</p>
                <p className="text-xs text-muted-foreground mt-1">{t("insights.performance.noDataHint")}</p>
              </div>
            ) : (
              <TooltipProvider>
                {/* Trigger Type Performance */}
                {pa.trigger_performance.length > 0 && (
                  <div className="space-y-2">
                    <SubHeading help={language === "ko"
                      ? "분석 후 관망한 종목들의 7/14/30일 가격 변화 추적 결과"
                      : "Price tracking results for stocks analyzed but not traded"}>
                      {language === "ko" ? "관망종목의 트리거 유형별 성과" : "Watched Stocks by Trigger Type"}
                    </SubHeading>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[560px] text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className={TH}>{language === "ko" ? "트리거" : "Trigger"}</th>
                            <th className={TH}>{t("insights.performance.count")}</th>
                            <th className={TH}>{t("insights.performance.day7")}</th>
                            <th className={TH}>{t("insights.performance.day14")}</th>
                            <th className={TH}>{t("insights.performance.day30")}</th>
                            <th className={TH}>
                              <div className="flex items-center justify-end gap-1">
                                {t("insights.performance.winRate")}
                                <Help text={language === "ko"
                                  ? "30일 후에도 수익인 종목의 비율"
                                  : "Percentage of stocks still profitable after 30 days"} />
                              </div>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {pa.trigger_performance.map((trigger, idx) => (
                            <tr key={idx} className={ROW}>
                              <td className={TD_FIRST}>{trigger.trigger_type}</td>
                              <td className={TD_NUM}>{trigger.count}</td>
                              <td className={`${TD_NUM} ${signTone(trigger.avg_7d_return)}`}>
                                {trigger.avg_7d_return !== null ? formatPercent(trigger.avg_7d_return) : "-"}
                              </td>
                              <td className={`${TD_NUM} ${signTone(trigger.avg_14d_return)}`}>
                                {trigger.avg_14d_return !== null ? formatPercent(trigger.avg_14d_return) : "-"}
                              </td>
                              <td className={`${TD_NUM} font-medium ${signTone(trigger.avg_30d_return)}`}>
                                {trigger.avg_30d_return !== null ? formatPercent(trigger.avg_30d_return) : "-"}
                              </td>
                              <td className={`${TD_NUM} ${trigger.win_rate_30d !== null && trigger.win_rate_30d !== undefined && trigger.win_rate_30d >= 0.5 ? "text-foreground font-medium" : "text-muted-foreground"}`}>
                                {trigger.win_rate_30d !== null && trigger.win_rate_30d !== undefined
                                  ? `${(trigger.win_rate_30d * 100).toFixed(0)}%`
                                  : "-"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Risk/Reward Threshold Analysis */}
                {pa.rr_threshold_analysis.length > 0 && (
                  <div className="space-y-2">
                    <SubHeading help={language === "ko"
                      ? "관망 종목의 손익비(목표가÷손절가) 구간별 30일 후 수익률. 30일 추적 완료된 종목 기준."
                      : "30-day returns of watched stocks by Risk/Reward ratio range."}>
                      {language === "ko" ? "관망종목의 손익비 구간별 분석" : "Watched Stocks by R/R Ratio"}
                    </SubHeading>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[560px] text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className={TH}>{t("insights.performance.range")}</th>
                            <th className={TH}>{t("insights.performance.count")}</th>
                            <th className={TH}>{t("insights.performance.traded")}</th>
                            <th className={TH}>{t("insights.performance.watched")}</th>
                            <th className={TH}>{t("insights.performance.allAvg")}</th>
                            <th className={TH}>{t("insights.performance.watchedAvg")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pa.rr_threshold_analysis.map((rr, idx) => (
                            <tr key={idx} className={ROW}>
                              <td className={`${TD_FIRST} font-mono tabular-nums text-[13px]`}>{rr.range}</td>
                              <td className={TD_NUM}>{rr.total_count}</td>
                              <td className={TD_NUM}>{rr.traded_count}</td>
                              <td className={TD_NUM}>{rr.watched_count}</td>
                              <td className={`${TD_NUM} ${signTone(rr.avg_all_return)}`}>
                                {rr.avg_all_return !== null ? formatPercent(rr.avg_all_return) : "-"}
                              </td>
                              <td className={`${TD_NUM} ${signTone(rr.avg_watched_return)}`}>
                                {rr.avg_watched_return !== null ? formatPercent(rr.avg_watched_return) : "-"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Actual Trading Performance */}
                {pa.actual_trading && pa.actual_trading.count > 0 && (
                  <div className="space-y-2">
                    <SubHeading help={language === "ko"
                      ? "최근 30일간 매도 완료된 거래 기준. 현재 보유중인 종목은 제외."
                      : "Based on trades sold in the last 30 days. Current holdings excluded."}>
                      {language === "ko" ? "실제 매매 성과 (최근 30일)" : "Actual Trading (Last 30 Days)"}
                      <span className="ml-1 text-xs font-normal text-muted-foreground font-mono tabular-nums">
                        {pa.actual_trading.count || 0}{language === "ko" ? "건 완료" : " trades"}
                      </span>
                    </SubHeading>
                    <div className="px-5">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-border border rounded-md overflow-hidden">
                        <MiniStat
                          label={language === "ko" ? "평균 수익률" : "Avg Return"}
                          value={formatPercent(pa.actual_trading.avg_profit_rate)}
                          className={signTone(pa.actual_trading.avg_profit_rate)}
                        />
                        <MiniStat
                          label={language === "ko" ? "승률" : "Win Rate"}
                          value={<>
                            {pa.actual_trading.win_rate !== null
                              ? `${(pa.actual_trading.win_rate * 100).toFixed(0)}%`
                              : "-"}
                            <span className="text-xs text-muted-foreground ml-1">
                              ({pa.actual_trading.win_count || 0}W/{pa.actual_trading.loss_count || 0}L)
                            </span>
                          </>}
                        />
                        <MiniStat
                          label={language === "ko" ? "평균 수익 (수익건)" : "Avg Profit (wins)"}
                          value={formatPercent(pa.actual_trading.avg_profit)}
                          className="text-success"
                        />
                        <MiniStat
                          label={language === "ko" ? "평균 손실 (손실건)" : "Avg Loss (losses)"}
                          value={formatPercent(pa.actual_trading.avg_loss)}
                          className="text-destructive"
                        />
                        <MiniStat
                          label={language === "ko" ? "최대 수익" : "Max Profit"}
                          value={formatPercent(pa.actual_trading.max_profit)}
                          className="text-success"
                        />
                        <MiniStat
                          label={language === "ko" ? "최대 손실" : "Max Loss"}
                          value={formatPercent(pa.actual_trading.max_loss)}
                          className="text-destructive"
                        />
                        <MiniStat
                          label={<>
                            Profit Factor
                            <Help text={language === "ko"
                              ? "총수익 ÷ 총손실. 1.0 이상이면 수익, 2.0 이상이면 우수"
                              : "Total Profit ÷ Total Loss. Above 1.0 is profitable, above 2.0 is excellent"} />
                          </>}
                          value={pa.actual_trading.profit_factor !== null
                            ? pa.actual_trading.profit_factor.toFixed(2)
                            : "-"}
                          className={pa.actual_trading.profit_factor !== null && pa.actual_trading.profit_factor >= 1
                            ? "text-success" : "text-destructive"}
                        />
                        <div className="bg-card" />
                      </div>
                    </div>
                  </div>
                )}

                {/* Actual Trading by Trigger Type (2026.01.12~) */}
                {pa.actual_trading_by_trigger && pa.actual_trading_by_trigger.length > 0 && (
                  <div className="space-y-2">
                    <SubHeading help={language === "ko"
                      ? "2026.01.12부터 트리거 유형 추적 시작. 어떤 트리거로 진입한 매매가 성과가 좋은지 비교."
                      : "Trigger type tracking started from 2026.01.12. Compare performance by entry trigger type."}>
                      {language === "ko" ? "실제 매매 종목의 트리거 유형별 성과 (2026.01.12~)" : "Actual Trading by Trigger Type (Since 2026.01.12)"}
                    </SubHeading>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[600px] text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className={TH}>{t("insights.performance.triggerType")}</th>
                            <th className={TH}>{t("insights.performance.count")}</th>
                            <th className={TH}>{language === "ko" ? "승률" : "Win Rate"}</th>
                            <th className={TH}>
                              <div className="flex items-center justify-end gap-1">
                                {language === "ko" ? "평균 수익" : "Avg Profit"}
                                <Help text={language === "ko" ? "수익 거래만의 평균" : "Average of winning trades only"} />
                              </div>
                            </th>
                            <th className={TH}>
                              <div className="flex items-center justify-end gap-1">
                                {language === "ko" ? "평균 손실" : "Avg Loss"}
                                <Help text={language === "ko" ? "손실 거래만의 평균" : "Average of losing trades only"} />
                              </div>
                            </th>
                            <th className={TH}>Profit Factor</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pa.actual_trading_by_trigger.map((item, idx) => (
                            <tr key={idx} className={ROW}>
                              <td className={TD_FIRST}>{item.trigger_type}</td>
                              <td className={TD_NUM}>
                                {item.count}
                                <span className="text-[11px] text-muted-foreground ml-1">
                                  ({item.win_count || 0}W/{item.loss_count || 0}L)
                                </span>
                              </td>
                              <td className={TD_NUM}>
                                {item.win_rate !== null ? `${(item.win_rate * 100).toFixed(0)}%` : "-"}
                              </td>
                              <td className={`${TD_NUM} ${item.avg_profit != null ? "text-success" : "text-muted-foreground"}`}>
                                {item.avg_profit !== null && item.avg_profit !== undefined
                                  ? formatPercent(item.avg_profit)
                                  : "-"}
                              </td>
                              <td className={`${TD_NUM} ${item.avg_loss != null ? "text-destructive" : "text-muted-foreground"}`}>
                                {item.avg_loss !== null && item.avg_loss !== undefined
                                  ? formatPercent(item.avg_loss)
                                  : "-"}
                              </td>
                              <td className={`${TD_NUM} ${
                                item.profit_factor === null ? "text-muted-foreground"
                                  : item.profit_factor >= 1 ? "text-success" : "text-destructive"
                              }`}>
                                {item.profit_factor !== null ? item.profit_factor.toFixed(2) : "-"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Missed Opportunities & Avoided Losses */}
                <div className="grid md:grid-cols-2 gap-7 md:gap-0 md:divide-x">
                  {[
                    {
                      key: "missed",
                      items: pa.missed_opportunities,
                      title: t("insights.performance.missedOpportunities"),
                      help: language === "ko"
                        ? "관망했지만 30일 후 +10% 이상 상승한 종목. 분석 시점 가격 기준."
                        : "Stocks skipped but rose +10%+ after 30 days from analysis price.",
                    },
                    {
                      key: "avoided",
                      items: pa.avoided_losses,
                      title: t("insights.performance.avoidedLosses"),
                      help: language === "ko"
                        ? "관망하여 30일 후 -10% 이상 하락을 피한 종목. 분석 시점 가격 기준."
                        : "Stocks skipped that fell -10%+ after 30 days. Avoided loss from analysis price.",
                    },
                  ].filter(group => group.items.length > 0).map(group => (
                    <div key={group.key} className="space-y-2 min-w-0">
                      <SubHeading help={group.help}>
                        {group.title}
                        <span className="text-xs font-normal text-muted-foreground font-mono tabular-nums">
                          {group.items.length}
                        </span>
                      </SubHeading>
                      <div className="max-h-[300px] overflow-y-auto border-t divide-y">
                        {group.items.map((opp, idx) => (
                          <div key={idx} className="px-5 py-2.5 hover:bg-muted/50 transition-colors">
                            <div className="flex items-baseline justify-between gap-3">
                              <div className="min-w-0 truncate">
                                <span className="font-medium text-sm">{opp.company_name}</span>
                                <span className="text-muted-foreground text-xs font-mono ml-1.5">{opp.ticker}</span>
                              </div>
                              <span className={`font-mono tabular-nums text-[13px] shrink-0 ${signTone(opp.tracked_30d_return)}`}>
                                {formatPercent(opp.tracked_30d_return)}
                              </span>
                            </div>
                            <div className="mt-1 text-[11px] text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5">
                              <span>{language === "ko" ? "분석일" : "Analyzed"}: <span className="font-mono">{opp.analyzed_date?.split(' ')[0] || '-'}</span></span>
                              <span>{language === "ko" ? "트리거" : "Trigger"}: {opp.trigger_type}</span>
                              <span>{language === "ko" ? "판정" : "Decision"}: {opp.decision || opp.skip_reason || '-'}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Recommendations */}
                {pa.recommendations.length > 0 && (
                  <div className="space-y-2">
                    <SubHeading>{t("insights.performance.recommendations")}</SubHeading>
                    <ul className="px-5 space-y-1.5">
                      {pa.recommendations.map((rec, idx) => (
                        <li key={idx} className="flex items-start gap-2 text-sm">
                          <span className="text-muted-foreground mt-0.5">•</span>
                          <span>{rec}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </TooltipProvider>
            )}
          </CardContent>
        </Card>
      )}

      {/* Trading Wisdom Section */}
      <SectionHeading>{t("insights.category.wisdom")}</SectionHeading>

      {/* Principles Section */}
      <Card className={CARD}>
        <CardHeader className="px-5 pt-4 pb-3">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm font-medium">{t("insights.principles")}</CardTitle>
            <span className="text-xs text-muted-foreground font-mono tabular-nums">{filteredPrinciples.length}</span>
            {marketFilter !== "all" && marketBadge(marketFilter)}
          </div>
          <p className="text-xs text-muted-foreground">{t("insights.principlesDescription")}</p>
        </CardHeader>
        <CardContent className="px-0 pb-1">
          {filteredPrinciples.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">{t("insights.noPrinciples")}</p>
          ) : (
            <div className="border-t divide-y">
              {filteredPrinciples.map((principle) => (
                <div key={principle.id} className="px-5 py-4 hover:bg-muted/50 transition-colors">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {principle.market && marketBadge(principle.market)}
                        <Badge variant="outline" className={`text-[10px] font-normal ${getPriorityColor(principle.priority)}`}>
                          {t(`insights.priority.${principle.priority}`)}
                        </Badge>
                        <Badge variant="outline" className={META_BADGE}>
                          {t(`insights.scope.${principle.scope}`)}
                          {principle.scope_context && `: ${principle.scope_context}`}
                        </Badge>
                      </div>
                      <div className="space-y-1 text-sm">
                        <p>
                          <span className="text-muted-foreground">{t("insights.condition")}:</span>{" "}
                          {principle.condition}
                        </p>
                        <p className="font-medium">
                          <span className="text-muted-foreground font-normal">{t("insights.action")}:</span>{" "}
                          {principle.action}
                        </p>
                        {principle.reason && (
                          <p className="text-xs text-muted-foreground">
                            <span>{t("insights.reason")}:</span> {principle.reason}
                          </p>
                        )}
                      </div>
                    </div>
                    <div className="space-y-1 sm:w-[140px] shrink-0">
                      <div className="text-xs text-muted-foreground">{t("insights.confidence")}</div>
                      {getConfidenceBar(principle.confidence)}
                      <div className="text-[11px] text-muted-foreground">
                        {t("insights.supportingTrades")}: <span className="font-mono tabular-nums">{principle.supporting_trades}</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Journal Section - Shared across markets */}
      <Card className={CARD}>
        <CardHeader className="px-5 pt-4 pb-3">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm font-medium">{t("insights.journal")}</CardTitle>
            <Badge variant="outline" className={META_BADGE}>
              {language === "ko" ? "KR+US 공통" : "All Markets"}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">{t("insights.journalDescription")}</p>
        </CardHeader>
        <CardContent className="px-0 pb-1">
          {journalEntries.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">{t("insights.noJournal")}</p>
          ) : (
            <Accordion type="single" collapsible className="w-full border-t">
              {journalEntries.map((entry) => (
                <AccordionItem key={entry.id} value={`journal-${entry.id}`} className="px-5">
                  <AccordionTrigger className="hover:no-underline py-3 min-w-0">
                    <div className="flex items-center justify-between gap-3 w-full min-w-0 pr-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {entry.market && marketBadge(entry.market)}
                        <span className="font-medium text-sm truncate">{entry.company_name}</span>
                        <span className="text-muted-foreground text-xs font-mono hidden sm:inline">{entry.ticker}</span>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span className={`font-mono tabular-nums text-[13px] ${signTone(entry.profit_rate)}`}>
                          {formatPercentDirect(entry.profit_rate)}
                        </span>
                        <span className="text-xs text-muted-foreground font-mono tabular-nums hidden sm:inline">
                          {formatDate(entry.trade_date)}
                        </span>
                        <Badge variant="outline" className={`${META_BADGE} font-mono`}>
                          L{entry.compression_layer}
                        </Badge>
                      </div>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="space-y-5 pt-1">
                      {/* One-line Summary */}
                      {entry.one_line_summary && (
                        <p className="text-sm font-medium border-l-2 border-border pl-3">{entry.one_line_summary}</p>
                      )}

                      {/* Trade Details */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div>
                          <span className="text-xs text-muted-foreground">{t("insights.tradeDate")}</span>
                          <p className="font-mono tabular-nums text-[13px]">{formatDate(entry.trade_date)}</p>
                        </div>
                        <div>
                          <span className="text-xs text-muted-foreground">{t("insights.holdingDays")}</span>
                          <p className="font-mono tabular-nums text-[13px]">{entry.holding_days}{language === "ko" ? "일" : " days"}</p>
                        </div>
                        <div>
                          <span className="text-xs text-muted-foreground">{t("insights.profitRate")}</span>
                          <p className={`font-mono tabular-nums text-[13px] ${signTone(entry.profit_rate)}`}>
                            {formatPercentDirect(entry.profit_rate)}
                          </p>
                        </div>
                        <div>
                          <span className="text-xs text-muted-foreground">{t("insights.layer")}</span>
                          <p className="font-mono tabular-nums text-[13px]">Layer {entry.compression_layer}</p>
                        </div>
                      </div>

                      {/* Situation Analysis */}
                      {entry.situation_analysis && (() => {
                        const parsed = tryParseJSON<SituationAnalysis>(entry.situation_analysis)
                        if (!parsed) return (
                          <div>
                            <h4 className="text-xs font-medium text-muted-foreground mb-1">
                              {t("insights.situationAnalysis")}
                            </h4>
                            <p className="text-sm">{entry.situation_analysis}</p>
                          </div>
                        )
                        return (
                          <div className="space-y-2">
                            <h4 className="text-xs font-medium text-muted-foreground">
                              {t("insights.situationAnalysis")}
                            </h4>
                            <div className="grid gap-2 text-sm">
                              {parsed.buy_context_summary && (
                                <div className="p-3 rounded-md border">
                                  <span className="text-xs font-medium">
                                    {language === "ko" ? "매수 컨텍스트" : "Buy Context"}
                                  </span>
                                  <p className="mt-1 text-muted-foreground">{parsed.buy_context_summary}</p>
                                </div>
                              )}
                              {parsed.sell_context_summary && (
                                <div className="p-3 rounded-md border">
                                  <span className="text-xs font-medium">
                                    {language === "ko" ? "매도 컨텍스트" : "Sell Context"}
                                  </span>
                                  <p className="mt-1 text-muted-foreground">{parsed.sell_context_summary}</p>
                                </div>
                              )}
                              {(parsed.market_at_buy || parsed.market_at_sell) && (
                                <div className="grid md:grid-cols-2 gap-2">
                                  {parsed.market_at_buy && (
                                    <div className="p-3 rounded-md bg-muted/40">
                                      <span className="text-xs text-muted-foreground">{language === "ko" ? "매수시점 시장" : "Market at Buy"}</span>
                                      <p className="text-sm mt-0.5">{parsed.market_at_buy}</p>
                                    </div>
                                  )}
                                  {parsed.market_at_sell && (
                                    <div className="p-3 rounded-md bg-muted/40">
                                      <span className="text-xs text-muted-foreground">{language === "ko" ? "매도시점 시장" : "Market at Sell"}</span>
                                      <p className="text-sm mt-0.5">{parsed.market_at_sell}</p>
                                    </div>
                                  )}
                                </div>
                              )}
                              {parsed.key_changes && parsed.key_changes.length > 0 && (
                                <div className="p-3 rounded-md border">
                                  <span className="text-xs font-medium">
                                    {language === "ko" ? "핵심 변화" : "Key Changes"}
                                  </span>
                                  <ul className="mt-1.5 space-y-1 text-muted-foreground">
                                    {parsed.key_changes.map((change, i) => (
                                      <li key={i} className="flex items-start gap-2">
                                        <span className="mt-0.5">•</span>
                                        <span>{change}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })()}

                      {/* Judgment Evaluation */}
                      {entry.judgment_evaluation && (() => {
                        const parsed = tryParseJSON<JudgmentEvaluation>(entry.judgment_evaluation)
                        if (!parsed) return (
                          <div>
                            <h4 className="text-xs font-medium text-muted-foreground mb-1">
                              {t("insights.judgmentEvaluation")}
                            </h4>
                            <p className="text-sm">{entry.judgment_evaluation}</p>
                          </div>
                        )
                        const qualityClass = (q: string) =>
                          q === "적절" || q === "Good" ? "text-success" : "text-amber-600 dark:text-amber-400"
                        return (
                          <div className="space-y-2">
                            <h4 className="text-xs font-medium text-muted-foreground">
                              {t("insights.judgmentEvaluation")}
                            </h4>
                            <div className="grid md:grid-cols-2 gap-2 text-sm">
                              {parsed.buy_quality && (
                                <div className="p-3 rounded-md border">
                                  <Badge variant="outline" className={`text-[10px] font-normal ${qualityClass(parsed.buy_quality)}`}>
                                    {language === "ko" ? "매수" : "Buy"}: {parsed.buy_quality}
                                  </Badge>
                                  {parsed.buy_quality_reason && (
                                    <p className="text-muted-foreground text-xs mt-2">{parsed.buy_quality_reason}</p>
                                  )}
                                </div>
                              )}
                              {parsed.sell_quality && (
                                <div className="p-3 rounded-md border">
                                  <Badge variant="outline" className={`text-[10px] font-normal ${qualityClass(parsed.sell_quality)}`}>
                                    {language === "ko" ? "매도" : "Sell"}: {parsed.sell_quality}
                                  </Badge>
                                  {parsed.sell_quality_reason && (
                                    <p className="text-muted-foreground text-xs mt-2">{parsed.sell_quality_reason}</p>
                                  )}
                                </div>
                              )}
                            </div>
                            {[
                              { key: "missed", items: parsed.missed_signals, label: language === "ko" ? "놓친 신호" : "Missed Signals" },
                              { key: "over", items: parsed.overreacted_signals, label: language === "ko" ? "과잉 반응 신호" : "Overreacted Signals" },
                            ].filter(g => g.items && g.items.length > 0).map(g => (
                              <div key={g.key} className="p-3 rounded-md border">
                                <span className="text-xs font-medium">{g.label}</span>
                                <ul className="mt-1.5 space-y-1 text-xs text-muted-foreground">
                                  {g.items!.map((signal, i) => (
                                    <li key={i} className="flex items-start gap-2">
                                      <span>•</span>
                                      <span>{signal}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            ))}
                          </div>
                        )
                      })()}

                      {/* Lessons */}
                      {entry.lessons && entry.lessons.length > 0 && (
                        <div className="space-y-2">
                          <h4 className="text-xs font-medium text-muted-foreground">
                            {t("insights.lessons")}
                          </h4>
                          <div className="space-y-2">
                            {entry.lessons.map((lesson, idx) => {
                              // L2 압축 데이터 호환: 문자열이면 객체로 변환, priority 기본값 'medium'
                              const normalizedLesson = typeof lesson === 'string'
                                ? { condition: '', action: lesson, reason: '', priority: 'medium' as const }
                                : lesson
                              const priority = normalizedLesson.priority || 'medium'

                              return (
                                <div key={idx} className="p-3 rounded-md border space-y-2 text-sm">
                                  <Badge variant="outline" className={`text-[10px] font-normal ${getPriorityColor(priority)}`}>
                                    {t(`insights.priority.${priority}`)}
                                  </Badge>
                                  {normalizedLesson.condition && (
                                    <div>
                                      <span className="text-xs text-muted-foreground">
                                        {language === "ko" ? "조건" : "Condition"}
                                      </span>
                                      <p className="mt-0.5">{normalizedLesson.condition}</p>
                                    </div>
                                  )}
                                  <div>
                                    <span className="text-xs text-muted-foreground">
                                      {language === "ko" ? "행동" : "Action"}
                                    </span>
                                    <p className="mt-0.5 font-medium">{normalizedLesson.action}</p>
                                  </div>
                                  {normalizedLesson.reason && (
                                    <div>
                                      <span className="text-xs text-muted-foreground">
                                        {language === "ko" ? "이유" : "Reason"}
                                      </span>
                                      <p className="mt-0.5 text-muted-foreground text-xs">{normalizedLesson.reason}</p>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {/* Pattern Tags */}
                      {entry.pattern_tags && entry.pattern_tags.length > 0 && (
                        <div>
                          <h4 className="text-xs font-medium text-muted-foreground mb-2">
                            {t("insights.patternTags")}
                          </h4>
                          <div className="flex flex-wrap gap-1.5">
                            {entry.pattern_tags.map((tag, idx) => (
                              <Badge key={idx} variant="outline" className={META_BADGE}>
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          )}
        </CardContent>
      </Card>

      {/* Intuitions Section */}
      <Card className={CARD}>
        <CardHeader className="px-5 pt-4 pb-3">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm font-medium">{t("insights.intuitions")}</CardTitle>
            <span className="text-xs text-muted-foreground font-mono tabular-nums">{filteredIntuitions.length}</span>
            {marketFilter !== "all" && marketBadge(marketFilter)}
          </div>
          <p className="text-xs text-muted-foreground">{t("insights.intuitionsDescription")}</p>
        </CardHeader>
        <CardContent className="px-0 pb-1">
          {filteredIntuitions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">{t("insights.noIntuitions")}</p>
          ) : (
            <div className="border-t divide-y">
              {filteredIntuitions.map((intuition) => (
                <div key={intuition.id} className="px-5 py-4 hover:bg-muted/50 transition-colors">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {intuition.market && marketBadge(intuition.market)}
                        <Badge variant="outline" className={META_BADGE}>
                          {intuition.category}
                        </Badge>
                        {intuition.subcategory && (
                          <Badge variant="outline" className={META_BADGE}>
                            {intuition.subcategory}
                          </Badge>
                        )}
                      </div>
                      <div className="space-y-1 text-sm">
                        <p>
                          <span className="text-muted-foreground">{t("insights.condition")}:</span>{" "}
                          {intuition.condition}
                        </p>
                        <p className="font-medium">
                          <span className="text-muted-foreground font-normal">{t("insights.insight")}:</span>{" "}
                          {intuition.insight}
                        </p>
                      </div>
                    </div>
                    <div className="space-y-1 sm:w-[160px] shrink-0">
                      <div className="text-xs text-muted-foreground">{t("insights.confidence")}</div>
                      {getConfidenceBar(intuition.confidence)}
                      <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                        <span>{t("insights.successRate")}: <span className="font-mono tabular-nums">{(intuition.success_rate * 100).toFixed(0)}%</span></span>
                        <span>{t("insights.supportingTrades")}: <span className="font-mono tabular-nums">{intuition.supporting_trades}</span></span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
