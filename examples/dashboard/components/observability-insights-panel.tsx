"use client"

import type React from "react"
import { ArrowDownRight, ArrowUpRight } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"
import { cn } from "@/lib/utils"
import type {
  Market,
  ObservabilityInsightsSnapshot,
  ObservabilityTradeMetrics,
} from "@/types/dashboard"

interface Props {
  data: ObservabilityInsightsSnapshot
  market: Market
}

const pct = (value: number | null, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : (value * 100).toFixed(digits) + "%"

const pctDirect = (value: number | null, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : (value >= 0 ? "+" : "") + value.toFixed(digits) + "%"

const number = (value: number | null, digits = 2) =>
  value === null || value === undefined ? "—" : value.toFixed(digits)

const tone = (value: number | null) =>
  value === null || value === undefined || value === 0
    ? ""
    : value > 0 ? "text-success" : "text-destructive"

function SampleBadge({
  sufficient,
  language,
}: {
  sufficient: boolean
  language: string
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px] font-normal",
        sufficient ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400",
      )}
    >
      {sufficient
        ? language === "ko" ? "표본 확보" : "Sample ready"
        : language === "ko" ? "표본 부족" : "Low sample"}
    </Badge>
  )
}

function Row({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-mono tabular-nums text-foreground", className)}>{value}</span>
    </div>
  )
}

function Tile({ label, value, children }: { label: string; value: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      <CardContent className="p-4 sm:p-5 space-y-3">
        <span className="text-[13px] text-muted-foreground block">{label}</span>
        <p className="font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums truncate">
          {value}
        </p>
        <div className="space-y-1">{children}</div>
      </CardContent>
    </Card>
  )
}

function metricDelta(
  pre: ObservabilityTradeMetrics,
  post: ObservabilityTradeMetrics,
) {
  if (pre.avg_return_pct === null || post.avg_return_pct === null) return null
  return post.avg_return_pct - pre.avg_return_pct
}

export function ObservabilityInsightsPanel({ data, market }: Props) {
  const { language } = useLanguage()
  const ko = language === "ko"
  const current = data.markets[market]
  const latestEvent = data.generated_at ? new Date(data.generated_at) : null
  const staleMinutes = latestEvent
    ? Math.max(0, (Date.now() - latestEvent.getTime()) / 60_000)
    : Number.POSITIVE_INFINITY
  const impacts = data.deployment_impacts.slice().reverse().slice(0, 8)
  const contextLedger = current.context_ledger
  const entryQualityCapture = current.entry_quality_capture
  const journalCapture = current.journal_influence_capture
  const fmtTime = (v: string) => new Date(v).toLocaleString(ko ? "ko-KR" : "en-US")
  const th = "h-9 px-2 text-xs font-normal text-muted-foreground whitespace-nowrap"
  const td = "px-2 py-2 text-right font-mono tabular-nums text-[13px]"

  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <div>
          <h3 className="text-sm font-medium text-foreground">
            {ko ? "PRISM 관측 센터" : "PRISM Observatory"}
          </h3>
          <p className="text-xs text-muted-foreground">
            {ko
              ? "ClickHouse 원본 이벤트를 정제한 매매 성과와 배포 영향"
              : "Curated trading performance and deployment impact from ClickHouse events"}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Badge
            variant="outline"
            className={cn("text-[10px] font-normal", staleMinutes <= 30 ? "text-success" : "text-destructive")}
          >
            {staleMinutes <= 30
              ? ko ? "수집 정상" : "Fresh"
              : ko ? "갱신 지연" : "Stale"}
          </Badge>
          <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">{market}</Badge>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Tile label={ko ? "실제 매매" : "Actual trades"} value={current.actual.count}>
          <Row label={ko ? "승률" : "Win rate"} value={pct(current.actual.win_rate)} />
          <Row label="PF" value={number(current.actual.profit_factor)} />
          <Row
            label={ko ? "평균" : "Average"}
            value={pctDirect(current.actual.avg_return_pct)}
            className={tone(current.actual.avg_return_pct)}
          />
          <div className="pt-1">
            <SampleBadge sufficient={current.actual.sample_sufficient} language={language} />
          </div>
        </Tile>

        <Tile label={ko ? "매매 컨텍스트 원장" : "Trade context ledger"} value={(contextLedger?.total || 0).toLocaleString()}>
          <Row
            label={ko ? "후보/진입/청산" : "Candidate/Entry/Exit"}
            value={`${contextLedger?.candidates || 0}/${contextLedger?.entries || 0}/${contextLedger?.exits || 0}`}
          />
          <Row label={ko ? "완결 포지션" : "Complete chains"} value={contextLedger?.complete_position_chains || 0} />
          <Row label={ko ? "진입품질 수집률" : "Entry-quality capture"} value={pct(entryQualityCapture?.coverage_rate ?? null, 0)} />
          <Row label={ko ? "매매일지 영향 수집률" : "Journal influence capture"} value={pct(journalCapture?.coverage_rate ?? null, 0)} />
          <Row
            label={ko ? "LLM 참고/입력 있음" : "LLM referenced/input present"}
            value={`${journalCapture?.llm_referenced_count || 0}/${journalCapture?.input_present_count || 0}`}
          />
          <Row
            label={ko ? "점수조정/threshold 변경" : "Score adjust/threshold flip"}
            value={`${journalCapture?.deterministic_adjustment_count || 0}/${
              (journalCapture?.threshold_crossing_distribution?.ALLOW_TO_BLOCK || 0) +
              (journalCapture?.threshold_crossing_distribution?.BLOCK_TO_ALLOW || 0)
            }`}
          />
          <Row
            label={ko ? "체결 확인/주문 관측" : "Confirmed/observed fills"}
            value={`${entryQualityCapture?.confirmed_fill_count || 0}/${entryQualityCapture?.fill_reconciliation_count || 0}`}
          />
          <p className="pt-1 text-[11px] text-muted-foreground font-mono tabular-nums">
            {contextLedger?.latest_at ? fmtTime(contextLedger.latest_at) : ko ? "수집 대기" : "Awaiting events"}
          </p>
        </Tile>

        <Tile label={ko ? "관찰 후보" : "Watched candidates"} value={current.candidate.count}>
          <Row label={ko ? "30일 상승" : "30d positive"} value={pct(current.candidate.positive_rate_30d)} />
          <Row
            label={ko ? "30일 평균" : "30d average"}
            value={pctDirect(current.candidate.avg_30d_pct)}
            className={tone(current.candidate.avg_30d_pct)}
          />
          <Row
            label={ko ? "30일 중앙값" : "30d median"}
            value={pctDirect(current.candidate.median_30d_pct)}
            className={tone(current.candidate.median_30d_pct)}
          />
          <div className="pt-1">
            <SampleBadge sufficient={current.candidate.sample_sufficient} language={language} />
          </div>
        </Tile>

        <Tile
          label={ko ? "현재 시장 국면" : "Latest regime"}
          value={<span className="capitalize">{current.latest_regime?.regime || "—"}</span>}
        >
          <Row label={ko ? "신뢰도" : "Confidence"} value={pct(current.latest_regime?.confidence ?? null)} />
          <p className="pt-1 text-[11px] text-muted-foreground font-mono tabular-nums">
            {current.latest_regime?.observed_at ? fmtTime(current.latest_regime.observed_at) : "—"}
          </p>
        </Tile>

        <Tile label={ko ? "관측 데이터" : "Telemetry"} value={data.data_quality.total_events.toLocaleString()}>
          <Row label="Live" value={data.data_quality.live_events} />
          <Row label="Backfill" value={data.data_quality.backfill_events} />
          <p className="pt-1 text-[11px] text-muted-foreground">
            {ko ? data.retention_days + "일 보존" : data.retention_days + "d retention"}
          </p>
        </Tile>
      </div>

      <Card className="gap-0 py-0 rounded-lg shadow-none">
        <CardHeader className="px-5 pt-4 pb-3">
          <CardTitle className="text-sm font-medium">
            {ko ? "트리거별 Candidate vs Actual" : "Candidate vs Actual by trigger"}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {ko
              ? "관찰 후보의 30일 성과와 실제 청산 성과는 서로 다른 지표로 표시합니다."
              : "Watched 30-day outcomes and realized trade outcomes remain separate."}
          </p>
        </CardHeader>
        <CardContent className="px-0 pb-1 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b">
                <th className={cn(th, "pl-5 text-left")}>{ko ? "트리거" : "Trigger"}</th>
                <th className={cn(th, "text-right")}>Actual n</th>
                <th className={cn(th, "text-right")}>{ko ? "실제 승률" : "Actual WR"}</th>
                <th className={cn(th, "text-right")}>PF</th>
                <th className={cn(th, "text-right")}>{ko ? "실제 평균" : "Actual avg"}</th>
                <th className={cn(th, "text-right")}>Candidate n</th>
                <th className={cn(th, "text-right")}>{ko ? "30일 상승" : "30d positive"}</th>
                <th className={cn(th, "pr-5 text-right")}>{ko ? "후보 평균" : "Candidate avg"}</th>
              </tr>
            </thead>
            <tbody>
              {current.triggers.slice(0, 12).map((row) => (
                <tr key={row.trigger_type} className="border-b last:border-b-0 hover:bg-muted/50 transition-colors">
                  <td className="pl-5 pr-2 py-2 font-medium">{row.trigger_type}</td>
                  <td className={td}>{row.actual.count}</td>
                  <td className={td}>{pct(row.actual.win_rate, 0)}</td>
                  <td className={td}>{number(row.actual.profit_factor)}</td>
                  <td className={cn(td, tone(row.actual.avg_return_pct))}>{pctDirect(row.actual.avg_return_pct)}</td>
                  <td className={td}>{row.candidate.count}</td>
                  <td className={td}>{pct(row.candidate.positive_rate_30d, 0)}</td>
                  <td className={cn(td, "pr-5", tone(row.candidate.avg_30d_pct))}>{pctDirect(row.candidate.avg_30d_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card className="gap-0 py-0 rounded-lg shadow-none">
        <CardHeader className="px-5 pt-4 pb-3">
          <CardTitle className="text-sm font-medium">
            {ko ? "배포 전후 14일 영향" : "14-day deployment impact"}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {ko
              ? "매수일 기준 전후 코호트 비교입니다. 인과관계가 아니라 관측된 변화입니다."
              : "Buy-date cohort comparison. This is observed association, not proof of causality."}
          </p>
        </CardHeader>
        <CardContent className="px-0 pb-1">
          {impacts.length === 0 ? (
            <p className="px-5 pb-4 text-sm text-muted-foreground">
              {ko ? "배포 기록이 없습니다." : "No deployment records."}
            </p>
          ) : (
            <div className="divide-y border-t">
              {impacts.map((impact) => {
                const currentImpact = impact.markets[market]
                const delta = metricDelta(currentImpact.pre, currentImpact.post)
                return (
                  <div
                    key={impact.git_sha + "-" + impact.target}
                    className="grid grid-cols-[1fr_1fr_auto] items-baseline gap-x-3 gap-y-1.5 px-5 py-3 hover:bg-muted/50 transition-colors md:grid-cols-[1.5fr_1fr_1fr_auto] md:items-center md:gap-4"
                  >
                    <div className="col-span-3 md:col-span-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <code className="font-mono text-xs text-foreground">{impact.git_sha.slice(0, 8)}</code>
                        <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                          {impact.target}
                        </Badge>
                        {!impact.post_window_complete && (
                          <Badge variant="outline" className="text-[10px] font-normal text-muted-foreground">
                            {ko ? "관측 중" : "In progress"}
                          </Badge>
                        )}
                        <span className="text-[11px] text-muted-foreground font-mono tabular-nums">
                          {fmtTime(impact.timestamp)}
                        </span>
                      </div>
                      {impact.subject && (
                        <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{impact.subject}</p>
                      )}
                    </div>
                    <div className="text-xs">
                      <span className="text-muted-foreground">{ko ? "이전 " : "Pre "}</span>
                      <span className="font-mono tabular-nums text-[13px]">
                        {currentImpact.pre.count} / {pctDirect(currentImpact.pre.avg_return_pct)}
                      </span>
                    </div>
                    <div className="text-xs">
                      <span className="text-muted-foreground">{ko ? "이후 " : "Post "}</span>
                      <span className="font-mono tabular-nums text-[13px]">
                        {currentImpact.post.count} / {pctDirect(currentImpact.post.avg_return_pct)}
                      </span>
                    </div>
                    <div
                      className={cn(
                        "flex items-center gap-1 font-mono tabular-nums text-[13px] md:justify-end",
                        delta === null ? "text-muted-foreground" : delta >= 0 ? "text-success" : "text-destructive",
                      )}
                    >
                      {delta === null ? null : delta >= 0
                        ? <ArrowUpRight className="h-3.5 w-3.5" />
                        : <ArrowDownRight className="h-3.5 w-3.5" />}
                      {delta === null ? "—" : pctDirect(delta)}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  )
}
