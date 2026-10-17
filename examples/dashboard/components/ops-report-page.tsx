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
import { useLanguage } from "@/components/language-provider"

// Static snapshot built from GitHub/git history, the PRISM dashboard data and yfinance
// (see "Basis" at the bottom of the page). Refresh by replacing public/ops_report.json.
interface DrawdownRow { day: string; prism: number; idx_a: number | null; idx_b: number | null }
interface Crisis {
  n: number; kind: "rally" | "drop"; start: string; end: string; index: string
  index_drop_pct: number; index_chg_pct: number | null; worst_day_pct: number; merged: number
  prism_change: number | null
  title: string; hit: string; response: string; title_en?: string; hit_en?: string; response_en?: string
}
interface Layer { lane: string; day: string; label: string; label_en?: string; pr: number }
interface Study { day: string; question: string; question_en?: string; verdict: string }
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

const fmt = (v: number | string | undefined) => (typeof v === "number" ? v.toLocaleString("en-US") : v ?? "—")
const pct = (v: number | string | undefined | null, signed = false) =>
  typeof v === "number" ? `${signed && v > 0 ? "+" : ""}${v}%` : "—"
const ym = (d: string) => d.slice(2, 7).replace("-", ".")

const PRISM_COLOR = "#2563eb"
const IDX_A_COLOR = "#94a3b8"
const IDX_B_COLOR = "#f59e0b"
const LANES: [string, string, string, string][] = [
  ["entry", "진입·스크리닝", "Entry & screening", "#2563eb"],
  ["size", "비중", "Position size", "#7c3aed"],
  ["exit", "매도·손절", "Exit & stops", "#dc2626"],
  ["reentry", "재진입", "Re-entry", "#0891b2"],
  ["regime", "시장 국면", "Market regime", "#d97706"],
  ["measure", "데이터·측정·검증", "Data & verification", "#16a34a"],
]
const LOC_KEYS: [keyof LocRow, string, string, string][] = [
  ["core", "한국·공통 엔진", "KR & core engine", "#2563eb"],
  ["us", "미국", "US", "#0891b2"],
  ["btc", "BTC", "BTC", "#d97706"],
  ["web_ops", "웹·봇·운영", "Web, bots & ops", "#7c3aed"],
  ["tools", "도구·연구", "Tools & research", "#db2777"],
  ["tests", "테스트", "Tests", "#16a34a"],
]
const VERDICTS: [string, string, string][] = [
  ["바꿈", "Changed a rule", "bg-emerald-500"],
  ["버림·현행 유지", "Dropped / kept current", "bg-slate-400 dark:bg-slate-600"],
  ["보류·참고", "On hold", "bg-amber-400"],
]

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
        <XAxis dataKey="day" tickFormatter={ym} minTickGap={40} fontSize={11} />
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

function LayerTimeline({ layers, en }: { layers: Layer[]; en: boolean }) {
  const t0 = Date.parse("2025-08-01")
  const t1 = Math.max(...layers.map((l) => Date.parse(l.day)))
  const x = (d: string) => (100 * (Date.parse(d) - t0)) / (t1 - t0)
  const ticks = ["2025-09-01", "2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"]
  return (
    <div className="space-y-2">
      {LANES.map(([key, ko, enLabel, color]) => (
        <div key={key} className="flex items-center gap-3">
          <span className="w-24 sm:w-32 shrink-0 text-xs text-muted-foreground text-right">{en ? enLabel : ko}</span>
          <div className="relative h-6 flex-1 border-b border-border/60">
            {layers.filter((l) => l.lane === key).map((l) => (
              <span
                key={`${l.pr}-${l.label}`}
                title={`${l.day} · #${l.pr} · ${en ? l.label_en ?? l.label : l.label}`}
                className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-background"
                style={{ left: `${x(l.day)}%`, background: color }}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="flex gap-3">
        <span className="w-24 sm:w-32 shrink-0" />
        <div className="relative h-4 flex-1 text-[10px] text-muted-foreground">
          {ticks.map((d) => (
            <span key={d} className="absolute -translate-x-1/2" style={{ left: `${x(d)}%` }}>{ym(d)}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

export function OpsReportPage() {
  const { language } = useLanguage()
  const en = language === "en"
  const L = (ko: string, enText: string) => (en ? enText : ko)
  const [r, setR] = useState<OpsReport | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch("/ops_report.json")
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then(setR)
      .catch(() => setError(true))
  }, [])

  if (error) return <p className="text-center text-muted-foreground py-20">{L("운영 보고서를 불러오지 못했습니다.", "Could not load the ops report.")}</p>
  if (!r) return <div className="flex justify-center py-20"><div className="w-10 h-10 border-4 border-primary border-t-transparent rounded-full animate-spin" /></div>

  const k = r.risk, u = r.us_split, s = r.study_summary
  const krHold = r.positions.find((p) => p.market === "KR")?.holdings ?? 0
  const order = VERDICTS.map((v) => v[0])
  const studies = [...r.studies].sort((a, b) => order.indexOf(a.verdict) - order.indexOf(b.verdict) || a.day.localeCompare(b.day))
  const verdictLabel = (v: string) => { const f = VERDICTS.find((x) => x[0] === v); return f ? (en ? f[1] : f[0]) : v }

  return (
    <div className="mx-auto max-w-6xl space-y-10">
      {/* Header */}
      <header className="space-y-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{L("완성된 자동매매는 없습니다", "There is no finished trading bot")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {L(
              `PRISM-INSIGHT 운영 보고서 · 한국 2025-09-29 / 미국 2026-01-29 시작 · 수익률 ${r.as_of.returns}, 코드 ${r.as_of.code} 기준`,
              `PRISM-INSIGHT ops report · KR since 2025-09-29 / US since 2026-01-29 · returns as of ${r.as_of.returns}, code as of ${r.as_of.code}`,
            )}
          </p>
        </div>
        <Card className="py-5">
          <CardContent>
            <Bullets items={en ? [
              <><b>Crash defense</b> — KOSPI <b>{pct(k.kospi_dd_pct)}</b> from peak, PRISM KR <b>{pct(k.kr_dd_pct)}p</b>, <b>{krHold}</b> holdings (100% cash)</>,
              <><b>Every crisis fixed by a rule</b> — {r.crises.length} crises, <b>{fmt(r.summary.merged)}</b> merged PRs, the latest 100 in <b>{r.summary.last100_days} days</b></>,
              <><b>Verify before changing</b> — of <b>{s.total}</b> studies only <b>{s.changed}</b> changed a rule; the rest were rejected or put on hold</>,
              <><b>Weak spot, shown openly</b> — US <b>{pct(k.us_ret_pct, true)}</b> vs S&amp;P 500 <b>{pct(k.sp_since_pct, true)}</b>; October micro-split, re-entry and leader-hold rules are the response (not yet proven)</>,
            ] : [
              <><b>폭락장 방어</b> — KOSPI 고점 대비 <b>{pct(k.kospi_dd_pct)}</b>, PRISM 한국 <b>{pct(k.kr_dd_pct)}p</b>, 보유 <b>{krHold}</b>종목(현금 100%)</>,
              <><b>위기마다 규칙 보완</b> — 위기 {r.crises.length}회 대응, 머지 PR <b>{fmt(r.summary.merged)}</b>개, 최근 100개는 <b>{r.summary.last100_days}일</b> 만에 머지</>,
              <><b>바꾸기 전에 검증</b> — 연구 <b>{s.total}</b>건 중 규칙 변경 <b>{s.changed}</b>건, 나머지는 기각·보류</>,
              <><b>약점 공개</b> — 미국 <b>{pct(k.us_ret_pct, true)}</b> vs S&amp;P 500 <b>{pct(k.sp_since_pct, true)}</b>, 10월 초분할·재진입·주도주 보유로 대응 중(성과 미입증)</>,
            ]} />
          </CardContent>
        </Card>
      </header>

      {/* ① Risk */}
      <section className="space-y-4">
        <SectionTitle no="①">{L("폭락장 방어", "Crash defense")}</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Tile label={L("KOSPI 고점 대비", "KOSPI from peak")} value={pct(k.kospi_dd_pct)} sub={`${L("고점", "Peak")} ${k.kospi_peak_day}`} tone="bad" />
          <Tile label={L("KOSDAQ 고점 대비", "KOSDAQ from peak")} value={pct(k.kosdaq_dd_pct)} sub={`${L("고점", "Peak")} ${k.kosdaq_peak_day}`} tone="bad" />
          <Tile label={L("PRISM 한국 고점 대비", "PRISM KR from peak")} value={`${pct(k.kr_dd_pct)}p`} sub={`${L("누적", "Cumulative")} ${pct(k.kr_peak_ret_pct, true)} → ${pct(k.kr_ret_pct, true)}`} />
          <Tile label={L("한국 보유 종목", "KR holdings")} value={`${krHold}`} sub={L("조정장 판정 → 신규 매수 중단", "Correction regime → no new buys")} tone="good" />
          <Tile label={L("PRISM 미국 시작 이후", "PRISM US since start")} value={pct(k.us_ret_pct, true)} sub={`S&P 500 ${pct(k.sp_since_pct, true)} · NASDAQ ${pct(k.nq_since_pct, true)}`} />
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <Card>
            <CardHeader><CardTitle className="text-base">{L("한국: 고점 대비 낙폭", "Korea: drawdown from peak")}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <DrawdownChart rows={r.drawdown.KR} a="KOSPI" b="KOSDAQ" me={L("PRISM 한국", "PRISM KR")} />
              <Bullets small items={en
                ? ["PRISM in %p of starting capital (same as the dashboard MDD), indices in % from peak", "Closer to 0 = smaller drawdown"]
                : ["PRISM은 원금 대비 %p(대시보드 MDD와 같은 방식), 지수는 고점 대비 %", "0에 가까울수록 덜 빠짐"]} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">{L("미국: 고점 대비 낙폭", "US: drawdown from peak")}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <DrawdownChart rows={r.drawdown.US} a="S&P 500" b="NASDAQ" me={L("PRISM 미국", "PRISM US")} />
              <Bullets small items={en ? [
                <b key="h" className="text-foreground">Why it fell more than the index</b>,
                `Index went sideways after the peak (${u.peak_day}): S&P 500 ${pct(u.sp_after_pct, true)}, NASDAQ ${pct(u.nq_after_pct, true)}`,
                `Win rate ${u.win_after_pct}% (before the peak ${u.win_before_pct}%)`,
                `Avg win +${u.avgw_after_pct}% (before +${u.avgw_before_pct}%) · avg loss ${u.avgl_after_pct}% (before ${u.avgl_before_pct}%)`,
                `Trades ≥ +10%: ${u.big_after} (before ${u.big_before}) · losses ≤ −3%: ${u.stop_after} of ${u.n_after}`,
                "Diagnosis: breakout buys stopped out again and again in a directionless market, few big winners (entry vs exit share not separated)",
                "Response: October micro-split (small first buy) and re-entry",
              ] : [
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
        <SectionTitle no="②">{L(`위기 ${r.crises.length}회 · 문제와 대응`, `${r.crises.length} crises · problem and fix`)}</SectionTitle>
        <Bullets small items={en ? [
          "Bull-market crises = could not buy or got hit hard / bear-market crises = surviving",
          "Down periods: in-period peak→trough and worst day · up periods: whole-period change · PR: merged in the period · PRISM: change in cumulative return (%p)",
        ] : [
          "상승장 위기 = 못 사거나 크게 깨짐 / 하락장 위기 = 버티기",
          "하락 구간: 구간 내 고점→저점·최악의 하루 · 상승 구간: 구간 전체 등락 · PR: 구간 내 머지 수 · PRISM: 누적 수익 변화(%p)",
        ]} />
        <ol className="grid md:grid-cols-2 gap-3">
          {r.crises.map((c) => (
            <li key={c.n} className="rounded-xl border bg-card p-4 space-y-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <b className="text-sm">{en ? c.title_en ?? c.title : c.title}</b>
                <span className="text-xs text-muted-foreground tabular-nums">{c.start.slice(2).replaceAll("-", ".")} ~ {c.end.slice(5).replace("-", ".")}</span>
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs tabular-nums">
                {c.kind === "rally" ? (
                  <span className="rounded-full border px-2 text-emerald-600 dark:text-emerald-400">{c.index} {pct(c.index_chg_pct, true)} {L("상승장", "rally")}</span>
                ) : (
                  <>
                    <span className="rounded-full border px-2 text-red-600 dark:text-red-400">{c.index} {c.index_drop_pct}%</span>
                    <span className="rounded-full border px-2 text-muted-foreground">{L("최악의 하루", "Worst day")} {c.worst_day_pct}%</span>
                  </>
                )}
                <span className="rounded-full border px-2 text-muted-foreground">PR {c.merged}</span>
                {c.prism_change != null && (
                  <span className="rounded-full border px-2 text-muted-foreground">PRISM {c.prism_change > 0 ? "+" : ""}{c.prism_change}%p</span>
                )}
              </div>
              <p className="text-xs leading-relaxed"><b className="text-red-600 dark:text-red-400 mr-1.5">{L("문제", "Problem")}</b>{en ? c.hit_en ?? c.hit : c.hit}</p>
              <p className="text-xs leading-relaxed"><b className="text-emerald-600 dark:text-emerald-400 mr-1.5">{L("대응", "Fix")}</b>{en ? c.response_en ?? c.response : c.response}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ③ Rigor */}
      <section className="space-y-4">
        <SectionTitle no="③">{L("검증 체계", "How changes are verified")}</SectionTitle>
        <Bullets items={en ? [
          <><b>{r.layer_summary.total}</b> decision mechanisms across 6 areas: entry, size, exit, re-entry, market regime, data</>,
          <>Of <b>{s.total}</b> studies: <b>{s.changed}</b> changed a rule · <b>{s.dropped}</b> rejected or kept current · <b>{s.held}</b> on hold</>,
          "Process: replay on past data → small live trial → scale up; a person approves every switch to LIVE",
        ] : [
          <>판단 장치 <b>{r.layer_summary.total}</b>개 · 진입·비중·매도·재진입·시장 국면·데이터 6개 영역으로 분리</>,
          <>연구 <b>{s.total}</b>건 중 규칙 변경 <b>{s.changed}</b>건 · 기각·현행 유지 <b>{s.dropped}</b>건 · 보류 <b>{s.held}</b>건</>,
          "원칙: 과거 데이터 재현 → 소규모 실전 → 확대, LIVE 전환은 사람이 승인",
        ]} />
        <Card>
          <CardHeader><CardTitle className="text-base">{L("판단 장치가 늘어난 과정", "How the decision mechanisms grew")}</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <LayerTimeline layers={r.layers} en={en} />
            <Bullets small items={en
              ? ["One dot = one PR that changed a trading decision (hover for details)", "2025: mostly entry/exit prompt fixes → Apr–Jul 2026: separate regime, size, re-entry and measurement mechanisms"]
              : ["점 1개 = 매매 판단을 바꾼 PR 1개 (마우스를 올리면 내용 표시)", "2025: 진입·매도 프롬프트 수정 위주 → 2026.4~7: 국면·비중·재진입·측정 장치 분리"]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">{L("검증한 아이디어와 결과", "Ideas tested and their outcome")}</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              {VERDICTS.map(([v, , color]) => (
                <span key={v} className="inline-flex items-center gap-1.5">
                  <i className={`h-2.5 w-2.5 rounded-sm ${color}`} />{verdictLabel(v)} {r.studies.filter((x) => x.verdict === v).length}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-1">
              {studies.map((x, i) => (
                <span
                  key={i}
                  title={`${x.day.slice(5)} · ${verdictLabel(x.verdict)} · ${en ? x.question_en ?? x.question : x.question}`}
                  className={`h-5 w-5 rounded ${VERDICTS.find((v) => v[0] === x.verdict)?.[2] ?? ""}`}
                />
              ))}
            </div>
            <Bullets small items={en
              ? ["One square = one study (docs/RESEARCH_LESSONS_ko.md, Oct 2–14)", "Most were \"plausible, but not supported by the data\""]
              : ["칸 1개 = 연구 1건 (docs/RESEARCH_LESSONS_ko.md, 10/2~10/14)", "대부분 \"그럴듯하지만 데이터로는 아님\""]} />
          </CardContent>
        </Card>
      </section>

      {/* ④ Speed */}
      <section className="space-y-4">
        <SectionTitle no="④">{L("운영 속도와 규모", "Pace and scale")}</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <Tile label={L("머지된 PR", "Merged PRs")} value={fmt(r.summary.merged)} sub={`${L("첫 머지", "First merge")} ${r.summary.first_merge}`} />
          <Tile label={L("최근 100개 PR 기간", "Latest 100 PRs took")} value={L(`${r.summary.last100_days}일`, `${r.summary.last100_days} days`)} sub={L(`첫 100개 ${r.summary.first100_days}일`, `First 100: ${r.summary.first100_days} days`)} />
          <Tile label={L("최근 4주 주당 머지", "Merges/week, last 4 weeks")} value={`${r.summary.recent4w_per_week}`} sub={`${L("전체 평균", "Overall")} ${r.summary.overall_per_week}`} />
          <Tile label={L("코드 줄 수", "Lines of code")} value={fmt(r.loc_growth.code_now)} sub={L(`2025.8 대비 ${r.loc_growth.mult_total}배 · 빈 줄·주석 제외`, `${r.loc_growth.mult_total}× since Aug 2025 · no blanks/comments`)} />
          <Tile label={L("테스트 함수", "Test functions")} value={fmt(r.complexity.test_functions)} sub={L(`테스트 코드 ${fmt(r.loc_growth.tests_now)}줄`, `${fmt(r.loc_growth.tests_now)} lines of tests`)} />
          <Tile label={L("정기 작업(cron)", "Scheduled jobs (cron)")} value={`${r.complexity.cron_jobs}`} sub={L(`서버 ${r.complexity.servers}대 · DB 테이블 ${r.complexity.db_tables}개`, `${r.complexity.servers} servers · ${r.complexity.db_tables} DB tables`)} />
        </div>
        <div className="grid md:grid-cols-2 gap-4">
          <Card>
            <CardHeader><CardTitle className="text-base">{L("누적 머지 PR", "Cumulative merged PRs")}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={r.cumulative} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="day" tickFormatter={ym} minTickGap={40} fontSize={11} />
                  <YAxis fontSize={11} width={48} />
                  <Tooltip formatter={(v: number) => [v, L("누적 머지", "Merged")]} />
                  <Line type="stepAfter" dataKey="total" stroke={PRISM_COLOR} dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
              <Bullets small items={[L("2026.6부터 기울기 상승", "Slope steepens from June 2026")]} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">{L("영역별 코드 줄 수 (월말)", "Lines of code by area (month end)")}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={r.loc} margin={{ top: 8, right: 12, left: -4, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="as_of" tickFormatter={ym} minTickGap={30} fontSize={11} />
                  <YAxis tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} fontSize={11} width={44} />
                  <Tooltip formatter={(v: number, name: string) => [v.toLocaleString("en-US"), name]} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {LOC_KEYS.map(([key, ko, enLabel, color]) => (
                    <Area key={key} type="monotone" dataKey={key} name={en ? enLabel : ko} stackId="loc" stroke={color} fill={color} fillOpacity={0.75} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
              <Bullets small items={[L(`2026.5 이후 ${r.loc_growth.mult_since_may}배 · 테스트가 최대 영역`, `${r.loc_growth.mult_since_may}× since May 2026 · tests are the largest area`)]} />
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Basis */}
      <section className="space-y-2 border-t pt-6">
        <h3 className="text-sm font-semibold">{L("기준", "Basis")}</h3>
        <Bullets small items={en ? [
          "Returns: 10-slot simulated portfolio, sum of closed-trade P&L (realized, simple) · not real account money",
          `Cumulative since start, KOSPI (${pct(k.kospi_since_pct, true)}) is ahead of PRISM KR (${pct(k.kr_ret_pct, true)}) · this report is about drawdown control`,
          "Lines of code: GitHub main branch .py·.ts·.tsx·.js·.sh, blank and comment lines excluded",
          "Decision-mechanism list and crisis summaries were curated from PR history · 3 study verdicts were judged manually",
          "Sources: GitHub PRs and git history, PRISM dashboard data, yfinance indices, read-only server queries",
        ] : [
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
