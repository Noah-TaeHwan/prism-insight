"use client"

import { useEffect, useState } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

// Static snapshot built from GitHub/git history, the PRISM dashboard data and yfinance
// (see "기준" at the bottom of the page). Refresh by replacing public/ops_report.json.
interface DrawdownRow { day: string; prism: number; idx_a: number | null; idx_b: number | null }
interface Crisis {
  n: number; title: string; kind: "rally" | "drop"; start: string; end: string; index: string
  index_drop_pct: number; index_chg_pct: number | null; worst_day_pct: number; merged: number
  prism_change: number | null; hit: string; response: string
}
interface Layer { lane: string; day: string; label: string; pr: number }
interface Study { day: string; question: string; verdict: string }
interface LocRow { as_of: string; core: number; us: number; btc: number; web_ops: number; tools: number; tests: number; docs: number; code_lines: number }
interface OpsReport {
  as_of: { returns: string; code: string; prs: string }
  summary: { merged: number; first_merge: string; first100_days: number; last100_days: number; recent4w_per_week: number; overall_per_week: number }
  risk: Record<string, number | string>
  us_split: Record<string, number | string>
  positions: { market: string; holdings: number }[]
  complexity: { test_functions: number; cron_jobs: number; db_tables: number; servers: number }
  loc_growth: { code_now: number; mult_total: number; mult_since_may: number; tests_now: number }
  layer_summary: { total: number }
  study_summary: { total: number; changed: number; dropped: number; held: number }
  drawdown: { KR: DrawdownRow[]; US: DrawdownRow[] }
  crises: Crisis[]
  layers: Layer[]
  studies: Study[]
  loc: LocRow[]
  cumulative: { day: string; total: number }[]
}

const fmt = (v: number | string | undefined) => (typeof v === "number" ? v.toLocaleString("ko-KR") : v ?? "—")
const pct = (v: number | string | undefined, signed = false) =>
  typeof v === "number" ? `${signed && v > 0 ? "+" : ""}${v}%` : "—"

const PRISM_COLOR = "#2563eb"
const IDX_A_COLOR = "#94a3b8"
const IDX_B_COLOR = "#f59e0b"
const LANES: [string, string, string][] = [
  ["entry", "진입·스크리닝", "#2563eb"],
  ["size", "비중", "#7c3aed"],
  ["exit", "매도·손절", "#dc2626"],
  ["reentry", "재진입", "#0891b2"],
  ["regime", "시장 국면", "#d97706"],
  ["measure", "데이터·측정·검증", "#16a34a"],
]
const LOC_KEYS: [keyof LocRow, string, string][] = [
  ["core", "한국·공통 엔진", "#2563eb"],
  ["us", "미국", "#0891b2"],
  ["btc", "BTC", "#d97706"],
  ["web_ops", "웹·봇·운영", "#7c3aed"],
  ["tools", "도구·연구", "#db2777"],
  ["tests", "테스트", "#16a34a"],
]
const VERDICT_COLOR: Record<string, string> = {
  "바꿈": "bg-emerald-500",
  "버림·현행 유지": "bg-slate-400 dark:bg-slate-600",
  "보류·참고": "bg-amber-400",
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "bad" | "good" }) {
  const toneClass = tone === "bad" ? "text-red-600 dark:text-red-400" : tone === "good" ? "text-emerald-600 dark:text-emerald-400" : ""
  return (
    <div className="rounded-xl border bg-card p-4 flex flex-col gap-1 min-w-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</span>
      {sub && <span className="text-xs text-muted-foreground tabular-nums">{sub}</span>}
    </div>
  )
}

function Bullets({ items, small }: { items: React.ReactNode[]; small?: boolean }) {
  return (
    <ul className={`list-disc pl-5 space-y-1 ${small ? "text-xs text-muted-foreground" : "text-sm"}`}>
      {items.map((it, i) => <li key={i}>{it}</li>)}
    </ul>
  )
}

function SectionTitle({ no, children }: { no?: string; children: React.ReactNode }) {
  return (
    <h2 className="text-xl font-semibold flex items-center gap-2">
      {no && <span className="text-muted-foreground">{no}</span>}
      {children}
    </h2>
  )
}

function DrawdownChart({ rows, a, b, me }: { rows: DrawdownRow[]; a: string; b: string; me: string }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(2, 7).replace("-", ".")} minTickGap={40} fontSize={11} />
        <YAxis tickFormatter={(v: number) => `${v}%`} fontSize={11} width={48} />
        <Tooltip formatter={(v: number, name: string) => [`${v}%`, name]} labelFormatter={(d: string) => d} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="idx_a" name={a} stroke={IDX_A_COLOR} strokeDasharray="4 3" dot={false} strokeWidth={1.5} connectNulls />
        <Line type="monotone" dataKey="idx_b" name={b} stroke={IDX_B_COLOR} strokeDasharray="4 3" dot={false} strokeWidth={1.5} connectNulls />
        <Line type="monotone" dataKey="prism" name={me} stroke={PRISM_COLOR} dot={false} strokeWidth={2.5} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function LayerTimeline({ layers }: { layers: Layer[] }) {
  const t0 = Date.parse("2025-08-01")
  const t1 = Math.max(...layers.map((l) => Date.parse(l.day)))
  const x = (d: string) => (100 * (Date.parse(d) - t0)) / (t1 - t0)
  const years = ["2025-09-01", "2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"]
  return (
    <div className="space-y-2">
      {LANES.map(([key, label, color]) => (
        <div key={key} className="flex items-center gap-3">
          <span className="w-28 shrink-0 text-xs text-muted-foreground text-right">{label}</span>
          <div className="relative h-6 flex-1 border-b border-border/60">
            {layers.filter((l) => l.lane === key).map((l) => (
              <span
                key={`${l.pr}-${l.label}`}
                title={`${l.day} · #${l.pr} · ${l.label}`}
                className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-background"
                style={{ left: `${x(l.day)}%`, background: color }}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="flex gap-3">
        <span className="w-28 shrink-0" />
        <div className="relative h-4 flex-1 text-[10px] text-muted-foreground">
          {years.map((d) => (
            <span key={d} className="absolute -translate-x-1/2" style={{ left: `${x(d)}%` }}>{d.slice(2, 7).replace("-", ".")}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

export function OpsReportPage() {
  const [r, setR] = useState<OpsReport | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch("/ops_report.json")
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(setR)
      .catch(() => setError(true))
  }, [])

  if (error) return <p className="text-center text-muted-foreground py-20">운영 보고서를 불러오지 못했습니다.</p>
  if (!r) return <div className="flex justify-center py-20"><div className="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin" /></div>

  const k = r.risk, u = r.us_split
  const krHold = r.positions.find((p) => p.market === "KR")?.holdings ?? 0
  const verdicts = ["바꿈", "버림·현행 유지", "보류·참고"]
  const studies = [...r.studies].sort((a, b) => verdicts.indexOf(a.verdict) - verdicts.indexOf(b.verdict) || a.day.localeCompare(b.day))

  return (
    <div className="mx-auto max-w-6xl space-y-10">
      {/* Header */}
      <header className="space-y-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">완성된 자동매매는 없습니다</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            PRISM-INSIGHT 운영 보고서 · 한국 2025-09-29 / 미국 2026-01-29 시작 · 수익률 {r.as_of.returns}, 코드 {r.as_of.code} 기준
          </p>
        </div>
        <Card className="py-5">
          <CardContent>
            <Bullets items={[
              <><b>폭락장 방어</b> — KOSPI 고점 대비 <b>{pct(k.kospi_dd_pct)}</b>, PRISM 한국 <b>{pct(k.kr_dd_pct)}p</b>, 보유 <b>{krHold}</b>종목(현금 100%)</>,
              <><b>위기마다 규칙 보완</b> — 위기 {r.crises.length}회 대응, 머지 PR <b>{fmt(r.summary.merged)}</b>개, 최근 100개는 <b>{r.summary.last100_days}일</b> 만에 머지</>,
              <><b>바꾸기 전에 검증</b> — 연구 <b>{r.study_summary.total}</b>건 중 규칙 변경 <b>{r.study_summary.changed}</b>건, 나머지는 기각·보류</>,
              <><b>약점 공개</b> — 미국 <b>{pct(k.us_ret_pct, true)}</b> vs S&amp;P 500 <b>{pct(k.sp_since_pct, true)}</b>, 10월 초분할·재진입·주도주 보유로 대응 중(성과 미입증)</>,
            ]} />
          </CardContent>
        </Card>
      </header>

      {/* ① Risk */}
      <section className="space-y-4">
        <SectionTitle no="①">폭락장 방어</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Tile label="KOSPI 고점 대비" value={pct(k.kospi_dd_pct)} sub={`고점 ${k.kospi_peak_day}`} tone="bad" />
          <Tile label="KOSDAQ 고점 대비" value={pct(k.kosdaq_dd_pct)} sub={`고점 ${k.kosdaq_peak_day}`} tone="bad" />
          <Tile label="PRISM 한국 고점 대비" value={`${pct(k.kr_dd_pct)}p`} sub={`누적 ${pct(k.kr_peak_ret_pct, true)} → ${pct(k.kr_ret_pct, true)}`} />
          <Tile label="한국 보유 종목" value={`${krHold}`} sub="조정장 판정 → 신규 매수 중단" tone="good" />
          <Tile label="PRISM 미국 시작 이후" value={pct(k.us_ret_pct, true)} sub={`S&P 500 ${pct(k.sp_since_pct, true)} · NASDAQ ${pct(k.nq_since_pct, true)}`} />
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <Card>
            <CardHeader><CardTitle className="text-base">한국: 고점 대비 낙폭</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <DrawdownChart rows={r.drawdown.KR} a="KOSPI" b="KOSDAQ" me="PRISM 한국" />
              <Bullets small items={["PRISM은 원금 대비 %p(대시보드 MDD와 같은 방식), 지수는 고점 대비 %", "0에 가까울수록 덜 빠짐"]} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">미국: 고점 대비 낙폭</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <DrawdownChart rows={r.drawdown.US} a="S&P 500" b="NASDAQ" me="PRISM 미국" />
              <Bullets small items={[
                <b key="h" className="text-foreground">지수보다 많이 빠진 이유</b>,
                `고점(${u.peak_day}) 이후 지수 횡보: S&P 500 ${pct(u.sp_after_pct, true)}, NASDAQ ${pct(u.nq_after_pct, true)}`,
                `승률 ${u.win_after_pct}% (고점 전 ${u.win_before_pct}%)`,
                `평균 이익 +${u.avgw_after_pct}% (전 +${u.avgw_before_pct}%) · 평균 손실 ${u.avgl_after_pct}% (전 ${u.avgl_before_pct}%)`,
                `+10% 이상 종목 ${u.big_after}건 (전 ${u.big_before}건) · −3% 이하 손실 ${u.stop_after}건 / 전체 ${u.n_after}건`,
                "진단: 방향 없는 장에서 돌파 매수 → 손절 반복, 큰 종목 부재 (진입·매도 중 원인 비중은 미구분)",
                "대응: 10월 초분할(첫 매수 소량)·재진입",
              ]} />
            </CardContent>
          </Card>
        </div>
      </section>

      {/* ② Crises */}
      <section className="space-y-4">
        <SectionTitle no="②">위기 {r.crises.length}회 · 문제와 대응</SectionTitle>
        <Bullets small items={[
          "상승장 위기 = 못 사거나 크게 깨짐 / 하락장 위기 = 버티기",
          "하락 구간: 구간 내 고점→저점·최악의 하루 · 상승 구간: 구간 전체 등락 · PR: 구간 내 머지 수 · PRISM: 누적 수익 변화(%p)",
        ]} />
        <ol className="grid md:grid-cols-2 gap-3">
          {r.crises.map((c) => (
            <li key={c.n} className="rounded-xl border bg-card p-4 space-y-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <b className="text-sm">{c.title}</b>
                <span className="text-xs text-muted-foreground tabular-nums">{c.start.slice(2).replaceAll("-", ".")} ~ {c.end.slice(5).replace("-", ".")}</span>
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs tabular-nums">
                {c.kind === "rally" ? (
                  <span className="rounded-full border px-2 text-emerald-600 dark:text-emerald-400">{c.index} {pct(c.index_chg_pct ?? undefined, true)} 상승장</span>
                ) : (
                  <>
                    <span className="rounded-full border px-2 text-red-600 dark:text-red-400">{c.index} {c.index_drop_pct}%</span>
                    <span className="rounded-full border px-2 text-muted-foreground">최악의 하루 {c.worst_day_pct}%</span>
                  </>
                )}
                <span className="rounded-full border px-2 text-muted-foreground">PR {c.merged}건</span>
                {c.prism_change != null && (
                  <span className="rounded-full border px-2 text-muted-foreground">PRISM {c.prism_change > 0 ? "+" : ""}{c.prism_change}%p</span>
                )}
              </div>
              <p className="text-xs leading-relaxed"><b className="text-red-600 dark:text-red-400 mr-1.5">문제</b>{c.hit}</p>
              <p className="text-xs leading-relaxed"><b className="text-emerald-600 dark:text-emerald-400 mr-1.5">대응</b>{c.response}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ③ Rigor */}
      <section className="space-y-4">
        <SectionTitle no="③">검증 체계</SectionTitle>
        <Bullets items={[
          <>판단 장치 <b>{r.layer_summary.total}</b>개 · 진입·비중·매도·재진입·시장 국면·데이터 6개 영역으로 분리</>,
          <>연구 <b>{r.study_summary.total}</b>건 중 규칙 변경 <b>{r.study_summary.changed}</b>건 · 기각·현행 유지 <b>{r.study_summary.dropped}</b>건 · 보류 <b>{r.study_summary.held}</b>건</>,
          "원칙: 과거 데이터 재현 → 소규모 실전 → 확대, LIVE 전환은 사람이 승인",
        ]} />
        <Card>
          <CardHeader><CardTitle className="text-base">판단 장치가 늘어난 과정</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <LayerTimeline layers={r.layers} />
            <Bullets small items={["점 1개 = 매매 판단을 바꾼 PR 1개 (마우스를 올리면 내용 표시)", "2025: 진입·매도 프롬프트 수정 위주 → 2026.4~7: 국면·비중·재진입·측정 장치 분리"]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">검증한 아이디어와 결과</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              {verdicts.map((v) => (
                <span key={v} className="inline-flex items-center gap-1.5">
                  <i className={`h-2.5 w-2.5 rounded-sm ${VERDICT_COLOR[v]}`} />{v} {r.studies.filter((s) => s.verdict === v).length}건
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-1">
              {studies.map((s, i) => (
                <span key={i} title={`${s.day.slice(5)} · ${s.verdict} · ${s.question}`} className={`h-5 w-5 rounded ${VERDICT_COLOR[s.verdict]}`} />
              ))}
            </div>
            <Bullets small items={["칸 1개 = 연구 1건 (docs/RESEARCH_LESSONS_ko.md, 10/2~10/14)", "대부분 \"그럴듯하지만 데이터로는 아님\""]} />
          </CardContent>
        </Card>
      </section>

      {/* ④ Speed */}
      <section className="space-y-4">
        <SectionTitle no="④">운영 속도와 규모</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <Tile label="머지된 PR" value={fmt(r.summary.merged)} sub={`${r.summary.first_merge} 첫 머지`} />
          <Tile label="최근 100개 PR 기간" value={`${r.summary.last100_days}일`} sub={`첫 100개 ${r.summary.first100_days}일`} />
          <Tile label="최근 4주 주당 머지" value={`${r.summary.recent4w_per_week}`} sub={`전체 평균 ${r.summary.overall_per_week}`} />
          <Tile label="코드 줄 수" value={fmt(r.loc_growth.code_now)} sub={`2025.8 대비 ${r.loc_growth.mult_total}배 · 빈 줄·주석 제외`} />
          <Tile label="테스트 함수" value={fmt(r.complexity.test_functions)} sub={`테스트 코드 ${fmt(r.loc_growth.tests_now)}줄`} />
          <Tile label="정기 작업(cron)" value={`${r.complexity.cron_jobs}`} sub={`서버 ${r.complexity.servers}대 · DB 테이블 ${r.complexity.db_tables}개`} />
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <Card>
            <CardHeader><CardTitle className="text-base">누적 머지 PR</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={r.cumulative} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(2, 7).replace("-", ".")} minTickGap={40} fontSize={11} />
                  <YAxis fontSize={11} width={48} />
                  <Tooltip formatter={(v: number) => [`${v}개`, "누적 머지"]} />
                  <Line type="stepAfter" dataKey="total" stroke={PRISM_COLOR} dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
              <Bullets small items={["2026.6부터 기울기 상승"]} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">영역별 코드 줄 수 (월말)</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={r.loc} margin={{ top: 8, right: 12, left: -4, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="as_of" tickFormatter={(d: string) => d.slice(2, 7).replace("-", ".")} minTickGap={30} fontSize={11} />
                  <YAxis tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} fontSize={11} width={44} />
                  <Tooltip formatter={(v: number, name: string) => [`${v.toLocaleString("ko-KR")}줄`, name]} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {LOC_KEYS.map(([key, label, color]) => (
                    <Area key={key} type="monotone" dataKey={key} name={label} stackId="loc" stroke={color} fill={color} fillOpacity={0.75} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
              <Bullets small items={[`2026.5 이후 ${r.loc_growth.mult_since_may}배 · 테스트가 최대 영역`]} />
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Basis */}
      <section className="space-y-2 border-t pt-6">
        <h3 className="text-sm font-semibold">기준</h3>
        <Bullets small items={[
          "수익률: 10칸 모의 포트폴리오, 판 종목 손익의 합(실현 기준, 단리) · 실계좌 금액 아님",
          `시작일 이후 누적은 KOSPI(${pct(k.kospi_since_pct, true)})가 PRISM 한국(${pct(k.kr_ret_pct, true)})보다 높음 · 이 보고서의 초점은 낙폭 관리`,
          "코드 줄 수: GitHub main 브랜치의 .py·.ts·.tsx·.js·.sh, 빈 줄·주석 제외",
          "판단 장치 목록·위기 요약은 PR 기록을 읽고 선별 · 연구 결과 3건은 직접 판정",
          "출처: GitHub PR·git 기록, PRISM 대시보드 데이터, yfinance 지수, 운영 서버 읽기 전용 조회",
        ]} />
      </section>
    </div>
  )
}
