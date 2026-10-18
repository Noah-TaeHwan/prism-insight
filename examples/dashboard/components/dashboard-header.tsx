"use client"

import { Moon, Sun, TrendingUp, Github, Send, Languages } from "lucide-react"
import { useTheme } from "next-themes"
import { useLanguage } from "@/components/language-provider"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Market } from "@/types/dashboard"

interface DashboardHeaderProps {
  activeTab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "ops-report" | "stance"
  onTabChange: (tab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "ops-report" | "stance") => void
  lastUpdated?: string
  market?: Market
  onMarketChange?: (market: Market) => void
}

export function DashboardHeader({ activeTab, onTabChange, lastUpdated, market = "KR", onMarketChange }: DashboardHeaderProps) {
  const { theme, setTheme } = useTheme()
  const { language, setLanguage, t } = useLanguage()

  const formatLastUpdated = () => {
    if (!lastUpdated) return t("header.realtimeUpdate")

    try {
      const date = new Date(lastUpdated)
      if (isNaN(date.getTime())) return t("header.realtimeUpdate")
      return date.toLocaleString(language === "ko" ? "ko-KR" : "en-US", {
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return t("header.realtimeUpdate")
    }
  }

  type Tab = DashboardHeaderProps["activeTab"]
  const tabs: { id: Tab; label: string }[] = [
    { id: "dashboard", label: t("header.dashboard") },
    { id: "ai-decisions", label: t("header.aiDecisions") },
    { id: "trading", label: t("header.trading") },
    { id: "watchlist", label: t("header.watchlist") },
    { id: "insights", label: t("header.insights") },
    { id: "ops-report", label: t("header.opsReport") },
    { id: "stance", label: "Stance" },
  ]

  const markets: { id: Market; label: string }[] = [
    { id: "KR", label: language === "ko" ? "한국" : "Korea" },
    { id: "US", label: language === "ko" ? "미국" : "US" },
  ]

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      {/* Platinum Sponsor Bar */}
      <div className="hidden sm:block border-b border-border">
        <div className="container mx-auto px-4 max-w-[1600px]">
          <div className="flex items-center justify-center py-1 gap-3 text-[11px] text-muted-foreground">
            <span className="uppercase tracking-[0.14em]">Platinum Sponsor</span>
            <a
              href="https://wrks.ai/en"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-foreground/80 hover:text-foreground transition-colors"
            >
              <img src="/wrks_ai_logo.png" alt="WrksAI" className="h-4 w-auto" />
              <span className="font-medium">WrksAI</span>
            </a>
          </div>
        </div>
      </div>

      <div className="container mx-auto px-4 max-w-[1600px]">
        <div className="flex h-14 items-center gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="flex items-center justify-center w-7 h-7 rounded-md bg-foreground text-background shrink-0">
              <TrendingUp className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-[15px] font-semibold tracking-tight">PRISM-INSIGHT</h1>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="hidden sm:inline px-1.5 py-px text-[10px] font-medium rounded border border-border text-muted-foreground cursor-help">
                        {t("header.openSource")}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">{t("header.tooltip.openSource")}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <p className="text-[11px] text-muted-foreground truncate">
                {t("header.updated")} <span className="font-mono tabular-nums">{formatLastUpdated()}</span>
              </p>
            </div>
          </div>

          {/* Market Selector */}
          {onMarketChange && (
            <div className="hidden sm:flex items-center rounded-lg border border-border p-0.5 gap-0.5">
              {markets.map((m) => (
                <button
                  key={m.id}
                  onClick={() => onMarketChange(m.id)}
                  aria-pressed={market === m.id}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-sm transition-colors ${
                    market === m.id
                      ? "bg-secondary text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m.label}
                  {market === m.id && <span className="text-[10px] text-muted-foreground font-normal">S2</span>}
                </button>
              ))}
            </div>
          )}

          {/* Utility Buttons */}
          <div className="ml-auto flex items-center gap-0.5">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" asChild className="h-8 w-8 text-muted-foreground hover:text-foreground">
                    <a href="https://github.com/dragon1086/prism-insight" target="_blank" rel="noopener noreferrer" aria-label="GitHub Repository">
                      <Github className="h-4 w-4" />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{t("header.tooltip.github")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" asChild className="h-8 w-8 text-muted-foreground hover:text-foreground">
                    <a href="https://t.me/stock_ai_agent" target="_blank" rel="noopener noreferrer" aria-label="Telegram Channel">
                      <Send className="h-4 w-4" />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{t("header.tooltip.telegram")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <button
              onClick={() => setLanguage(language === "ko" ? "en" : "ko")}
              className="flex items-center gap-1 h-8 px-2 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              aria-label="Language"
            >
              <Languages className="h-3.5 w-3.5" />
              <span className={language === "en" ? "text-foreground font-medium" : ""}>EN</span>
              <span className="opacity-40">/</span>
              <span className={language === "ko" ? "text-foreground font-medium" : ""}>한</span>
            </button>

            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
            >
              <Sun className="h-4 w-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
              <span className="sr-only">{t("header.tooltip.theme")}</span>
            </Button>
          </div>
        </div>

        {/* Mobile Market Selector */}
        {onMarketChange && (
          <div className="sm:hidden flex items-center gap-2 pb-2">
            <div className="flex flex-1 rounded-lg border border-border p-0.5 gap-0.5">
              {markets.map((m) => (
                <button
                  key={m.id}
                  onClick={() => onMarketChange(m.id)}
                  aria-pressed={market === m.id}
                  className={`flex-1 px-3 py-1.5 rounded-md text-sm transition-colors ${
                    market === m.id ? "bg-secondary text-foreground font-medium" : "text-muted-foreground"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <a
              href="https://wrks.ai/en"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground"
            >
              Sponsor
              <img src="/wrks_ai_logo.png" alt="WrksAI" className="h-4 w-auto" />
            </a>
          </div>
        )}

        {/* Navigation Tabs */}
        <nav className="-mb-px flex items-center gap-1 overflow-x-auto [scrollbar-width:none]" aria-label="Tabs">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              aria-current={activeTab === tab.id ? "page" : undefined}
              className={`whitespace-nowrap px-3 py-2.5 text-sm border-b-2 transition-colors ${
                activeTab === tab.id
                  ? "border-foreground text-foreground font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  )
}
