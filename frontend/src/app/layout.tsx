import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Providers } from "@/lib/providers";

export const metadata: Metadata = {
  title: { default: "Parallax — Trading Analysis", template: "%s · Parallax" },
  description: "Evidence-based market analysis: multi-timeframe setups, transparent scoring, backtesting and paper trading.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f5f7" },
    { media: "(prefers-color-scheme: dark)", color: "#090c11" },
  ],
};

// Applies the saved theme before first paint (no light/dark flash).
const themeScript = `(function(){try{var p=localStorage.getItem('px-theme')||'system';var d=p==='dark'||(p==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);if(d)document.documentElement.classList.add('dark');}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
