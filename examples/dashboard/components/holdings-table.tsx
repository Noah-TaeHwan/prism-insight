"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil, formatPercent as formatPercentUtil } from "@/lib/currency"
import type { Holding, Market } from "@/types/dashboard"

interface HoldingsTableProps {
  holdings: Holding[]
  onStockClick: (stock: Holding) => void
  title?: string
  isRealTrading?: boolean
  market?: Market
}

export function HoldingsTable({ holdings, onStockClick, title = "보유 종목", isRealTrading = false, market = "KR" }: HoldingsTableProps) {
  const { language, t } = useLanguage()

  const isUSMarket = market === "US"

  const formatCurrency = (value: number | undefined) => {
    if (value === undefined || value === null) return isUSMarket ? "$0.00" : "₩0"
    return formatCurrencyUtil(value, market, language as "ko" | "en")
  }

  const formatPercent = (value: number | undefined) => {
    if (value === undefined || value === null) return "0.00%"
    return formatPercentUtil(value, true)
  }

  const formatWeight = (value: number | undefined) => {
    if (value === undefined || value === null) return "-"
    return `${value.toFixed(2)}%`
  }

  return (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      <CardHeader className="px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-sm font-medium">{title}</CardTitle>
            {isRealTrading ? (
              <>
                <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                  {isUSMarket ? (language === "ko" ? "미국 실전투자" : "US Real") : t("badge.realTrading")}
                </Badge>
                <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                  {isUSMarket ? "Season 1" : t("badge.season2")}
                </Badge>
              </>
            ) : (
              <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                {isUSMarket ? (language === "ko" ? "미국 시뮬레이션" : "US Simulation") : t("badge.aiSimulation")}
              </Badge>
            )}
          </div>
          <span className="text-xs text-muted-foreground font-mono tabular-nums">
            {holdings.length}{isRealTrading ? "" : " / 10"}
          </span>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-1">
        {holdings.length === 0 ? (
          <div className="px-5 pb-5 space-y-3">
            {!isRealTrading && (
              <div className="grid grid-cols-10 gap-1" aria-hidden="true">
                {Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className="h-5 rounded border border-dashed border-border" />
                ))}
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">
                {language === "ko" ? "보유 종목 없음." : "No open positions."}
              </span>{" "}
              {language === "ko"
                ? "전액 현금으로 매수 신호를 기다리는 중입니다."
                : "Fully in cash, waiting for the next buy signal."}
            </p>
          </div>
        ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="text-xs font-normal text-muted-foreground first:pl-5">{t("table.stockName")}</TableHead>
                {!isRealTrading && <TableHead className="text-xs font-normal text-muted-foreground first:pl-5">{t("table.sector")}</TableHead>}
                {isRealTrading ? (
                  <>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.quantity")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.avgPrice")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.currentPrice")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.totalValue")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.profitAmount")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.profitRate")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.weight")}</TableHead>
                  </>
                ) : (
                  <>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.buyPrice")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.currentPrice")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.targetPrice")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.stopLoss")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.profitRate")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.holdingDays")}</TableHead>
                    <TableHead className="text-right text-xs font-normal text-muted-foreground last:pr-5">{t("table.period")}</TableHead>
                  </>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((holding) => {
                const stockName = holding.company_name || holding.name || t("table.unknown")
                const buyPrice = holding.buy_price || holding.avg_price || 0
                
                return (
                  <TableRow
                    key={holding.ticker}
                    className="cursor-pointer hover:bg-muted/50 transition-colors font-mono tabular-nums text-[13px]"
                    onClick={() => onStockClick(holding)}
                  >
                    <TableCell className="pl-5 font-sans">
                      <div>
                        <p className="font-medium text-foreground">{stockName}</p>
                        <p className="text-xs text-muted-foreground">{holding.ticker}</p>
                      </div>
                    </TableCell>
                    
                    {!isRealTrading && (
                      <TableCell className="font-sans">
                        <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                          {holding.sector || holding.scenario?.sector || "-"}
                        </Badge>
                      </TableCell>
                    )}
                    
                    {isRealTrading ? (
                      <>
                        <TableCell className="text-right font-medium">
                          {(holding.quantity || 0).toLocaleString()}{t("common.shares")}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatCurrency(holding.avg_price)}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatCurrency(holding.current_price)}
                        </TableCell>
                        <TableCell className="text-right text-xs font-normal text-muted-foreground last:pr-5">
                          {formatCurrency(holding.value)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className={`font-semibold ${(holding.profit || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                            {formatCurrency(holding.profit)}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          <span className={`font-medium ${(holding.profit_rate || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                            {formatPercent(holding.profit_rate)}
                          </span>
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground pr-5">
                          {formatWeight(holding.weight)}
                        </TableCell>
                      </>
                    ) : (
                      <>
                        <TableCell className="text-right text-muted-foreground">
                          {formatCurrency(buyPrice)}
                          {(holding.allocation ?? 1) < 1 || (holding.add_count || 0) > 0 ? (
                            <div className="mt-1 flex flex-col items-end gap-0.5">
                              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                                {language === "ko"
                                  ? holding.allocation_label || `비중 ${Math.round((holding.allocation ?? 1) * 100)}%`
                                  : (holding.allocation_label || `${Math.round((holding.allocation ?? 1) * 100)}%`).replace("비중", "Alloc")}
                              </Badge>
                              {(holding.add_count || 0) > 0 && holding.average_entry ? (
                                <span className="text-[10px]">
                                  {language === "ko" ? "평균 " : "Avg "}{formatCurrency(holding.average_entry)}
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatCurrency(holding.current_price)}
                        </TableCell>
                        <TableCell className="text-right text-success">
                          {formatCurrency(holding.target_price)}
                        </TableCell>
                        <TableCell className="text-right text-destructive">
                          {formatCurrency(holding.stop_loss)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className={`font-medium ${(holding.profit_rate || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                            {formatPercent(holding.profit_rate)}
                          </span>
                          {(holding.allocation ?? 1) < 1 && holding.slot_profit_rate !== undefined ? (
                            <div className="text-[10px] text-muted-foreground">
                              {language === "ko" ? "슬롯 기준 " : "Slot "}{formatPercent(holding.slot_profit_rate)}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {holding.holding_days || 0}{t("common.days")}
                        </TableCell>
                        <TableCell className="text-right pr-5 font-sans">
                          <Badge variant={holding.investment_period === "단기" ? "secondary" : "outline"} className="text-xs font-normal">
                            {holding.investment_period || "-"}
                          </Badge>
                        </TableCell>
                      </>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
        )}
      </CardContent>
    </Card>
  )
}
