"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { HelpCircle } from "lucide-react"
import type { Trade, Summary, PrismPerformance, MarketCondition, Market } from "@/types/dashboard"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil } from "@/lib/currency"

interface TradingHistoryPageProps {
  history: Trade[]
  summary: Summary
  prismPerformance?: PrismPerformance[]
  marketCondition?: MarketCondition[]
  market?: Market
}

export function TradingHistoryPage({ history, summary, prismPerformance = [], marketCondition = [], market = "KR" }: TradingHistoryPageProps) {
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
      day: "numeric"
    })
  }

  const { total_trades, win_rate, avg_profit_rate, avg_holding_days } = summary.trading

  // 최고/최저 수익 종목
  const bestTrade = history.reduce((best, trade) => 
    (trade.profit_rate > best.profit_rate) ? trade : best
  , history[0] || { profit_rate: 0 })
  
  const worstTrade = history.reduce((worst, trade) => 
    (trade.profit_rate < worst.profit_rate) ? trade : worst
  , history[0] || { profit_rate: 0 })

  // 섹터별 수익률 (scenario.sector 활용)
  const sectorPerformance = history.reduce((acc, trade) => {
    const sector = trade.scenario?.sector || t("common.other")
    if (!acc[sector]) {
      acc[sector] = { total: 0, count: 0, avgProfit: 0 }
    }
    acc[sector].total += trade.profit_rate
    acc[sector].count += 1
    acc[sector].avgProfit = acc[sector].total / acc[sector].count
    return acc
  }, {} as Record<string, { total: number; count: number; avgProfit: number }>)

  const sortedSectors = Object.entries(sectorPerformance)
    .sort(([, a], [, b]) => b.avgProfit - a.avgProfit)
    .slice(0, 3)

  // 투자기간별 수익률 (수익/손실 분리)
  const periodPerformance = history.reduce((acc, trade) => {
    const period = trade.scenario?.investment_period || t("common.unclassified")
    if (!acc[period]) {
      acc[period] = {
        total: 0, count: 0, avgProfit: 0,
        winTotal: 0, winCount: 0, avgWin: 0,
        lossTotal: 0, lossCount: 0, avgLoss: 0
      }
    }
    acc[period].total += trade.profit_rate
    acc[period].count += 1
    acc[period].avgProfit = acc[period].total / acc[period].count

    if (trade.profit_rate >= 0) {
      acc[period].winTotal += trade.profit_rate
      acc[period].winCount += 1
      acc[period].avgWin = acc[period].winTotal / acc[period].winCount
    } else {
      acc[period].lossTotal += trade.profit_rate
      acc[period].lossCount += 1
      acc[period].avgLoss = acc[period].lossTotal / acc[period].lossCount
    }
    return acc
  }, {} as Record<string, {
    total: number; count: number; avgProfit: number;
    winTotal: number; winCount: number; avgWin: number;
    lossTotal: number; lossCount: number; avgLoss: number;
  }>)

  // 수익 거래와 손실 거래 분리
  const winningTrades = history.filter(t => t.profit_rate >= 0)
  const losingTrades = history.filter(t => t.profit_rate < 0)

  // 평균 수익률 (수익 거래만)
  const avgWinRate = winningTrades.length > 0
    ? winningTrades.reduce((sum, t) => sum + t.profit_rate, 0) / winningTrades.length
    : 0

  // 평균 손실률 (손실 거래만)
  const avgLossRate = losingTrades.length > 0
    ? losingTrades.reduce((sum, t) => sum + t.profit_rate, 0) / losingTrades.length
    : 0

  // Profit Factor (총 수익 / 총 손실의 절대값)
  const totalProfit = winningTrades.reduce((sum, t) => sum + t.profit_rate, 0)
  const totalLoss = Math.abs(losingTrades.reduce((sum, t) => sum + t.profit_rate, 0))
  const profitFactor = totalLoss > 0 ? totalProfit / totalLoss : totalProfit > 0 ? Infinity : 0

  // 손익비 (Risk/Reward Ratio) - 평균 수익 / 평균 손실의 절대값
  const riskRewardRatio = Math.abs(avgLossRate) > 0 ? avgWinRate / Math.abs(avgLossRate) : avgWinRate > 0 ? Infinity : 0

  // 최대 연속 승/패 계산
  let maxConsecutiveWins = 0
  let maxConsecutiveLosses = 0
  let currentWinStreak = 0
  let currentLossStreak = 0

  // 날짜순 정렬
  const sortedHistory = [...history].sort((a, b) =>
    new Date(a.sell_date).getTime() - new Date(b.sell_date).getTime()
  )

  sortedHistory.forEach(trade => {
    if (trade.profit_rate >= 0) {
      currentWinStreak++
      currentLossStreak = 0
      maxConsecutiveWins = Math.max(maxConsecutiveWins, currentWinStreak)
    } else {
      currentLossStreak++
      currentWinStreak = 0
      maxConsecutiveLosses = Math.max(maxConsecutiveLosses, currentLossStreak)
    }
  })

  // MDD (Maximum Drawdown) 계산
  const calculateMDD = (performances: PrismPerformance[]): number => {
    if (performances.length === 0) return 0

    let maxReturn = -Infinity
    let maxDrawdown = 0

    for (const perf of performances) {
      const currentReturn = perf.prism_simulator_return
      if (currentReturn > maxReturn) {
        maxReturn = currentReturn
      }
      const drawdown = maxReturn - currentReturn
      if (drawdown > maxDrawdown) {
        maxDrawdown = drawdown
      }
    }

    return maxDrawdown
  }

  const prismMDD = calculateMDD(prismPerformance)

  // 알파, 베타, 샤프 비율 계산
  const calculateRiskMetrics = () => {
    if (prismPerformance.length < 2 || marketCondition.length < 2) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // Season2 시작일 기준 필터링
    const season2StartDate = '2025-09-29'
    const filteredMarket = marketCondition
      .filter(m => m.date >= season2StartDate)
      .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())

    if (filteredMarket.length < 2) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 시작 시점 KOSPI 지수
    const startKospi = filteredMarket[0]?.kospi_index || 0
    if (startKospi === 0) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 프리즘 누적 수익률 맵핑
    const prismMap = new Map<string, number>()
    prismPerformance.forEach(p => {
      prismMap.set(p.date, p.prism_simulator_return)
    })

    // 일별 수익률 계산
    // 프리즘: 누적 수익률의 차이 (이미 % 단위)
    // 시장: KOSPI 지수의 일별 변화율 (% 단위)
    const prismDailyReturns: number[] = []
    const marketDailyReturns: number[] = []

    for (let i = 1; i < filteredMarket.length; i++) {
      const prevDate = filteredMarket[i - 1].date
      const currDate = filteredMarket[i].date

      // 프리즘: 누적 수익률의 일별 변화
      const prevPrism = prismMap.get(prevDate) || 0
      const currPrism = prismMap.get(currDate) || 0
      const prismDailyReturn = currPrism - prevPrism // 이미 % 단위

      // 시장: KOSPI 일별 수익률
      const prevKospi = filteredMarket[i - 1].kospi_index
      const currKospi = filteredMarket[i].kospi_index
      const marketDailyReturn = prevKospi > 0 ? ((currKospi - prevKospi) / prevKospi) * 100 : 0

      prismDailyReturns.push(prismDailyReturn)
      marketDailyReturns.push(marketDailyReturn)
    }

    if (prismDailyReturns.length === 0) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 누적 수익률 계산 (최종 값)
    const latestPrismReturn = prismPerformance[prismPerformance.length - 1]?.prism_simulator_return || 0
    const latestKospi = filteredMarket[filteredMarket.length - 1]?.kospi_index || startKospi
    const totalMarketReturn = ((latestKospi - startKospi) / startKospi) * 100

    // ============================================
    // 알파 (Alpha) - 단순 초과수익률
    // ============================================
    // 가장 직관적인 알파: 포트폴리오 수익률 - 벤치마크 수익률
    const alpha = latestPrismReturn - totalMarketReturn

    // ============================================
    // 베타 (Beta) - 시장 민감도
    // ============================================
    // 일별 수익률 기반 계산
    const avgPrismDaily = prismDailyReturns.reduce((a, b) => a + b, 0) / prismDailyReturns.length
    const avgMarketDaily = marketDailyReturns.reduce((a, b) => a + b, 0) / marketDailyReturns.length

    let covariance = 0
    let marketVariance = 0
    let prismVariance = 0

    for (let i = 0; i < prismDailyReturns.length; i++) {
      const prismDiff = prismDailyReturns[i] - avgPrismDaily
      const marketDiff = marketDailyReturns[i] - avgMarketDaily
      covariance += prismDiff * marketDiff
      marketVariance += marketDiff * marketDiff
      prismVariance += prismDiff * prismDiff
    }

    covariance /= prismDailyReturns.length
    marketVariance /= prismDailyReturns.length
    prismVariance /= prismDailyReturns.length

    // 베타 = Cov(포트폴리오, 시장) / Var(시장)
    const beta = marketVariance > 0 ? covariance / marketVariance : 0

    // ============================================
    // 샤프 비율 (Sharpe Ratio)
    // ============================================
    // 무위험 수익률: 연 3% 가정
    const annualRiskFreeRate = 3 // %
    const tradingDays = prismDailyReturns.length
    const periodRiskFreeRate = (tradingDays / 252) * annualRiskFreeRate // 기간 비례 무위험수익률

    // 프리즘 표준편차 (일별 수익률 기준)
    const prismStdDev = Math.sqrt(prismVariance)
    // 연환산 표준편차
    const annualizedPrismStdDev = prismStdDev * Math.sqrt(252)

    // 샤프 비율 = (연환산 수익률 - 연환산 무위험수익률) / 연환산 표준편차
    // 기간 수익률을 연환산
    const annualizedPrismReturn = (latestPrismReturn / tradingDays) * 252
    const sharpeRatio = annualizedPrismStdDev > 0
      ? (annualizedPrismReturn - annualRiskFreeRate) / annualizedPrismStdDev
      : 0

    // ============================================
    // 정보 비율 (Information Ratio)
    // ============================================
    // 추적 오차: 포트폴리오 수익률 - 벤치마크 수익률의 표준편차
    const trackingErrors: number[] = []
    for (let i = 0; i < prismDailyReturns.length; i++) {
      trackingErrors.push(prismDailyReturns[i] - marketDailyReturns[i])
    }

    const avgTrackingError = trackingErrors.reduce((a, b) => a + b, 0) / trackingErrors.length
    let trackingErrorVariance = 0
    for (const te of trackingErrors) {
      trackingErrorVariance += (te - avgTrackingError) ** 2
    }
    trackingErrorVariance /= trackingErrors.length
    const trackingErrorStdDev = Math.sqrt(trackingErrorVariance)

    // 정보 비율 = 연환산 초과수익률 / 연환산 추적오차
    const annualizedAlpha = (alpha / tradingDays) * 252
    const annualizedTrackingError = trackingErrorStdDev * Math.sqrt(252)
    const informationRatio = annualizedTrackingError > 0
      ? annualizedAlpha / annualizedTrackingError
      : 0

    // 데이터 기간 계산
    const startDate = filteredMarket[0]?.date || ''
    const endDate = filteredMarket[filteredMarket.length - 1]?.date || ''
    const dataPoints = filteredMarket.length

    return { alpha, beta, sharpeRatio, informationRatio, startDate, endDate, dataPoints }
  }

  const { alpha, beta, sharpeRatio, informationRatio, startDate: riskStartDate, endDate: riskEndDate, dataPoints } = calculateRiskMetrics()

  const signTone = (v: number) => (v >= 0 ? "text-success" : "text-destructive")
  const AMBER = "text-amber-600 dark:text-amber-400"
  // good / warning / bad thresholds keep their original meaning; only the palette changed
  const tierTone = (good: boolean, ok: boolean) => (good ? "text-success" : ok ? AMBER : "text-destructive")

  type HelpLine = [string, string]
  const kpi = (label: string, value: string, valueClass: string, sub: string, help?: { text: string; lines: HelpLine[] }) => (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      <CardContent className="p-4 sm:p-5 space-y-2.5">
        <div className="flex items-center gap-1.5">
          <span className="text-[13px] text-muted-foreground">{label}</span>
          {help && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" aria-label={label} className="text-muted-foreground/60 hover:text-muted-foreground cursor-help">
                  <HelpCircle className="w-3.5 h-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs p-3">
                <p className="font-medium mb-1">{label}</p>
                <p className="text-xs mb-2">{help.text}</p>
                <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                  {help.lines.map(([cls, text]) => (
                    <p key={text} className={cls}>{text}</p>
                  ))}
                </div>
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        <p className={`font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums ${valueClass}`}>{value}</p>
        <p className="text-xs text-muted-foreground">{sub}</p>
      </CardContent>
    </Card>
  )

  const cardHeader = (title: string, meta?: string) => (
    <CardHeader className="px-5 pt-4 pb-3">
      <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {meta && <span className="text-xs text-muted-foreground font-mono tabular-nums">{meta}</span>}
      </div>
    </CardHeader>
  )

  const textBlock = (label: string, text: string) => (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-1">{label}</p>
      <p className="text-sm text-foreground leading-relaxed break-words">{text}</p>
    </div>
  )

  const tradeExtreme = (title: string, trade: Trade) => (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      {cardHeader(title)}
      <CardContent className="px-5 pb-4 space-y-3">
        <div className="flex items-baseline gap-2">
          <p className="font-medium text-foreground">{trade.company_name}</p>
          <p className="text-xs text-muted-foreground font-mono">{trade.ticker}</p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t("trading.profitRate")}</p>
            <p className={`font-serif text-2xl leading-none tracking-tight tabular-nums ${signTone(trade.profit_rate)}`}>{formatPercent(trade.profit_rate)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t("trading.holdingPeriod")}</p>
            <p className="font-serif text-2xl leading-none tracking-tight tabular-nums text-foreground">{trade.holding_days}{t("common.days")}</p>
          </div>
        </div>
        <p className="pt-2 border-t border-border text-xs text-muted-foreground tabular-nums">
          {formatDate(trade.buy_date)} → {formatDate(trade.sell_date)}
        </p>
      </CardContent>
    </Card>
  )

  const levelLabels: Array<[string, string]> = [
    ["primary_support", t("modal.primarySupportShort")],
    ["secondary_support", t("modal.secondarySupportShort")],
    ["primary_resistance", t("modal.primaryResistanceShort")],
    ["secondary_resistance", t("modal.secondaryResistanceShort")],
  ]

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-foreground">{t("trading.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("trading.description")}</p>
        </div>
        <span className="text-xs text-muted-foreground font-mono tabular-nums">
          {t("trading.totalTrades")} {total_trades || 0}{t("trading.tradeCount")}
        </span>
      </div>

      {/* 통계 카드 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {kpi(t("trading.totalTrades"), `${total_trades || 0}${t("trading.times")}`, "text-foreground", t("trading.completedTrades"))}
        {kpi(t("trading.winRate"), `${(win_rate || 0).toFixed(0)}%`, "text-foreground", t("trading.winningTradeRatio"))}
        {kpi(t("trading.avgWinRate"), formatPercent(avgWinRate), "text-success", `${winningTrades.length}${t("trading.winningTradesAvg")}`)}
        {kpi(t("trading.avgLossRate"), formatPercent(avgLossRate), "text-destructive", `${losingTrades.length}${t("trading.losingTradesAvg")}`)}
      </div>

      {/* 성과 지표 카드 */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {kpi(
          t("trading.profitFactor"),
          profitFactor === Infinity ? "∞" : profitFactor.toFixed(2),
          tierTone(profitFactor >= 1.5, profitFactor >= 1),
          t("trading.profitFactorDesc"),
          { text: t("trading.profitFactorTooltip"), lines: [["text-success", t("trading.profitFactorGood")], [AMBER, t("trading.profitFactorNeutral")], ["text-destructive", t("trading.profitFactorBad")]] },
        )}
        {kpi(
          t("trading.riskRewardRatio"),
          riskRewardRatio === Infinity ? "∞" : riskRewardRatio.toFixed(2),
          tierTone(riskRewardRatio >= 2, riskRewardRatio >= 1),
          t("trading.riskRewardDesc"),
          { text: t("trading.riskRewardTooltip"), lines: [["text-success", t("trading.riskRewardGood")], [AMBER, t("trading.riskRewardNeutral")], ["text-destructive", t("trading.riskRewardBad")]] },
        )}
        {kpi(
          t("trading.mdd"),
          prismMDD > 0 ? `-${prismMDD.toFixed(2)}%` : "0%",
          tierTone(prismMDD <= 10, prismMDD <= 20),
          t("trading.mddDesc"),
          { text: t("trading.mddTooltip"), lines: [["text-success", t("trading.mddGood")], [AMBER, t("trading.mddNeutral")], ["text-destructive", t("trading.mddBad")]] },
        )}
        {kpi(t("trading.maxWinStreak"), `${maxConsecutiveWins}${t("trading.consecutive")}`, "text-foreground", t("trading.maxWinStreakDesc"))}
        {kpi(t("trading.avgHoldingDays"), `${(avg_holding_days || 0).toFixed(0)}${t("common.days")}`, "text-foreground", t("trading.daysFromBuyToSell"))}
      </div>

      {/* 위험조정 성과 지표 (알파, 베타, 샤프비율, 정보비율) */}
      {(prismPerformance.length > 0 && marketCondition.length > 0 && dataPoints > 0) && (
        <section className="space-y-2.5">
          <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
            <h3 className="text-sm font-medium text-foreground">{t("trading.riskAdjustedMetrics")}</h3>
            {riskStartDate && riskEndDate && (
              <span className="text-xs text-muted-foreground font-mono tabular-nums">
                {t("trading.dataPeriod")}: {riskStartDate} ~ {riskEndDate} ({dataPoints}{t("common.days")})
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {kpi(
              t("trading.alpha"),
              `${alpha >= 0 ? "+" : ""}${alpha.toFixed(2)}%`,
              signTone(alpha),
              t("trading.alphaDesc"),
              { text: t("trading.alphaTooltip"), lines: [["text-success", t("trading.alphaGood")], ["text-destructive", t("trading.alphaBad")]] },
            )}
            {kpi(
              t("trading.beta"),
              beta.toFixed(2),
              "text-foreground",
              t("trading.betaDesc"),
              { text: t("trading.betaTooltip"), lines: [["", t("trading.betaLow")], ["", t("trading.betaNeutral")], ["", t("trading.betaHigh")]] },
            )}
            {kpi(
              t("trading.sharpeRatio"),
              sharpeRatio.toFixed(2),
              sharpeRatio >= 1 ? "text-success" : sharpeRatio >= 0 ? "text-foreground" : "text-destructive",
              t("trading.sharpeDesc"),
              { text: t("trading.sharpeTooltip"), lines: [["text-success", t("trading.sharpeGood")], ["", t("trading.sharpeNeutral")], ["text-destructive", t("trading.sharpeBad")]] },
            )}
            {kpi(
              t("trading.informationRatio"),
              informationRatio.toFixed(2),
              informationRatio >= 0.5 ? "text-success" : informationRatio >= 0 ? "text-foreground" : "text-destructive",
              t("trading.informationRatioDesc"),
              { text: t("trading.irTooltip"), lines: [["text-success", t("trading.irGood")], ["", t("trading.irNeutral")], ["text-destructive", t("trading.irBad")]] },
            )}
          </div>
        </section>
      )}

      {/* 베스트/워스트 거래 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {tradeExtreme(t("trading.bestTrade"), bestTrade as Trade)}
          {tradeExtreme(t("trading.worstTrade"), worstTrade as Trade)}
        </div>
      )}

      {/* 섹터별 & 기간별 성과 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {sortedSectors.length > 0 && (
            <Card className="gap-0 py-0 rounded-lg shadow-none">
              {cardHeader(t("trading.sectorPerformanceTop3"))}
              <CardContent className="px-0 pb-1">
                <div className="divide-y divide-border border-t border-border">
                  {sortedSectors.map(([sector, data], index) => (
                    <div key={sector} className="flex items-center justify-between gap-3 px-5 py-3">
                      <div className="flex items-baseline gap-3 min-w-0">
                        <span className="text-xs text-muted-foreground font-mono tabular-nums">{index + 1}</span>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">{sector}</p>
                          <p className="text-xs text-muted-foreground">{data.count}{t("trading.tradeCount")}</p>
                        </div>
                      </div>
                      <p className={`font-mono tabular-nums text-[13px] ${signTone(data.avgProfit)}`}>{formatPercent(data.avgProfit)}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {Object.keys(periodPerformance).length > 0 && (
            <Card className="gap-0 py-0 rounded-lg shadow-none">
              {cardHeader(t("trading.periodPerformance"))}
              <CardContent className="px-0 pb-1">
                <div className="divide-y divide-border border-t border-border">
                  {Object.entries(periodPerformance).map(([period, data]) => (
                    <div key={period} className="px-5 py-3 space-y-1.5">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">{period}</p>
                          <p className="text-xs text-muted-foreground">{data.count}{t("trading.tradeCount")}</p>
                        </div>
                        <p className={`font-mono tabular-nums text-[13px] ${signTone(data.avgProfit)}`}>{formatPercent(data.avgProfit)}</p>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
                        <span>
                          {t("trading.avgWinShort")}{" "}
                          <span className="font-mono text-success">{data.winCount > 0 ? formatPercent(data.avgWin) : "-"}</span>{" "}
                          ({data.winCount}{t("trading.tradeCountShort")})
                        </span>
                        <span>
                          {t("trading.avgLossShort")}{" "}
                          <span className="font-mono text-destructive">{data.lossCount > 0 ? formatPercent(data.avgLoss) : "-"}</span>{" "}
                          ({data.lossCount}{t("trading.tradeCountShort")})
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* 거래 상세 내역 */}
      <Card className="gap-0 py-0 rounded-lg shadow-none">
        {cardHeader(t("trading.detailedHistory"), `${history.length}${t("trading.tradeCount")}`)}
        <CardContent className="px-0 pb-0">
          <div className="divide-y divide-border border-t border-border">
            {history.map((trade) => (
              <article key={trade.id} className="px-4 sm:px-5 py-4 space-y-3">
                {/* 종목 헤더 */}
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <h3 className="font-medium text-foreground">{trade.company_name}</h3>
                      <span className="text-xs text-muted-foreground font-mono">{trade.ticker}</span>
                      {trade.scenario?.sector && (
                        <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">{trade.scenario.sector}</Badge>
                      )}
                      {trade.scenario?.investment_period && (
                        <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">{trade.scenario.investment_period}</Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground tabular-nums">
                      {formatDate(trade.buy_date)} → {formatDate(trade.sell_date)} ({trade.holding_days}{t("trading.daysHeld")})
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-muted-foreground">{t("trading.profitRate")}</p>
                    <p className={`font-serif text-2xl leading-none tracking-tight tabular-nums mt-1 ${signTone(trade.profit_rate)}`}>
                      {formatPercent(trade.profit_rate)}
                    </p>
                    {(trade.allocation ?? 1) < 1 || (trade.add_count || 0) > 0 ? (
                      <p className="text-[11px] text-muted-foreground mt-1 tabular-nums">
                        {language === "ko"
                          ? `${trade.allocation_label || `비중 ${Math.round((trade.allocation ?? 1) * 100)}%`} · 슬롯 기준 ${formatPercent(trade.slot_profit_rate ?? trade.profit_rate * (trade.allocation ?? 1))}`
                          : `${(trade.allocation_label || `${Math.round((trade.allocation ?? 1) * 100)}%`).replace("비중", "Alloc")} · slot ${formatPercent(trade.slot_profit_rate ?? trade.profit_rate * (trade.allocation ?? 1))}`}
                        {(trade.add_count || 0) > 0 ? (language === "ko" ? " · 매수가는 평균" : " · avg entry") : ""}
                      </p>
                    ) : null}
                  </div>
                </div>

                {/* 거래 정보 */}
                <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-3 rounded-md border border-border p-3">
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t("trading.buyPrice")}</dt>
                    <dd className="font-mono tabular-nums text-[13px] text-foreground">{formatCurrency(trade.buy_price)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t("trading.sellPrice")}</dt>
                    <dd className="font-mono tabular-nums text-[13px] text-foreground">{formatCurrency(trade.sell_price)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t("trading.aiTargetPrice")}</dt>
                    <dd className="font-mono tabular-nums text-[13px] text-success">
                      {trade.scenario?.target_price ? formatCurrency(trade.scenario.target_price) : "-"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground mb-0.5">{t("trading.targetAchievementRate")}</dt>
                    <dd className="font-mono tabular-nums text-[13px] text-foreground">
                      {trade.scenario?.target_price
                        ? `${((trade.sell_price / trade.scenario.target_price) * 100).toFixed(0)}%`
                        : "-"}
                    </dd>
                  </div>
                </dl>

                {/* AI 시나리오 */}
                {trade.scenario && (
                  <div className="space-y-3">
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">{t("trading.aiInvestmentRationale")}</p>
                      {trade.scenario.rationale && (
                        <p className="text-sm text-foreground leading-relaxed break-words">{trade.scenario.rationale}</p>
                      )}
                    </div>
                    {trade.scenario.portfolio_analysis && textBlock(t("modal.portfolioAnalysis"), trade.scenario.portfolio_analysis)}
                    {trade.scenario.valuation_analysis && textBlock(t("modal.valuationAnalysis"), trade.scenario.valuation_analysis)}
                    {trade.scenario.sector_outlook && textBlock(t("modal.sectorOutlook"), trade.scenario.sector_outlook)}
                    {trade.scenario.market_condition && textBlock(t("trading.pastMarketCondition"), trade.scenario.market_condition)}

                    {/* 매매 시나리오 상세 */}
                    {trade.scenario.trading_scenarios && (
                      <div className="rounded-md border border-border p-3 space-y-3">
                        <p className="text-xs font-medium text-foreground">{t("trading.aiScenarioDetails")}</p>

                        {trade.scenario.trading_scenarios.key_levels && (
                          <div>
                            <p className="text-xs text-muted-foreground mb-1.5">{t("modal.keyPriceLevels")}</p>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 text-xs">
                              {levelLabels.map(([key, label]) => {
                                const value = trade.scenario?.trading_scenarios?.key_levels?.[key]
                                return value ? (
                                  <div key={key} className="rounded bg-muted/50 px-2 py-1.5">
                                    <span className="text-muted-foreground">{label}: </span>
                                    <span className="text-foreground tabular-nums">{value}</span>
                                  </div>
                                ) : null
                              })}
                            </div>
                          </div>
                        )}

                        {trade.scenario.trading_scenarios.sell_triggers && trade.scenario.trading_scenarios.sell_triggers.length > 0 && (
                          <div>
                            <p className="text-xs text-muted-foreground mb-1.5">{t("modal.sellTriggers")}</p>
                            <ul className="space-y-1">
                              {trade.scenario.trading_scenarios.sell_triggers.map((trigger, idx) => (
                                <li key={idx} className="text-xs text-foreground leading-relaxed break-words pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-destructive">
                                  {trigger}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {trade.scenario.trading_scenarios.hold_conditions && trade.scenario.trading_scenarios.hold_conditions.length > 0 && (
                          <div>
                            <p className="text-xs text-muted-foreground mb-1.5">{t("modal.holdConditions")}</p>
                            <ul className="space-y-1">
                              {trade.scenario.trading_scenarios.hold_conditions.map((condition, idx) => (
                                <li key={idx} className="text-xs text-foreground leading-relaxed break-words pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-success">
                                  {condition}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {trade.scenario.trading_scenarios.portfolio_context && (
                          <div>
                            <p className="text-xs text-muted-foreground mb-1.5">{t("modal.portfolioContext")}</p>
                            <p className="text-xs text-foreground leading-relaxed break-words">{trade.scenario.trading_scenarios.portfolio_context}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        </CardContent>
      </Card>

      {history.length === 0 && (
        <Card className="gap-0 py-0 rounded-lg shadow-none">
          <CardContent className="p-10 text-center">
            <p className="text-sm text-muted-foreground">{t("trading.noData")}</p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
