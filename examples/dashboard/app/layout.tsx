import type React from "react"
import type { Metadata } from "next"
import { Geist, Geist_Mono, Hedvig_Letters_Serif } from "next/font/google"
import "./globals.css"
import { ThemeProvider } from "@/components/theme-provider"
import { LanguageProvider } from "@/components/language-provider"

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" })
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" })
// Serif for headline figures only (KPI values), body stays Geist
const hedvig = Hedvig_Letters_Serif({ subsets: ["latin"], variable: "--font-hedvig" })

export const metadata: Metadata = {
  title: "Prism Insight Dashboard",
  description: "AI Agent-based Korean Stock Analysis & Trading System",
  generator: "v0.app",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className={`${geist.variable} ${geistMono.variable} ${hedvig.variable} font-sans antialiased`}>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
          <LanguageProvider>
            {children}
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
