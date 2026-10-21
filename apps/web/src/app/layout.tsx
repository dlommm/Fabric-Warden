import type { Metadata } from "next";
import { Atkinson_Hyperlegible_Mono, Atkinson_Hyperlegible_Next } from "next/font/google";
import { Providers } from "@/lib/session";
import "./globals.css";

const ui = Atkinson_Hyperlegible_Next({ variable: "--font-ui", subsets: ["latin"], display: "swap" });
const data = Atkinson_Hyperlegible_Mono({ variable: "--font-data", subsets: ["latin"], display: "swap" });

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
