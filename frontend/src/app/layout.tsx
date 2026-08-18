import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "./app-shell";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Muldro",
  description: "Personal AI Operating System",
};

// Inline script to set theme before paint — prevents FOUC.
// Content is a hardcoded string literal (no user input), safe for dangerouslySetInnerHTML.
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('muldro_theme');var r=t==='light'?'light':t==='dark'?'dark':window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';document.documentElement.setAttribute('data-theme',r)}catch(e){}})()`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-theme="dark"
      className={`${geistSans.variable} ${geistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
      </head>
      <body
        className="antialiased bg-surface-0 text-t-primary min-h-screen"
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
