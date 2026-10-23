import type { Metadata } from "next";
import { Archivo, Azeret_Mono } from "next/font/google";
import { Providers } from "@/lib/session";
import "./globals.css";

// Archivo at normal width for all reading and headings; Azeret Mono is for
// measurements, addresses and times.
const ui = Archivo({ variable: "--font-ui", subsets: ["latin"], display: "swap" });
const data = Azeret_Mono({ variable: "--font-data", subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: { default: "Fabric Warden", template: "%s · Fabric Warden" },
  description: "Self-hosted network operations for UniFi.",
  icons: { icon: "/icon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-density="comfortable" className={`${ui.variable} ${data.variable}`} suppressHydrationWarning>
      <body className="min-h-dvh bg-ground text-ink">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
